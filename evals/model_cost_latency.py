"""三家模型的成本与延迟：同一批内容、同一个 triage prompt，**串行**逐条调用、计时。

为什么串行：要测的是"一次调用多久"。并发会把限流排队的时间也算进去，
Kimi 那次 73 条 429（D47）就是例子——那是账号配额问题，不是模型本身的延迟。

为什么只测 triage 这一步：它是每条内容都要过的一步，调用量最大；
摘要和分类的 prompt 更长，按同样的输入/输出 token 比例外推会偏低，报告里写明。

价格不写在代码里：从三家官网实时查到后填进 --prices，报告里注明查询日期（CLAUDE.md：版本号、价格这类
高频变动信息一律先查再写）。

用法：
  python3 evals/model_cost_latency.py --n 12
结果存 evals/results/cost-latency-<时间>.json，只读数据库。
"""
import argparse
import json
import os
import statistics as st
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pipeline.config import load_config           # noqa: E402
from pipeline.guards import Budget                # noqa: E402
from pipeline.llm import LLMError, build_client    # noqa: E402
from pipeline.models import Item                   # noqa: E402
from pipeline.stages import triage                 # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "evals"))
import triage_prompt_eval as tpe                   # noqa: E402

MODELS = {   # 角色 → .env 里的变量前缀
    "DeepSeek": "AIRADAR_LLM",
    "GLM": "AIRADAR_FALLBACK",
    "Kimi": "AIRADAR_JUDGE",
}


def measure(name: str, prefix: str, cases: list) -> dict:
    cfg = load_config()
    cfg.llm_base_url = os.environ.get(f"{prefix}_BASE_URL", "")
    cfg.llm_api_key = os.environ.get(f"{prefix}_API_KEY", "")
    cfg.llm_model = os.environ.get(f"{prefix}_MODEL", "")
    cfg.fallback_base_url = cfg.fallback_api_key = cfg.fallback_model = ""   # 只测这一家，不许悄悄切走
    stats = {}
    llm = build_client(cfg, Budget(500_000, len(cases) * 3), stats)
    rows = []
    for c in cases:
        r = c["row"]
        it = Item(title=r["title"] or "", source=r["source"] or "", tier=r["tier"] or "C",
                  content=r["content"] or "", url=r["url"])
        before = (stats.get("llm_prompt_tokens", 0), stats.get("llm_completion_tokens", 0))
        t0 = time.monotonic()
        try:
            outcome = triage.evaluate(it, cfg, llm)
        except LLMError:
            outcome = "error"
        dt = time.monotonic() - t0
        rows.append({"url": r["url"], "outcome": outcome, "seconds": round(dt, 2),
                     "prompt_tokens": stats.get("llm_prompt_tokens", 0) - before[0],
                     "completion_tokens": stats.get("llm_completion_tokens", 0) - before[1]})
        print(f"  {name:<9}{outcome:<11}{dt:6.1f}s  in {rows[-1]['prompt_tokens']:>5}"
              f"  out {rows[-1]['completion_tokens']:>5}", flush=True)
    ok = [x for x in rows if x["outcome"] == "llm_scored"]
    summary = {"model": cfg.llm_model, "calls": len(rows), "ok": len(ok)}
    if ok:
        secs = [x["seconds"] for x in ok]
        summary.update({
            "latency_median_s": round(st.median(secs), 1),
            "latency_p90_s": round(sorted(secs)[int(0.9 * (len(secs) - 1))], 1),
            "prompt_tokens_avg": round(st.mean(x["prompt_tokens"] for x in ok)),
            "completion_tokens_avg": round(st.mean(x["completion_tokens"] for x in ok)),
        })
    return {"summary": summary, "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    args = ap.parse_args()
    # 固定取黄金集前 n 条：三家测的是同一批内容，输入长度一致，延迟才能比
    cases = [c for c in tpe.load_cases() if c["row"]["content"]][:args.n]
    out = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "n": len(cases), "prompt": triage.PROMPT_VERSION, "models": {}}
    for name, prefix in MODELS.items():
        print(f"\n{name}", flush=True)
        out["models"][name] = measure(name, prefix, cases)
    path = os.path.join(ROOT, "evals", "results",
                        f"cost-latency-{datetime.now():%Y%m%d-%H%M%S}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print()
    for name, m in out["models"].items():
        print(name, m["summary"])
    print(f"→ {os.path.relpath(path, ROOT)}")


if __name__ == "__main__":
    main()
