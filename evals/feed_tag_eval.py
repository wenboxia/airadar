"""分类准确率：拿官网 feed 自带的标签当参照（D39 的免费评测集，D43 起替代人工标分类）。

为什么不让主人在黄金集里标分类：分类体系还在变，标了就得跟着重标；
而 OpenAI / Anthropic 的 feed 每条都自带编辑打的标签，是独立于我们系统的现成答案。

但官网标签按发布方的组织架构划分，不等于我们的类目，所以：
- 只用意思明确的标签，Company / Research / Announcements 这类大杂烩不用
- 每个标签给一个「可接受的主类集合」，模型的主类落在集合里就算对
- 集合只有一个类的标签（单答案）单独统计，那才是真正卡得住的部分

输入是 feed 里的标题 + 简介（OpenAI 约 140 字、Anthropic 约 60 字），
比 pipeline 实际用的中文长摘要信息少，测出来的数偏保守。

用法：
  python3 evals/feed_tag_eval.py --per-tag 15       # 每个标签最多抽 15 条
结果存 evals/results/feed-tags-<时间>.json（含逐条明细，不一致的条目就是改 prompt 的线索）
"""
import argparse
import json
import math
import os
import random
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import feedparser
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pipeline.config import load_config           # noqa: E402
from pipeline.guards import Budget                # noqa: E402
from pipeline.llm import LLMError, build_client    # noqa: E402
from pipeline.stages import classify               # noqa: E402
from pipeline.stages.fetch import UA, _clean_text  # noqa: E402

FEEDS = {
    "OpenAI": "https://openai.com/news/rss.xml",
    "Anthropic": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_news.xml",
}

# 标签 → 可接受的主类。依据是 2026-09-17 逐个标签看过的标题样本
TAG_MAP = {
    "OpenAI": {
        "Safety": {"安全"},
        "Safety & Alignment": {"安全"},        # 以 System Card、对齐研究为主
        "Security": {"安全", "产品与应用"},     # 「给防御方用的模型 / 计划」按边界句主类可以是产品
        "Startup": {"产品与应用"},              # 创业公司用 OpenAI 的案例
        "Story": {"产品与应用"},                # 企业客户案例
        "API": {"产品与应用"},                  # 看过样本，全是客户案例，不是 API 发布
        "AI Adoption": {"产品与应用"},
        "Applied AI": {"产品与应用"},           # AI 用于科研、医疗
        "Global Affairs": {"行业动态", "安全"},  # 政策、国家合作；也有打击影响力行动
        "Engineering": {"Agent 与开发", "模型"},  # 系统工程；推理加速归模型
        "Product": {"模型", "产品与应用", "Agent 与开发"},
        "Release": {"模型", "产品与应用", "Agent 与开发"},
    },
    "Anthropic": {
        "Case Study": {"产品与应用"},
        "Education": {"产品与应用"},
        "Economics": {"行业动态"},
        "Policy": {"安全", "行业动态"},          # 负责任扩展政策、立法表态、用电承诺
        "Societal Impacts": {"行业动态", "安全"},
        "Product": {"模型", "产品与应用", "Agent 与开发"},
        # Alignment 只有 1 条，标题是能源投资，标签本身不可信，不用
    },
}


class _Shim:
    def __init__(self, title, text, url):
        self.title, self.url = title, url
        self.summary_long, self.content = "", text
        self.categories, self.category, self.topics, self.horizon = [], "", [], ""


def wilson(k, n, z=1.96):
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 3), round(c + h, 3)]


