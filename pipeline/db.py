"""SQLite 知识库。数据库文件在 data/knowledge.db，随 repo 提交（零服务器架构）。"""
import json
import os
import sqlite3

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
    score REAL, score_detail TEXT, status TEXT, notes TEXT, extra TEXT,
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
        """轻量迁移：老库缺 categories 列时补上，并用 category 回填。
        数据不能因为加字段就重跑一遍——已有的运行记录是项目的资产。"""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(items)")}
        if "categories" not in cols:
            self.conn.execute("ALTER TABLE items ADD COLUMN categories TEXT")
            self.conn.execute(
                "UPDATE items SET categories = json_array(category) "
                "WHERE category IS NOT NULL AND category != ''")
            self.conn.commit()

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


def _row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    for f in _JSON_FIELDS:
        try:
            d[f] = json.loads(d.get(f) or "null")
        except (json.JSONDecodeError, TypeError):
            pass
    return d
