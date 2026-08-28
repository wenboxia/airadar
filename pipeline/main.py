"""AIRadar AgentLoop 入口。

手写 agent loop（不用 LangGraph，理由见 docs/decisions.md D1）：
线性 pipeline + 置信度条件路由，每个 stage 是一个带统一契约的 skill：
    run(items: list[Item], ctx: Context) -> list[Item]
stage 内部各自兜底；这里做外层舱壁：单 stage 崩溃记录错误后跳过，不炸整个运行。

用法：
    python3 -m pipeline.main                # 完整运行
    python3 -m pipeline.main --limit 5      # 每信源限量（调试）
    python3 -m pipeline.main --no-llm       # 强制降级路径（零成本验证）
    python3 -m pipeline.main --since-days 7 # 放宽时间窗（首跑用）
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone

from .config import ROOT, load_config
from .db import DB
from .guards import Budget
from .llm import build_client
from .models import Context
from .stages import classify, dedupe, fetch, publish, summarize, triage

STAGES = [fetch, dedupe, triage, summarize, classify, publish]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="每个信源最多抓 N 条（调试用）")
    ap.add_argument("--no-llm", action="store_true", help="不调用 LLM，走降级路径")
    ap.add_argument("--since-days", type=int, help="时间窗（天）")
    args = ap.parse_args()

    cfg = load_config()
    if args.limit:
        cfg.per_source_limit = args.limit
    if args.since_days:
        cfg.since_days = args.since_days
    if args.no_llm:
        cfg.llm_api_key = cfg.fallback_api_key = ""

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    stats = {}
    budget = Budget(cfg.token_budget, cfg.max_llm_calls)
    llm = build_client(cfg, budget, stats)
    ctx = Context(cfg=cfg, llm=llm, db=DB(), run_id=run_id, stats=stats)

    if llm.available():
        chain = " → ".join(f"{p.name}({p.model})" for p in llm.providers)
        mode = f"LLM 模式 | 降级链 {chain}"
    else:
        mode = "降级模式（无可用 LLM）"
    print(f"AIRadar run {run_id} 启动 | {mode} | 时间窗 {cfg.since_days} 天")

    items = []
    for stage in STAGES:
        name = stage.MANIFEST["name"]
        t0 = time.time()
        try:
            items = stage.run(items, ctx)
        except Exception as ex:  # noqa: BLE001 —— 外层舱壁
            ctx.note_error(name, f"stage 级失败: {ex}")
        stats.setdefault("timing", {})[name] = round(time.time() - t0, 1)

    finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    stats["budget"] = budget.snapshot()
    ctx.db.save_run(run_id, started, finished, stats, ctx.errors)
    # 本次运行入库后重写一遍 stats.json，否则前端看到的运行记录永远少一次
    publish.write_stats(ctx)

    os.makedirs(os.path.join(ROOT, "data", "runs"), exist_ok=True)
    report = {"run_id": run_id, "started": started, "finished": finished,
              "mode": mode, "stats": stats, "errors": ctx.errors}
    with open(os.path.join(ROOT, "data", "runs", f"{run_id}.json"),
              "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    print(f"\n=== 运行报告 {run_id} ===")
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    if ctx.errors:
        print(f"非致命错误 {len(ctx.errors)} 个（详见 data/runs/{run_id}.json）")


if __name__ == "__main__":
    main()
