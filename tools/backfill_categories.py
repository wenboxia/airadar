"""一次性回填：把历史条目的分类统一到 2026-09-16 的新类目表（D39）。

用法：
  python3 tools/backfill_categories.py --dry-run --limit 5   # 先试 5 条，不写库
  python3 tools/backfill_categories.py                       # 正式回填（可中断，重跑自动续）
  python3 tools/backfill_categories.py --report              # 只出对比表，不调模型

为什么不直接重跑 pipeline（CLAUDE.md：不许靠重跑 pipeline 补数据）：
重跑会新增 run 记录、污染运行统计，还会重新走 triage——而 triage 写 auto_status，
那是 D28 的禁区。这里只借用 classify 的模型调用，其余什么都不碰。

只改 categories / category 两列，**不改 topics 和 horizon**：
- horizon 决定内容过期（db.archive_items），重跑会让历史条目因为模型随机性突然过期或复活
- topics 进话题趋势（memory.py），重跑只会给趋势历史加噪声
这次改版动的是类目表，只有 categories 需要跟着变。

范围和 classify.run 一致：只回填 published / review。discarded 从来不进分类环节。
"""
import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pipeline.config import load_config           # noqa: E402
from pipeline.db import DB                         # noqa: E402
from pipeline.guards import Budget                # noqa: E402
from pipeline.llm import LLMError, build_client    # noqa: E402
from pipeline.models import Context                # noqa: E402
from pipeline.stages import classify               # noqa: E402

MARK = "categories_backfilled_at"
# 旧类目表里不存在、改版才有的类目——只有它们的出现能归因到改版本身
NEW_ONLY = {"安全与防护", "落地案例"}
SCOPE = "status IN ('published','review')"


class _Shim:
    """_classify_one 只读 title / summary_long / content，只写四个分类字段。"""
    def __init__(self, row):
        self.title = row["title"] or ""
        self.summary_long = row["summary_long"] or ""
        self.content = row["content"] or ""
        self.categories, self.category, self.topics, self.horizon = [], "", [], ""


def _checksum(db) -> str:
    """人的判断与系统原判的指纹。回填前后必须一字不差（D28）。"""
    h = hashlib.sha256()
    for r in db.conn.execute("SELECT id, status, auto_status FROM items ORDER BY id"):
        h.update(f"{r['id']}|{r['status']}|{r['auto_status']}\n".encode())
    return h.hexdigest()


def _snapshot(db) -> int:
    """把旧分类存进 categories_v1。只写一次——重跑时绝不能把新分类当成旧的存进去。"""
    cur = db.conn.execute(
        f"UPDATE items SET categories_v1 = categories "
        f"WHERE {SCOPE} AND categories_v1 IS NULL")
    db.conn.commit()
    return cur.rowcount


def _todo(db, limit):
    rows = db.conn.execute(
        f"SELECT id, title, summary_long, content, extra FROM items WHERE {SCOPE} "
        f"ORDER BY published_at").fetchall()
    rows = [r for r in rows if MARK not in json.loads(r["extra"] or "{}")]
    return rows[:limit] if limit else rows


