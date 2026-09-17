"""测分类的「噪声底」：同一个 prompt、同一批内容，连分两次，结果有多一致（D43）。

为什么要单独测：D39 说"重分类有 37% 会变"，但那次比的是新旧两个不同 prompt，
混着体系改动和模型随机性，不能当基线。要判断新分类是不是更稳，得拿「同一 prompt 自己跟自己比」。

用法：
  python3 tools/classify_stability.py --n 100          # 测当前 pipeline/stages/classify.py
  python3 tools/classify_stability.py --n 100 --seed 7 # 换一批样本

结果存 evals/results/classify-stability-<时间>.json，只读数据库，不写任何条目。
"""
import argparse
import json
import os
import random
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pipeline.config import load_config           # noqa: E402
from pipeline.db import DB                         # noqa: E402
from pipeline.guards import Budget                # noqa: E402
from pipeline.llm import LLMError, build_client    # noqa: E402
from pipeline.stages import classify               # noqa: E402


class _Shim:
    def __init__(self, row):
        self.url = row["url"] or ""
        self.title = row["title"] or ""
        self.source = row["source"] or ""
        self.summary_long = row["summary_long"] or ""
        self.content = row["content"] or ""
        self.categories, self.category, self.topics, self.horizon = [], "", [], ""
        self.notes, self.extra = [], {}


def _once(row, ctx):
    it = _Shim(row)
    try:
        ok = classify._classify_one(it, ctx)
    except LLMError:
        ok = False
    return (it.category, tuple(sorted(it.categories))) if ok else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20260917)
    args = ap.parse_args()

    db = DB()
    rows = db.conn.execute(
        "SELECT id, url, title, source, summary_long, content FROM items "
        "WHERE status IN ('published','review') ORDER BY id").fetchall()
    sample = random.Random(args.seed).sample(list(rows), min(args.n, len(rows)))

    cfg = load_config()
    llm_stats = {}
    llm = build_client(cfg, Budget(2_000_000, args.n * 3), llm_stats)
    if not llm.available():
        sys.exit("没有可用的 LLM")
    ctx = type("Ctx", (), {"llm": llm, "cfg": cfg})()

    with ThreadPoolExecutor(max_workers=cfg.llm_workers) as pool:
        first = list(pool.map(lambda r: _once(r, ctx), sample))
        second = list(pool.map(lambda r: _once(r, ctx), sample))

    pairs = [(a, b) for a, b in zip(first, second) if a and b]
    n = len(pairs)
    primary_same = sum(a[0] == b[0] for a, b in pairs)
    set_same = sum(a[1] == b[1] for a, b in pairs)
    flips = Counter()
    for a, b in pairs:
        if a[0] != b[0]:
            flips[tuple(sorted((a[0], b[0])))] += 1

    result = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "classify_version": classify.MANIFEST["version"],
        "categories": classify.CATEGORIES,
        "model": cfg.llm_model,
        "seed": args.seed,
        # 主力限流时会切到备用模型，混进另一家的输出会让数字失真，所以记下来
        "calls_by_provider": llm_stats.get("calls_by_provider", {}),
        "sampled": len(sample),
        "valid_pairs": n,
        "primary_agreement": round(primary_same / n, 3) if n else None,
        "set_agreement": round(set_same / n, 3) if n else None,
        "top_primary_flips": [[f"{x} ↔ {y}", k] for (x, y), k in flips.most_common(10)],
    }
    os.makedirs(os.path.join(ROOT, "evals", "results"), exist_ok=True)
    path = os.path.join(ROOT, "evals", "results",
                        f"classify-stability-{datetime.now():%Y%m%d-%H%M%S}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    print(f"\n→ {os.path.relpath(path, ROOT)}")


if __name__ == "__main__":
    main()
