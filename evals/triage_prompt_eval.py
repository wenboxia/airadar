"""离线实验：换了 triage 的打分标准之后，三路路由会变好还是变坏。

和 run_eval.py 的区别（为什么不合并）：
- run_eval 每天跟着定时任务跑，必须免费、确定性、不依赖外部服务；这里每跑一次要调上百次模型
- run_eval 校验的是**库里已经落地**的判断，这里实验的是**还没落库**的判断
- 但三路指标只能有一份实现，所以共用 run_eval.route_metrics()

**全程只读数据库**：只调 triage.evaluate() / route()，它们结构上就不能写 status/auto_status（D28）。

用法：
  python3 evals/triage_prompt_eval.py                      # 调模型，逐条原始输出存盘
  python3 evals/triage_prompt_eval.py --replay <file>      # 不调模型，重算指标
  python3 evals/triage_prompt_eval.py --replay <file> --weights 0.4,0.2,0.2,0.6 --cap 30
      # 扫参数：tier权重,relevance,novelty,longterm 与封顶值，全是落库后的算术，零额外调用
"""
import argparse
import importlib.util
import json
import os
import sqlite3
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pipeline.config import load_config           # noqa: E402
from pipeline.db import DB_PATH                    # noqa: E402
from pipeline.guards import Budget                # noqa: E402
from pipeline.llm import build_client              # noqa: E402
from pipeline.models import Item                   # noqa: E402
from pipeline.stages import triage                 # noqa: E402

GOLDEN = os.path.join(ROOT, "evals", "golden_set", "golden.jsonl")
RESULTS = os.path.join(ROOT, "evals", "results")

_spec = importlib.util.spec_from_file_location("run_eval", os.path.join(ROOT, "evals", "run_eval.py"))
run_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_eval)


