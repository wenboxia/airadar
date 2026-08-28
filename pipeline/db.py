"""SQLite 知识库。数据库文件在 data/knowledge.db，随 repo 提交（零服务器架构）。"""
import json
import os
import sqlite3
from datetime import datetime, timezone

from .config import ROOT
from .models import Item

# 支持环境变量覆盖：测试时用副本，避免污染真实知识库
DB_PATH = os.environ.get("AIRADAR_DB_PATH") or os.path.join(ROOT, "data", "knowledge.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    url TEXT, title TEXT, source TEXT, tier TEXT,
    published_at TEXT, fetched_at TEXT,
    content TEXT, summary_short TEXT, summary_long TEXT,
    key_points TEXT, topics TEXT, category TEXT, categories TEXT, horizon TEXT,
    score REAL, score_detail TEXT, status TEXT, auto_status TEXT, notes TEXT, extra TEXT,
    run_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT, finished_at TEXT,
    stats TEXT, errors TEXT
);
"""

_JSON_FIELDS = ("key_points", "topics", "categories", "score_detail", "notes", "extra")


class DB:
    def __init__(self, path: str = DB_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        """轻量迁移：加字段不重跑 pipeline——已有的运行记录是项目的资产。"""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(items)")}
        if "categories" not in cols:
            self.conn.execute("ALTER TABLE items ADD COLUMN categories TEXT")
            self.conn.execute(
                "UPDATE items SET categories = json_array(category) "
                "WHERE category IS NOT NULL AND category != ''")
            self.conn.commit()
        if "auto_status" not in cols:
            self._migrate_auto_status()

    def _migrate_auto_status(self):
        """回填 auto_status（系统自主判断），区分它与被人工改写的 status。

        怎么反推被 HITL 改写过的条目：**只有 review 状态的条目才会进审批 issue**，
        所以凡是在 feedback.jsonl 里出现过的 item_id，它当初的 auto_status 必然是 review。
        其余条目没被人碰过，status 就等于 auto_status。
        """
        import json as _json
        self.conn.execute("ALTER TABLE items ADD COLUMN auto_status TEXT")
        self.conn.execute("UPDATE items SET auto_status = status")

        fb = os.path.join(ROOT, "data", "feedback.jsonl")
        touched = set()
        if os.path.exists(fb):
            with open(fb, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        touched.add(_json.loads(line)["item_id"])
        for item_id in touched:
            self.conn.execute(
                "UPDATE items SET auto_status='review' WHERE id=?", (item_id,))
        self.conn.commit()
        if touched:
            print(f"  [migrate] 已从审批记录反推 {len(touched)} 条的原始判断（auto_status=review）")

    def existing_ids(self) -> set:
        return {r["id"] for r in self.conn.execute("SELECT id FROM items")}

    def upsert_items(self, items: list, run_id: str):
        for it in items:
            d = it.to_dict()
            d["run_id"] = run_id
            for f in _JSON_FIELDS:
                d[f] = json.dumps(d[f], ensure_ascii=False)
            cols = ",".join(d.keys())
            marks = ",".join("?" * len(d))
            self.conn.execute(
                f"INSERT OR REPLACE INTO items ({cols}) VALUES ({marks})",
                list(d.values()))
        self.conn.commit()

    def save_run(self, run_id: str, started_at: str, finished_at: str,
                 stats: dict, errors: list):
        self.conn.execute(
            "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?)",
            (run_id, started_at, finished_at,
             json.dumps(stats, ensure_ascii=False),
             json.dumps(errors, ensure_ascii=False)))
        self.conn.commit()

    def recent_items(self, days: int = 7, statuses=("published",)) -> list:
        q = ("SELECT * FROM items WHERE status IN (%s) "
             "AND published_at >= datetime('now', ?) "
             "ORDER BY score DESC, published_at DESC"
             % ",".join("?" * len(statuses)))
        rows = self.conn.execute(q, (*statuses, f"-{days} days")).fetchall()
        return [_row_to_dict(r) for r in rows]

    def archive_items(self, short_ttl_days: int = 14) -> list:
        """知识库：长期留存的内容。

        过期规则（主人提出，见 decisions.md D30）：**时效类内容会过期，长期价值内容不会**。
        具体地，`horizon='short'` 且**未经人工审批认可**的条目，超过 N 天后退出知识库。
        - 为什么不真删：数据留在库里可审计——"三个月前判死了哪些、判对了吗"要能复盘
          （沿用 D9「discarded 也入库」的原则）
        - 为什么人工认可的不过期：那是主人明确说"这个我要留着"的东西，机器无权代为遗忘
        """
        approved = {r["id"] for r in self.conn.execute(
            "SELECT id FROM items WHERE status='published' AND auto_status='review'")}
        rows = self.conn.execute(
            "SELECT * FROM items WHERE status='published' "
            "ORDER BY published_at DESC, score DESC").fetchall()
        out = []
        for r in rows:
            d = _row_to_dict(r)
            expired = (
                d.get("horizon") == "short"
                and d["id"] not in approved      # 人工认可过的永不过期
                and _older_than(d.get("published_at"), short_ttl_days)
            )
            if not expired:
                out.append(d)
        return out

    def all_runs(self) -> list:
        rows = self.conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["stats"] = json.loads(d["stats"] or "{}")
            out.append(d)
        return out

    def counts(self) -> dict:
        row = self.conn.execute(
            "SELECT status, COUNT(*) n FROM items GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in row}


def _older_than(iso: str, days: int) -> bool:
    if not iso:
        return False
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - d).days > days


def _row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    for f in _JSON_FIELDS:
        try:
            d[f] = json.loads(d.get(f) or "null")
        except (json.JSONDecodeError, TypeError):
            pass
    return d
