"""一次性回填：分类体系改版后，把历史条目的分类统一到当前 classify.py 的类目表。

用过两次，每次把改版前的分类快照到不同的列：
  - 2026-09-16（D39，13 类）：快照 categories_v1，完成标记 extra.categories_backfilled_at
  - 2026-09-17（D43，平铺 8 类）：快照 categories_v2，完成标记 extra.categories_v3_backfilled_at

用法（默认参数就是最近一次改版）：
  python3 tools/backfill_categories.py --dry-run --limit 5   # 先试 5 条，不写库
  python3 tools/backfill_categories.py                       # 正式回填（可中断，重跑自动续）
  python3 tools/backfill_categories.py --report              # 只出对比表，不调模型
  python3 tools/backfill_categories.py --snapshot-col categories_v1 --report   # 看 D39 那次的对比

只改 prompt（类目表没变）时也用它重跑，换一个完成标记即可，快照列保持不动：
  python3 tools/backfill_categories.py --mark categories_prompt031_at
  python3 tools/backfill_categories.py --mark x --only-category "Agent 与开发" --dry-run --limit 20
重跑前的分类值不再进新列——上一版的值随 data/knowledge.db 一起在 git 历史里（提交 d7d53ba）。

为什么不直接重跑 pipeline（CLAUDE.md：不许靠重跑 pipeline 补数据）：
重跑会新增 run 记录、污染运行统计，还会重新走 triage——而 triage 写 auto_status，
那是 D28 的禁区。这里只借用 classify 的模型调用，其余什么都不碰。

只改 categories / category 两列，**不改 topics 和 horizon**：
- horizon 决定内容过期（db.archive_items），重跑会让历史条目因为模型随机性突然过期或复活
- topics 进话题趋势（memory.py），重跑只会给趋势历史加噪声
这次改版动的是类目表，只有 categories 需要跟着变。

范围按 auto_status：系统当初判为发布或送审的条目都走过分类环节。按 status 选会漏掉
被人工否决的条目（status 已改成 discarded），它们会一直挂着旧类目。
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

SCOPE = "auto_status IN ('published','review')"
SNAPSHOT_COLS = ("categories_v1", "categories_v2")
MARKS = {"categories_v1": "categories_backfilled_at",
         "categories_v2": "categories_v3_backfilled_at"}


class _Shim:
    """_classify_one 读 title / url / summary_long / content，只写四个分类字段。"""
    def __init__(self, row):
        self.title = row["title"] or ""
        self.url = row["url"] or ""
        self.summary_long = row["summary_long"] or ""
        self.content = row["content"] or ""
        self.categories, self.category, self.topics, self.horizon = [], "", [], ""


def _checksum(db) -> str:
    """人的判断与系统原判的指纹。回填前后必须一字不差（D28）。"""
    h = hashlib.sha256()
    for r in db.conn.execute("SELECT id, status, auto_status FROM items ORDER BY id"):
        h.update(f"{r['id']}|{r['status']}|{r['auto_status']}\n".encode())
    return h.hexdigest()


def _snapshot(db, col) -> int:
    """把旧分类存进快照列。只写一次——重跑时绝不能把新分类当成旧的存进去。"""
    assert col in SNAPSHOT_COLS
    cur = db.conn.execute(
        f"UPDATE items SET {col} = categories "
        f"WHERE {SCOPE} AND {col} IS NULL")
    db.conn.commit()
    return cur.rowcount


def _todo(db, limit, mark, only_category=None):
    rows = db.conn.execute(
        f"SELECT id, url, title, category, summary_long, content, extra FROM items WHERE {SCOPE} "
        f"ORDER BY published_at").fetchall()
    rows = [r for r in rows if mark not in json.loads(r["extra"] or "{}")]
    if only_category:
        rows = [r for r in rows if r["category"] in only_category]
    return rows[:limit] if limit else rows


def _report(db, col):
    if col == "categories_v1":
        return _report_v1(db)
    rows = db.conn.execute(
        f"SELECT {col}, categories FROM items WHERE {SCOPE} AND {col} IS NOT NULL").fetchall()
    flow = {}                     # 旧类目 → 这些条目的新主类分布
    old_dist, new_dist, primary_dist = Counter(), Counter(), Counter()
    for r in rows:
        old = json.loads(r[col] or "[]")
        new = json.loads(r["categories"] or "[]")
        old_dist.update(old)
        new_dist.update(new)
        if new:
            primary_dist[new[0]] += 1
        for o in old:
            flow.setdefault(o, Counter())[new[0] if new else "（空）"] += 1
    n = len(rows)
    print(f"\n对比样本：{n} 条（有 {col} 快照的条目）")
    print(f"\n{'新类目':<10}{'作主类':>6}{'出现':>6}")
    for c in classify.CATEGORIES:
        print(f"{c:<10}{primary_dist[c]:>6}{new_dist[c]:>6}")
    print("\n旧类目的条目，新主类去了哪（前 3）：")
    for o, _ in old_dist.most_common():
        top = " · ".join(f"{k} {v}" for k, v in flow[o].most_common(3))
        print(f"  {o:<10}（{old_dist[o]:>3}）→ {top}")


def _report_v1(db):
    """D39 那次的对比：拆出 / 新增类目的归因分析。"""
    NEW_ONLY = {"安全与防护", "落地案例"}
    rows = db.conn.execute(
        f"SELECT categories_v1, categories_v2 AS categories FROM items "
        f"WHERE {SCOPE} AND categories_v1 IS NOT NULL AND categories_v2 IS NOT NULL").fetchall()
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
    for c in sorted(set(old_dist) | set(new_dist)):
        d = new_dist[c] - old_dist[c]
        flag = "  ← 新增" if c in NEW_ONLY else ""
        print(f"{c:<10}{old_dist[c]:>6}{new_dist[c]:>6}{d:>+7}{flag}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="调模型但不写库")
    ap.add_argument("--report", action="store_true", help="只出对比表")
    ap.add_argument("--snapshot-col", default="categories_v2", choices=SNAPSHOT_COLS,
                    help="改版前分类存到哪一列")
    ap.add_argument("--mark", help="换一个完成标记，用于同一套类目表下重跑（比如只改了 prompt）")
    ap.add_argument("--only-category", help="只重跑当前主类在这几个类里的条目，逗号分隔")
    args = ap.parse_args()
    col, mark = args.snapshot_col, args.mark or MARKS[args.snapshot_col]
    only = [c.strip() for c in args.only_category.split(",")] if args.only_category else None

    db = DB()
    if args.report:
        _report(db, col)
        return

    cfg = load_config()
    stats = {}
    # pipeline 单次预算是 260 次调用，不够回填用；单独给一个略高于预估的硬上限
    budget = Budget(token_limit=2_500_000, call_limit=700)
    llm = build_client(cfg, budget, stats)
    if not llm.available():
        sys.exit("没有可用的 LLM（检查 .env）。回填必须用模型，不走启发式兜底。")
    ctx = Context(cfg=cfg, llm=llm, db=db, run_id="backfill", stats=stats)

    before = _checksum(db)
    if not args.dry_run:
        print(f"快照旧分类：新写入 {_snapshot(db, col)} 条 {col}", flush=True)

    todo = _todo(db, args.limit, mark, only)
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
            extra[mark] = now
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
        _report(db, col)


if __name__ == "__main__":
    main()
