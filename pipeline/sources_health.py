"""信源体检：报告每个信源的最新内容日期与存活状态。

为什么需要（decisions.md D22）：信源会悄悄死掉——SemiAnalysis 的公开 RSS 停更在
2025-09（内容转付费），但注册表里它看起来一切正常，每天照常被抓取、照常返回 0 条。
这种"沉默的失败"不会报错、不会告警，只会让信源池慢慢腐烂。

用法：python3 -m pipeline.sources_health
"""
import sys
from datetime import datetime, timedelta, timezone

import yaml

from .config import ROOT, load_config
from .models import Context
from .stages.fetch import SOURCES_PATH, _FETCHERS, parse_iso

# 停更阈值按信源性质分档：专家博客本来就发得少（Karpathy 可能几个月一篇），
# 用同一把尺子会把"低频但活着"误判成"死了"
STALE_DAYS = {"S": 30, "A": 180, "B": 30, "C": 120, "D": 120}
# 查询型信源（HN 搜索 / GitHub 搜索）没有"最新一篇"的概念——它们是按条件查，
# 不是按时间流。对这类只报"当前窗口能否查到内容"，不做停更判定。
QUERY_TYPES = {"hn", "github"}


def main():
    cfg = load_config()
    cfg.since_days = 3650          # 体检要看全量，不受日常时间窗限制
    ctx = Context(cfg=cfg, llm=None, db=None, run_id="health")
    sources = yaml.safe_load(open(SOURCES_PATH, encoding="utf-8"))["sources"]
    now = datetime.now(timezone.utc)
    rows, dead = [], 0

    for src in sources:
        if src.get("tier") == "X":
            continue
        fetcher = _FETCHERS.get(src.get("type"))
        if not fetcher:
            rows.append((src, None, "未知类型"))
            continue
        is_query = src.get("type") in QUERY_TYPES
        window = 7 if is_query else 3650
        try:
            items = fetcher(src, now - timedelta(days=window), ctx)
        except Exception as e:  # noqa: BLE001
            rows.append((src, None, f"抓取失败 {type(e).__name__}"))
            dead += 1
            continue
        if is_query:
            # 查询型：能查到东西就算健康
            ok = len(items) > 0
            rows.append((src, (f"{len(items)} 条/7天", "查询型"),
                         "正常" if ok else "查不到内容"))
            dead += not ok
            continue
        dates = [i.published_at for i in items if i.published_at]
        if not dates:
            rows.append((src, None, "无内容（feed 可能已失效）"))
            dead += 1
            continue
        latest = max(dates)
        d = parse_iso(latest)
        if not d:
            rows.append((src, None, "时间格式异常")); dead += 1; continue
        age = (now - d).days
        limit = STALE_DAYS.get(src["tier"], 90)
        status = f"停更 >{limit}天" if age > limit else "正常"
        if status != "正常":
            dead += 1
        rows.append((src, (latest[:10], age), status))

    print(f"{'等级':<4}{'信源':<26}{'最新内容':<13}{'距今':<8}状态")
    print("-" * 66)
    for src, info, status in sorted(rows, key=lambda r: "SABCD".find(r[0]["tier"])):
        latest, age = info if info else ("—", "—")
        flag = "⚠️ " if status != "正常" else "   "
        print(f"{src['tier']:<4}{src['name'][:24]:<26}{latest:<13}"
              f"{(str(age) + ' 天' if isinstance(age, int) else str(age)) if info else '—':<10}{flag}{status}")

    print(f"\n共 {len(rows)} 个信源，{dead} 个需要关注")
    if dead:
        print("处理建议：确认是真停更还是抓取方式变了；真死了就从注册表移除，别留着腐烂。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
