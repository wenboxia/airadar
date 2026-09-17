"""一次性工具：用新的打分标准给"还堵在审批队列里"的条目重算一次分。

背景：2026-09-18 把 triage 的打分标准改成和黄金集收录标准同一套（D45）。
库里 248 条待审是按旧标准打的分，不重算就会继续按旧分排队。

**只写 extra.rescore 这一处**，原始 `score` / `score_detail` / `auto_status` / `status`
一个字节都不动：
- 不写 status：新分数只是"新标准下的虚拟路由"，不是人的判断。改 status 会让
  run_eval 的 human_touched 把这一百多条统计成"被人工审批改写过"——一个不会报错的假数字
- 不写 auto_status：那是 triage 当初的原判，评测唯一可信的对照面（D28）
- 不加新列：upsert_items 是 INSERT OR REPLACE，不在 Item dataclass 里的列会被清空；
  extra 是 Item 的字段，跟着 dataclass 一起走

用法：
  python3 tools/rescore_review_backlog.py --dry-run --limit 5
  python3 tools/rescore_review_backlog.py            # 正式跑（可中断，重跑自动续）
  python3 tools/rescore_review_backlog.py --report   # 只出对比表，不调模型
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
from pipeline.models import Item                   # noqa: E402
from pipeline.stages import triage                 # noqa: E402

SCOPE = "status='review'"      # 这次要处理的就是"现在还堵在队列里的"，所以看 status
PROMPT = triage.PROMPT_VERSION


def _checksum(db) -> str:
    """比分类回填那次更严：这次连分数都不许动。"""
    h = hashlib.sha256()
    for r in db.conn.execute(
            "SELECT id, status, auto_status, score, score_detail FROM items ORDER BY id"):
        h.update(f"{r['id']}|{r['status']}|{r['auto_status']}|{r['score']}|"
                 f"{r['score_detail']}\n".encode())
    return h.hexdigest()


def _todo(db, limit):
    rows = [dict(r) for r in db.conn.execute(
        f"SELECT * FROM items WHERE {SCOPE} ORDER BY published_at")]
    rows = [r for r in rows
            if (json.loads(r["extra"] or "{}").get("rescore") or {}).get("prompt") != PROMPT]
    return rows[:limit] if limit else rows


def _report(db):
    rows = [dict(r) for r in db.conn.execute(f"SELECT * FROM items WHERE {SCOPE}")]
    done = [(r, (json.loads(r["extra"] or "{}").get("rescore") or {})) for r in rows]
    done = [(r, rs) for r, rs in done if rs.get("prompt") == PROMPT]
    if not done:
        print("还没有重打分记录")
        return
    verdicts = Counter(rs["verdict"] for _, rs in done)
    kinds = Counter(rs.get("kind") for _, rs in done)
    print(f"\n重打分 {len(done)} / 队列 {len(rows)} 条（打分标准 {PROMPT}）")
    print("\n新标准下的去向：" + " · ".join(f"{k} {v}" for k, v in verdicts.most_common()))
    print("内容类型：" + " · ".join(f"{k} {v}" for k, v in kinds.most_common()))

    by_src = {}
    for r, rs in done:
        s = by_src.setdefault(r["source"], Counter())
        s[rs["verdict"]] += 1
        s["n"] += 1
    print(f"\n{'信源':<26}{'条数':>5}{'仍送审':>7}{'不达标':>7}{'可发布':>7}")
    for name, c in sorted(by_src.items(), key=lambda kv: -kv[1]["n"]):
        print(f"{name:<26}{c['n']:>5}{c['review']:>7}{c['discarded']:>7}{c['published']:>7}")

    moved = [(r, rs) for r, rs in done if abs(rs["score"] - (r["score"] or 0)) >= 10]
    print(f"\n分数变化 10 分以上的 {len(moved)} 条，抽 8 条看：")
    for r, rs in sorted(moved, key=lambda x: x[1]["score"] - (x[0]["score"] or 0))[:4] + \
            sorted(moved, key=lambda x: -(x[1]["score"] - (x[0]["score"] or 0)))[:4]:
        print(f"  {r['score']:>5} → {rs['score']:>5}  [{rs.get('kind')}] {r['title'][:44]}")

    print("\n⚠️ 这份表不是对新标准的独立验证：队列里有 41 条就在黄金集里，"
          "而新标准的参数正是在黄金集上调的。独立验证只能靠再标一批，"
          "或看抽样复审的撤下率（那是无偏的前瞻口径）。")


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
    budget = Budget(token_limit=600_000, call_limit=400)
    llm = build_client(cfg, budget, {})
    if not llm.available():
        sys.exit("没有可用的 LLM。降级打分只有 tier 分，落进 rescore 就是污染。")

    before = _checksum(db)
    todo = _todo(db, args.limit)
    print(f"待重打分 {len(todo)} 条（并发 {cfg.llm_workers}）", flush=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _one(row):
        it = Item(title=row["title"] or "", source=row["source"] or "",
                  tier=row["tier"] or "C", content=row["content"] or "", url=row["url"])
        try:
            outcome = triage.evaluate(it, cfg, llm)   # 结构上就写不到状态字段
        except LLMError as e:
            return ("error", row["id"], str(e)[:80])
        if outcome != "llm_scored":
            return ("degraded", row["id"], None)
        return ("ok", row["id"], {"prompt": PROMPT, "at": now, "score": it.score,
                                  "verdict": triage.route(it, cfg, outcome),
                                  "kind": it.score_detail.get("kind"),
                                  "detail": it.score_detail})

    ok = err = degraded = 0
    with ThreadPoolExecutor(max_workers=cfg.llm_workers) as pool:
        futures = [pool.submit(_one, row) for row in todo]
        for n, fut in enumerate(as_completed(futures), 1):
            try:
                kind, item_id, payload = fut.result()
            except Exception:  # noqa: BLE001 单条崩溃不影响其余
                err += 1
                continue
            if kind != "ok":
                err += kind == "error"
                degraded += kind == "degraded"
                continue
            ok += 1
            if args.dry_run:
                print(f"  [dry] {item_id} → {payload['score']} {payload['verdict']}"
                      f" [{payload['kind']}]", flush=True)
                continue
            # 边完成边落库：上一个一次性工具就是"全跑完再写"，中断后进度全丢
            extra = json.loads(db.conn.execute(
                "SELECT extra FROM items WHERE id=?", (item_id,)).fetchone()["extra"] or "{}")
            extra["rescore"] = payload
            db.conn.execute("UPDATE items SET extra=? WHERE id=?",
                            (json.dumps(extra, ensure_ascii=False), item_id))
            db.conn.commit()
            if n % 25 == 0:
                print(f"  进度 {n}/{len(todo)}（成功 {ok}）", flush=True)

    after = _checksum(db)
    print(f"\n完成 {ok} / 降级跳过 {degraded} / 调用失败 {err}（重跑本脚本会自动续上）")
    snap = budget.snapshot()
    print(f"调用 {snap['calls_made']} 次，token {snap['tokens_used']:,}")
    if before != after:
        sys.exit("!!! status / auto_status / score 指纹变了——本工具只该写 extra，立即排查")
    print("status / auto_status / score 指纹一致 ✓")
    if not args.dry_run:
        _report(db)


if __name__ == "__main__":
    main()