def load_golden() -> list:
    out = []
    with open(GOLDEN, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("//"):
                g = json.loads(line)
                if not g.get("deprecated"):
                    out.append(g)
    return out


def load_cases() -> list:
    """黄金集条目 + 库里存的原始输入。用库里的 content 是刻意的：
    那就是当初 triage 看到的东西，所以分差只来自 prompt，不来自输入。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    by_url = {r["url"]: dict(r) for r in conn.execute(
        "SELECT url, id, title, source, tier, content, score, auto_status FROM items")}
    conn.close()
    cases = []
    for g in load_golden():
        r = by_url.get(g.get("url"))
        if r:
            cases.append({"gold": g, "row": r})
    return cases


def score_all(cases, cfg, llm) -> list:
    """逐条离线打分，返回原始输出（不含任何路由判断——路由是后面的算术）。"""
    def _one(c):
        r = c["row"]
        it = Item(title=r["title"] or "", source=r["source"] or "", tier=r["tier"] or "C",
                  content=r["content"] or "", url=r["url"])
        outcome = triage.evaluate(it, cfg, llm)
        return {"url": r["url"], "title": r["title"], "source": r["source"], "tier": r["tier"],
                "include": c["gold"]["include"], "old_score": r["score"],
                "old_auto": r["auto_status"], "outcome": outcome,
                "detail": it.score_detail, "notes": it.notes}

    with ThreadPoolExecutor(max_workers=cfg.llm_workers) as pool:
        return list(pool.map(_one, cases))


def recompute(raws, cfg, weights=None, cap=None) -> list:
    """用给定权重/封顶重算分数与路由。纯算术，不调模型。"""
    tw, wr, wn, wl = weights or (cfg.tier_weight, *cfg.value_weights)
    out = []
    for raw in raws:
        d = raw["detail"]
        if raw["outcome"] == "degraded" or "relevance" not in d:
            verdict = "published" if raw["tier"] in ("S", "A") else "review"
            out.append({**raw, "score": d.get("tier_base", 30), "verdict": verdict})
            continue
        value = wr * d["relevance"] + wn * d["novelty"] + wl * d["longterm"]
        kind = d.get("kind")
        limit = cap if cap is not None else cfg.kind_value_cap.get(kind)
        capped = kind in cfg.kind_value_cap and limit is not None and value > limit
        if capped:
            value = limit
        score = round(tw * d["tier_base"] + (1 - tw) * value, 1)
        verdict = ("published" if score >= cfg.publish_threshold
                   else "review" if score >= cfg.review_threshold else "discarded")
        out.append({**raw, "score": score, "verdict": verdict, "capped": capped})
    return out


def report(scored, label: str) -> dict:
    new = run_eval.route_metrics([(s["verdict"], s["include"], s["title"]) for s in scored])
    old = run_eval.route_metrics([(s["old_auto"], s["include"], s["title"]) for s in scored])
    capped_in_review = sum(1 for s in scored if s.get("capped") and s["verdict"] == "review")
    print(f"\n=== {label} · {len(scored)} 条 ===")
    print(f"{'路由':<12}{'旧':>16}{'新':>16}")
    for key, name, metric in (("auto_publish", "自动发布认同", "precision"),
                              ("auto_discard", "自动丢弃认同", "precision"),
                              ("sent_to_human", "送审里该收", "hit_rate")):
        f = lambda m: (f"{m[key][metric]:.0%} ({m[key]['n']})"     # noqa: E731
                       if m[key][metric] is not None else f"— ({m[key]['n']})")
        print(f"{name:<12}{f(old):>16}{f(new):>16}")
    print(f"漏杀（该收却被自动丢弃）：旧 {len(old['auto_discard']['missed'])} 条 → "
          f"新 {len(new['auto_discard']['missed'])} 条")
    for t in new["auto_discard"]["missed"]:
        print(f"    ✗ {t}")
    print(f"被封顶的条目：{sum(1 for s in scored if s.get('capped'))} 条"
          f"（其中仍在送审区 {capped_in_review} 条）")
    kinds = Counter(s["detail"].get("kind") for s in scored if s["detail"].get("kind"))
    print("kind 分布：" + " · ".join(f"{k} {v}" for k, v in kinds.most_common()))
    return {"label": label, "new": new, "old": old,
            "kinds": dict(kinds), "n": len(scored)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", help="重放已存盘的原始打分，不调模型")
    ap.add_argument("--weights", help="tier,relevance,novelty,longterm，如 0.4,0.2,0.2,0.6")
    ap.add_argument("--cap", type=float, help="营销通稿 / 仿造品的价值分封顶")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    cfg = load_config()

    if args.replay:
        raws = [json.loads(l) for l in open(args.replay, encoding="utf-8") if l.strip()]
        label = f"replay {os.path.basename(args.replay)}"
    else:
        cases = load_cases()
        if args.limit:
            cases = cases[:args.limit]
        llm = build_client(cfg, Budget(1_000_000, len(cases) * 2), {})
        if not llm.available():
            sys.exit("没有可用的 LLM")
        raws = score_all(cases, cfg, llm)
        os.makedirs(RESULTS, exist_ok=True)
        path = os.path.join(RESULTS, f"triage-prompt-{datetime.now():%Y%m%d-%H%M%S}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for r in raws:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        label = f"triage {triage.MANIFEST['version']}"
        print(f"原始打分已存 {os.path.relpath(path, ROOT)}（之后可 --replay 扫参数，零额外调用）")

    weights = tuple(float(x) for x in args.weights.split(",")) if args.weights else None
    scored = recompute(raws, cfg, weights, args.cap)
    res = report(scored, label + (f" 权重{weights}" if weights else "") +
                 (f" 封顶{args.cap}" if args.cap is not None else ""))
    res["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    res["weights"] = weights or (cfg.tier_weight, *cfg.value_weights)
    res["cap"] = args.cap if args.cap is not None else cfg.kind_value_cap
    out = os.path.join(RESULTS, f"triage-eval-{datetime.now():%Y%m%d-%H%M%S}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"→ {os.path.relpath(out, ROOT)}")


if __name__ == "__main__":
    main()