def load_samples(per_tag, seed):
    rng = random.Random(seed)
    out = []
    for feed, url in FEEDS.items():
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        by_tag = defaultdict(list)
        for e in feedparser.parse(r.content).entries:
            tags = [t.get("term") for t in e.get("tags", [])]
            if len(tags) != 1 or tags[0] not in TAG_MAP[feed]:
                continue
            by_tag[tags[0]].append({
                "feed": feed, "tag": tags[0], "title": e.get("title", ""),
                "url": e.get("link", ""),
                "text": _clean_text(e.get("summary", ""), 800),
            })
        for tag in sorted(by_tag):
            rows = by_tag[tag]
            out += rng.sample(rows, min(per_tag, len(rows)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-tag", type=int, default=15)
    ap.add_argument("--seed", type=int, default=20260917)
    args = ap.parse_args()

    samples = load_samples(args.per_tag, args.seed)
    cfg = load_config()
    llm_stats = {}
    llm = build_client(cfg, Budget(2_000_000, len(samples) * 2), llm_stats)
    if not llm.available():
        sys.exit("没有可用的 LLM")
    ctx = type("Ctx", (), {"llm": llm, "cfg": cfg})()

    def _one(s):
        it = _Shim(s["title"], s["text"], s["url"])
        try:
            ok = classify._classify_one(it, ctx)
        except LLMError:
            ok = False
        accept = TAG_MAP[s["feed"]][s["tag"]]
        return {**s, "accept": sorted(accept),
                "primary": it.category if ok else None,
                "categories": it.categories if ok else None,
                "hit": ok and it.category in accept,
                "loose_hit": ok and bool(accept & set(it.categories))}

    with ThreadPoolExecutor(max_workers=cfg.llm_workers) as pool:
        rows = list(pool.map(_one, samples))
    rows = [r for r in rows if r["primary"]]

    def _summ(rs):
        n, k = len(rs), sum(r["hit"] for r in rs)
        return {"n": n, "hit": k, "rate": round(k / n, 3) if n else None, "ci95": wilson(k, n),
                "loose_rate": round(sum(r["loose_hit"] for r in rs) / n, 3) if n else None}

    content = set(classify.CONTENT_CATEGORIES)
    sec_rate = round(sum(1 for r in rows
                         if len(content & set(r["categories"])) > 1) / len(rows), 3) if rows else None
    single = [r for r in rows if len(r["accept"]) == 1]
    per_tag = {}
    for r in rows:
        per_tag.setdefault(f'{r["feed"]}/{r["tag"]}', []).append(r)

    result = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "classify_version": classify.MANIFEST["version"],
        "model": cfg.llm_model,
        "seed": args.seed, "per_tag": args.per_tag,
        # 主力限流时会切到备用模型，混进另一家的输出会让数字失真，所以记下来
        "calls_by_provider": llm_stats.get("calls_by_provider", {}),
        "sampled": len(samples), "classified": len(rows),
        "secondary_rate": sec_rate,
        "all": _summ(rows),
        "single_answer_tags": _summ(single),
        "by_tag": {k: {**_summ(v), "accept": v[0]["accept"],
                       "got": dict(sorted(
                           {p: sum(x["primary"] == p for x in v) for p in {x["primary"] for x in v}}.items(),
                           key=lambda kv: -kv[1]))}
                   for k, v in sorted(per_tag.items())},
        "misses": [{k: r[k] for k in ("feed", "tag", "title", "url", "accept", "categories")}
                   for r in rows if not r["hit"]],
        # 逐条留痕：换 seed 复测时要能算出两次抽样重叠了多少，否则说不清"新样本"有多新
        "rows": [{"tag": f'{r["feed"]}/{r["tag"]}', "url": r["url"], "title": r["title"],
                  "primary": r["primary"], "cats": r["categories"], "hit": r["hit"]} for r in rows],
    }
    os.makedirs(os.path.join(ROOT, "evals", "results"), exist_ok=True)
    path = os.path.join(ROOT, "evals", "results",
                        f"feed-tags-{datetime.now():%Y%m%d-%H%M%S}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    print(f"抽样 {len(samples)} 条，分类成功 {len(rows)} 条（classify {result['classify_version']}）"
          f"，带副类 {sec_rate:.1%}")
    for name, key in (("全部标签", "all"), ("单答案标签", "single_answer_tags")):
        s = result[key]
        print(f"{name}：主类命中 {s['hit']}/{s['n']} = {s['rate']:.0%}"
              f"（95% CI {s['ci95'][0]:.0%}–{s['ci95'][1]:.0%}），主副类任一命中 {s['loose_rate']:.0%}")
    print(f"\n{'标签':<28}{'n':>4}{'命中':>7}  可接受 → 实际主类")
    for k, v in result["by_tag"].items():
        got = " ".join(f"{p}{c}" for p, c in v["got"].items())
        print(f"{k:<28}{v['n']:>4}{v['rate']:>7.0%}  {'/'.join(v['accept'])} → {got}")
    print(f"\n→ {os.path.relpath(path, ROOT)}")


if __name__ == "__main__":
    main()
