"""换主力模型之前，摘要这一步要单独测：它 token 用得最多，也是最容易出幻觉的一步。

两阶段，因为生成和裁判用的是不同账号，限流互不相干：
  1. --generate：同一批文章，每个候选模型各写一遍摘要（用 pipeline 里真实的 summarize prompt）
  2. --judge <文件>：Kimi 当裁判，每条判 3 次取多数票（D33），和候选模型都不同家（D4）

只读数据库；摘要写进 evals/results，不进库。

用法：
  python3 evals/summary_model_eval.py --generate --n 10
  python3 evals/summary_model_eval.py --judge evals/results/summary-gen-<时间>.jsonl
"""
import argparse
import importlib.util
import json
import os
import random
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pipeline.config import load_config           # noqa: E402
from pipeline.db import DB_PATH                    # noqa: E402
from pipeline.guards import Budget                # noqa: E402
from pipeline.llm import LLMError, build_client    # noqa: E402
from pipeline.models import Item                   # noqa: E402
from pipeline.stages import summarize              # noqa: E402

RESULTS = os.path.join(ROOT, "evals", "results")
DEFAULT = ("DSPro=AIRADAR_LLM:deepseek-v4-pro,DSFlash=AIRADAR_LLM:deepseek-flash,"
           "GLMFlash=AIRADAR_FALLBACK:glm-5.3-flash")

_spec = importlib.util.spec_from_file_location(
    "judge", os.path.join(ROOT, "evals", "judge_hallucination.py"))
judge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(judge)


def _sample(n: int, seed: int) -> list:
    """只挑有完整原文的：原文不足走简介模式，没有原文可对照就测不了忠实度（D14）。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT id, url, title, source, content FROM items "
        "WHERE status IN ('published','review') AND length(content) >= ? ORDER BY id",
        (summarize.THIN_CONTENT_CHARS * 3,))]
    conn.close()
    return random.Random(seed).sample(rows, min(n, len(rows)))


def _client(prefix: str, model: str):
    cfg = load_config()
    cfg.llm_base_url = os.environ.get(f"{prefix}_BASE_URL", "")
    cfg.llm_api_key = os.environ.get(f"{prefix}_API_KEY", "")
    cfg.llm_model = model
    cfg.fallback_base_url = cfg.fallback_api_key = cfg.fallback_model = ""  # 只测这一家
    return build_client(cfg, Budget(600_000, 200), {})


def generate(n: int, seed: int, spec: str) -> str:
    sample = _sample(n, seed)
    models = {}
    for part in spec.split(","):
        label, rest = part.split("=", 1)
        prefix, model = rest.split(":", 1)
        models[label] = (model, _client(prefix, model))
    path = os.path.join(RESULTS, f"summary-gen-{datetime.now():%Y%m%d-%H%M%S}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for r in sample:
            for label, (model, llm) in models.items():
                it = Item(title=r["title"], content=r["content"], url=r["url"])
                ctx = type("Ctx", (), {"llm": llm})()
                t0 = time.monotonic()
                try:
                    ok = summarize._summarize_one(it, ctx)
                except LLMError:
                    ok = False
                row = {"id": r["id"], "title": r["title"], "label": label, "model": model,
                       "ok": ok, "seconds": round(time.monotonic() - t0, 1),
                       "summary_short": it.summary_short, "summary_long": it.summary_long,
                       "content": r["content"]}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(f"  {label:<9}{'ok' if ok else 'FAIL':<5}{row['seconds']:>6}s  {r['title'][:40]}",
                      flush=True)
    print(f"→ {os.path.relpath(path, ROOT)}")
    return path


def judge_file(path: str, k: int):
    cfg = load_config()
    llm = judge._judge_client(cfg)
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    out = []
    for r in rows:
        if not r["ok"]:
            out.append({**r, "verdict": None, "skipped_reason": "摘要生成失败"})
            continue
        v = judge._vote(llm, r, k)
        out.append({**r, **{key: v.get(key) for key in ("verdict", "votes", "split", "problems",
                                                         "skipped_reason")}})
        mark = {True: "✅", False: "⚠️", None: "—"}[v["verdict"]]
        print(f"  {mark} {r['label']:<9}{r['title'][:44]}", flush=True)

    by = {}
    for r in out:
        by.setdefault(r["label"], []).append(r)
    summary = {}
    for label, rs in by.items():
        judged = [r for r in rs if r["verdict"] is not None]
        bad = [r for r in judged if r["verdict"] is False]
        summary[label] = {
            "model": rs[0]["model"], "n": len(rs), "generated": sum(r["ok"] for r in rs),
            "judged": len(judged), "unfaithful": len(bad),
            "hallucination_rate": round(len(bad) / len(judged), 3) if judged else None,
            "avg_len": round(sum(len(r["summary_long"] or "") for r in rs if r["ok"])
                             / max(1, sum(r["ok"] for r in rs))),
            "gen_seconds_median": sorted(r["seconds"] for r in rs)[len(rs) // 2],
            "problems": [(r["title"][:40], (r.get("problems") or [""])[0][:120]) for r in bad],
        }
    res = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "judge_model": cfg.judge_model, "k": k, "source": os.path.basename(path),
           "summary": summary, "rows": [{kk: v for kk, v in r.items() if kk != "content"}
                                        for r in out]}
    dst = path.replace("summary-gen-", "summary-judge-").replace(".jsonl", ".json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print()
    for label, s in summary.items():
        print(label, {k2: v for k2, v in s.items() if k2 != "problems"})
    print(f"→ {os.path.relpath(dst, ROOT)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--judge")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--seed", type=int, default=20260918)
    ap.add_argument("--models", default=DEFAULT)
    args = ap.parse_args()
    if args.generate:
        generate(args.n, args.seed, args.models)
    elif args.judge:
        judge_file(args.judge, args.k)
    else:
        ap.error("要么 --generate，要么 --judge <文件>")


if __name__ == "__main__":
    main()
