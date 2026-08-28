"""Stage 2: dedupe —— 去重。

两层：URL 规范化（去 utm 等跟踪参数）+ 批内标题相似度（同一事件多信源报道时保高 tier 的）。
同时过滤掉知识库里已存在的条目（跨天去重）。
"""
import hashlib
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..models import Context

MANIFEST = {
    "name": "dedupe", "version": "0.1.0",
    "input": "list[Item]", "output": "list[Item]（去重后，已赋 id）",
    "eval_cases": "evals/golden_set/",
}

_TRACK_PREFIXES = ("utm_", "ref", "fbclid", "gclid", "source")
_TIER_ORDER = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4, "X": 5}


def canonical_url(url: str) -> str:
    p = urlparse(url.strip())
    query = [(k, v) for k, v in parse_qsl(p.query)
             if not any(k.lower().startswith(t) for t in _TRACK_PREFIXES)]
    return urlunparse((p.scheme.lower(), p.netloc.lower(),
                       p.path.rstrip("/"), "", urlencode(query), ""))


def item_id(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode()).hexdigest()[:16]


def run(items: list, ctx: Context) -> list:
    existing = ctx.db.existing_ids()
    seen_ids = set()
    kept = []
    dropped_db = dropped_url = dropped_title = 0

    # 高 tier 优先保留（标题撞车时留权威来源）
    for it in sorted(items, key=lambda x: _TIER_ORDER.get(x.tier, 9)):
        it.id = item_id(it.url)
        if it.id in existing:
            dropped_db += 1
            continue
        if it.id in seen_ids:
            dropped_url += 1
            continue
        title_dup = any(
            SequenceMatcher(None, it.title.lower(), k.title.lower()).ratio() > 0.92
            for k in kept)
        if title_dup:
            dropped_title += 1
            continue
        seen_ids.add(it.id)
        kept.append(it)

    ctx.stats["dedupe"] = {"kept": len(kept), "dropped_already_in_db": dropped_db,
                           "dropped_dup_url": dropped_url,
                           "dropped_dup_title": dropped_title}
    print(f"  dedupe: 保留 {len(kept)}（库中已有 {dropped_db}，URL 重复 {dropped_url}，标题重复 {dropped_title}）")
    return kept
