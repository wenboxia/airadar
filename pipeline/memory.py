"""记忆分层：把一条条孤立的内容变成"话题的演进轨迹"。

三层记忆（对应产品的短/中/长期）：
  短期 —— 今日/本周 feed（publish.py 已产出）
  中期 —— **话题热度与生命周期**（本模块）：同一话题在 7/30/90 天窗口里的出现频次，
          据此判断它处于萌芽/升温/成熟/衰退的哪个阶段
  长期 —— 知识库 + 话题时间线（本模块产出时间线，db.archive_items() 产出知识库）

为什么这是"记忆"而不是"统计"：单看一条内容只知道"发生了什么"；
把同一话题的历史串起来才知道"它正在往哪走"——后者才是判断"要不要跟进"的依据。
"""
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

from .config import ROOT
from .db import DB
from .stages.fetch import parse_iso

FEED_DIR = os.path.join(ROOT, "data", "feed")
WINDOWS = (7, 30, 90)
MIN_MENTIONS = 2          # 少于这个次数不算一个"话题"，只是噪声


# 生命周期判断需要足够的历史跨度。数据只有几天时，任何话题看起来都像"刚萌芽"——
# 那不是发现，是错觉（decisions.md D31）。跨度不足时诚实标注"数据不足"。
MIN_SPAN_DAYS = 21


def _lifecycle(w7: int, w30: int, w90: int, span_days: int) -> tuple:
    """用三个窗口的相对密度判断生命周期。

    比的是"日均频次"而不是绝对次数——否则长窗口永远数字更大，看不出趋势。
    """
    if span_days < MIN_SPAN_DAYS:
        return "观察中", f"知识库目前只覆盖 {span_days} 天，还不足以判断趋势"
    d7, d30, d90 = w7 / 7, w30 / 30, w90 / 90
    if w90 == w7 and w7 >= MIN_MENTIONS:
        return "萌芽", "第一次出现就密集讨论，是全新话题"
    if d7 > d30 * 1.6:
        return "升温", "近 7 天的讨论密度明显高于过去一个月"
    if d30 > d90 * 1.4:
        return "成熟", "持续被讨论，热度稳定"
    if d7 < d30 * 0.5:
        return "衰退", "近期讨论明显减少"
    return "平稳", "热度没有明显变化"


def build(db: DB) -> dict:
    now = datetime.now(timezone.utc)
    rows = [dict(r) for r in db.conn.execute(
        "SELECT * FROM items WHERE status='published'")]

    # 知识库实际覆盖的时间跨度——决定生命周期判断可不可信
    dates = [parse_iso(r.get("published_at") or "") for r in rows]
    dates = [d for d in dates if d]
    span_days = (max(dates) - min(dates)).days if len(dates) > 1 else 0

    buckets = {w: defaultdict(list) for w in WINDOWS}
    for r in rows:
        d = parse_iso(r.get("published_at") or "")
        if not d:
            continue
        age = (now - d).days
        topics = json.loads(r.get("topics") or "null") or []
        cats = json.loads(r.get("categories") or "null") or (
            [r["category"]] if r.get("category") else [])
        # 话题 = 细粒度 topic 标签 + 分类，两者都进热度统计
        for name in set(topics) | set(cats):
            if not name:
                continue
            for w in WINDOWS:
                if age <= w:
                    buckets[w][name].append(r)

    topics_out = []
    for name in buckets[90]:
        w7, w30, w90 = (len(buckets[w].get(name, [])) for w in WINDOWS)
        if w90 < MIN_MENTIONS:
            continue
        phase, why = _lifecycle(w7, w30, w90, span_days)
        # 时间线：该话题的历史条目按时间倒序——"长期记忆"的可视形态
        timeline = sorted(buckets[90][name],
                          key=lambda r: r.get("published_at") or "", reverse=True)
        topics_out.append({
            "topic": name,
            "counts": {"d7": w7, "d30": w30, "d90": w90},
            "phase": phase, "why": why,
            "top_score": max((r["score"] or 0) for r in buckets[90][name]),
            "timeline": [{"id": r["id"], "title": r["title"], "url": r["url"],
                          "source": r["source"], "tier": r["tier"],
                          "published_at": r["published_at"], "score": r["score"],
                          "summary_short": r["summary_short"]}
                         for r in timeline[:12]],
        })

    topics_out.sort(key=lambda t: (-t["counts"]["d7"], -t["top_score"]))
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "windows": list(WINDOWS),
        "min_mentions": MIN_MENTIONS,
        "span_days": span_days,               # 知识库覆盖的时间跨度
        "lifecycle_ready": span_days >= MIN_SPAN_DAYS,   # 生命周期判断是否可信
        "min_span_days": MIN_SPAN_DAYS,
        "topics": topics_out,
    }


def write(db: DB):
    os.makedirs(FEED_DIR, exist_ok=True)
    data = build(db)
    with open(os.path.join(FEED_DIR, "trends.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return data


if __name__ == "__main__":
    d = write(DB())
    print(f"话题趋势：{len(d['topics'])} 个话题（出现 >= {MIN_MENTIONS} 次）")
    print(f"知识库时间跨度：{d['span_days']} 天  "
          f"{'✅ 生命周期判断可信' if d['lifecycle_ready'] else f'⚠️ 不足 {MIN_SPAN_DAYS} 天，暂不下趋势结论'}\n")
    for t in d["topics"][:12]:
        c = t["counts"]
        print(f"  {t['phase']:<3} {t['topic']:<22} "
              f"7天 {c['d7']:>2} · 30天 {c['d30']:>2} · 90天 {c['d90']:>2}   {t['why']}")