def _report(db):
    rows = db.conn.execute(
        f"SELECT categories_v1, categories FROM items "
        f"WHERE {SCOPE} AND categories_v1 IS NOT NULL").fetchall()
    old_dist, new_dist = Counter(), Counter()
    source_of = Counter()         # 获得新类目的条目，原先挂在哪个旧类目下
    attributable = drift = 0
    for r in rows:
        old = set(json.loads(r["categories_v1"] or "[]"))
        new = set(json.loads(r["categories"] or "[]"))
        old_dist.update(old)
        new_dist.update(new)
        if old == new:
            continue
        # 两桶互斥：拿到了新类目的算改版带来的（同时丢掉「安全与对齐」正是拆分的预期效果，
        # 不能再重复记成漂移）；没拿到新类目却变了的，只能是模型随机性
        gained = new & NEW_ONLY
        if gained:
            attributable += 1
            for o in (old - new) or {"（原类目保留）"}:
                for g in gained:
                    source_of[(o, g)] += 1
        else:
            drift += 1

    n = len(rows)
    changed = attributable + drift
    print(f"\n对比样本：{n} 条（有旧分类快照的 published/review）")
    print(f"分类集合有变化：{changed} 条（{changed * 100 // max(n, 1)}%）")
    print(f"  ├ 获得新类目（可归因于改版）：{attributable} 条")
    print(f"  └ 未获得新类目但变了（模型随机性，不归因）：{drift} 条"
          f"（{drift * 100 // max(n, 1)}%——这是重跑分类的噪声底）")

    print("\n新类目的条目，原先挂在哪（被替换掉的旧类目）：")
    for (o, g), k in source_of.most_common(12):
        print(f"  {o:<12} → {g:<8} {k:>3}")

    print(f"\n{'类目':<10}{'旧':>6}{'新':>6}{'变化':>7}")
    for c in classify.CATEGORIES:
        d = new_dist[c] - old_dist[c]
        flag = "  ← 新增" if c in NEW_ONLY else ""
        print(f"{c:<10}{old_dist[c]:>6}{new_dist[c]:>6}{d:>+7}{flag}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="调模型但不写库")
    ap.add_argument("--report", action="store_true", help="只出对比表")
    args = ap.parse_args()

    db = DB()
    if args.report:
        _report(db)
        return

    cfg = load_config()
    stats = {}
    # pipeline 单次预算是 260 次调用，不够回填用；单独给一个略高于预估的硬上限
    budget = Budget(token_limit=1_200_000, call_limit=560)
    llm = build_client(cfg, budget, stats)
    if not llm.available():
        sys.exit("没有可用的 LLM（检查 .env）。回填必须用模型，不走启发式兜底。")
    ctx = Context(cfg=cfg, llm=llm, db=db, run_id="backfill", stats=stats)

    before = _checksum(db)
    if not args.dry_run:
        print(f"快照旧分类：新写入 {_snapshot(db)} 条 categories_v1", flush=True)

    todo = _todo(db, args.limit)
    print(f"待回填 {len(todo)} 条（并发 {cfg.llm_workers}）", flush=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _one(row):
        shim = _Shim(row)
        try:
            if not classify._classify_one(shim, ctx):
                return ("invalid", row["id"], None)
        except LLMError as e:          # 预算耗尽也是 LLMError（permanent），下次重跑续上
            return ("error", row["id"], str(e)[:80])
        return ("ok", row["id"], shim.categories)

    # 边完成边落库：第一次正式回填跑到一半被中断，因为原先是全部跑完才统一写入，
    # 已完成的调用全部作废。写库只在主线程做（sqlite 连接不跨线程）
    ok = err = invalid = 0
    with ThreadPoolExecutor(max_workers=cfg.llm_workers) as pool:
        futures = [pool.submit(_one, row) for row in todo]
        for n, fut in enumerate(as_completed(futures), 1):
            try:
                kind, item_id, cats = fut.result()
            except Exception:  # noqa: BLE001 单条崩溃不影响其余
                err += 1
                continue
            if kind != "ok":
                err += kind == "error"
                invalid += kind == "invalid"
                continue
            ok += 1
            if args.dry_run:
                print(f"  [dry] {item_id} → {cats}", flush=True)
                continue
            extra = json.loads(db.conn.execute(
                "SELECT extra FROM items WHERE id=?", (item_id,)).fetchone()["extra"] or "{}")
            extra[MARK] = now
            db.conn.execute(
                "UPDATE items SET categories=?, category=?, extra=? WHERE id=?",
                (json.dumps(cats, ensure_ascii=False), cats[0],
                 json.dumps(extra, ensure_ascii=False), item_id))
            db.conn.commit()
            if n % 25 == 0:
                print(f"  进度 {n}/{len(todo)}（成功 {ok}）", flush=True)

    after = _checksum(db)
    print(f"\n完成 {ok} / 模型输出无效 {invalid} / 调用失败 {err}"
          f"（未完成的重跑本脚本会自动续上）")
    snap = budget.snapshot()
    print(f"调用 {snap['calls_made']} 次，token {snap['tokens_used']:,}")
    if before != after:
        sys.exit("!!! status / auto_status 指纹变了——回填不应碰这两列，立即排查")
    print("status / auto_status 指纹一致 ✓")
    if not args.dry_run:
        _report(db)


if __name__ == "__main__":
    main()
