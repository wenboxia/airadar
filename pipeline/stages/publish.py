"""Stage 6: publish —— 写知识库 + 导出前端数据 + HITL 待审清单。

前端是纯静态站，消费 data/feed/*.json：
- latest.json  本次运行的发布条目（今日视图）
- week.json    近 7 天已发布（本周视图）
- pending.json HITL 待审队列（Week 2 起同步开 GitHub Issue 审批卡片）
- stats.json   运行统计（机制页展示：处理量/筛除率/降级次数……）
"""
import json
import os
from datetime import datetime, timezone

from ..config import ROOT
from ..models import Context
from .classify import CATEGORIES

MANIFEST = {
    "name": "publish", "version": "0.1.0",
    "input": "list[Item]", "output": "SQLite + data/feed/*.json",
    "eval_cases": "evals/run_eval.py 规则校验",
}

FEED_DIR = os.path.join(ROOT, "data", "feed")


def _item_view(d: dict) -> dict:
    """前端视图：去掉大字段（原文全文只留库里，feed 不带）。"""
    return {k: d[k] for k in
            ("id", "url", "title", "source", "tier", "published_at",
             "summary_short", "summary_long", "key_points", "topics",
             "category", "categories", "horizon", "score", "status", "extra")
            if k in d}


def _dump(name: str, obj):
    os.makedirs(FEED_DIR, exist_ok=True)
    with open(os.path.join(FEED_DIR, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def _source_registry() -> dict:
    """把 sources.yaml 的真实状态导出给前端。

    为什么不让前端硬编码：前端「机制」页曾写着"13 个分层信源"，而实际已经 19 个——
    文档与实现的偏差（decisions.md D21）会以各种形式反复出现。根治办法是
    **让展示层从唯一事实来源派生**，而不是靠人记得同步。
    """
    import yaml
    with open(os.path.join(ROOT, "pipeline", "sources.yaml"), encoding="utf-8") as f:
        sources = yaml.safe_load(f)["sources"]
    by_tier = {}
    for s in sources:
        by_tier.setdefault(s["tier"], []).append(s["name"])
    # total 只数在抓的；X 级是「评估过、决定不抓」，名单照样导出供机制页展示
    return {"total": sum(1 for s in sources if s["tier"] != "X"), "by_tier": by_tier}


def _archive_provenance(db) -> dict:
    """知识库里有多少条真的被人看过。三种背书必须分开数——合并成一个"人工认可 N 条"
    会夸大人的参与度：主动勾收和"抽查时看过没撤"完全不是一回事。"""
    approved = db.conn.execute(
        "SELECT COUNT(*) FROM items WHERE status='published' AND auto_status='review'"
    ).fetchone()[0]
    kept = db.conn.execute(
        "SELECT COUNT(*) FROM items WHERE status='published' "
        "AND json_extract(extra, '$.hitl.kept_at') IS NOT NULL").fetchone()[0]
    retracted = db.conn.execute(
        "SELECT COUNT(*) FROM items WHERE status='discarded' AND auto_status='published'"
    ).fetchone()[0]
    total = len(db.archive_items())
    return {"total": total, "human_approved": approved, "human_kept": kept,
            # 撤下的不在知识库里，但要露出来——它是这套机制在起作用的证据，藏起来就只剩报喜
            "human_retracted": retracted, "auto_only": total - approved - kept}


def write_stats(ctx: Context):
    """单独抽出来是因为要被调用两次：publish 阶段写一次（保证有文件），
    main.py 在 save_run 之后再写一次——否则 stats.json 永远少记当次运行。"""
    _dump("stats.json", {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totals": ctx.db.counts(),
        "sources": _source_registry(),
        "archive": _archive_provenance(ctx.db),
        # 筛选按钮的顺序跟类目表走，不按字母排——前端不再各抄一份类目表
        "categories": CATEGORIES,
        "runs": [{"run_id": r["run_id"], "started_at": r["started_at"],
                  "stats": r["stats"]} for r in ctx.db.all_runs()[:30]],
    })


def run(items: list, ctx: Context) -> list:
    # 全部条目入库（含 discarded——留审计记录，"筛掉了什么"也是数据）
    ctx.db.upsert_items(items, ctx.run_id)

    published = [it.to_dict() for it in items if it.status == "published"]
    pending = [it.to_dict() for it in items if it.status == "review"]
    published.sort(key=lambda d: -d["score"])
    pending.sort(key=lambda d: -d["score"])

    today = datetime.now(timezone.utc).date().isoformat()
    _dump("latest.json", {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": today, "run_id": ctx.run_id,
        "top": [_item_view(d) for d in published[:5]],
        "items": [_item_view(d) for d in published],
    })
    # 本周是主视图（D37：审批改周更后，内容呈现也按周看——参照的周刊没有一家日更）
    week = ctx.db.recent_items(days=7)
    # 精选不推已停抓信源的旧条目：改为 X 级之前自动发布的 arXiv 论文分数普遍 90+，
    # 不排除的话首页头条就是刚决定不再收的东西。历史记录照常保留在列表和知识库里
    retired = set(_source_registry()["by_tier"].get("X", []))
    picks = sorted((d for d in week if d["source"] not in retired),
                   key=lambda d: -(d["score"] or 0))[:5]
    _dump("week.json", {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "top": [_item_view(d) for d in picks],
        "items": [_item_view(d) for d in week],
    })
    _dump("pending.json", {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "items": [_item_view(d) for d in pending],
    })

    # 知识库：全部长期留存的内容，不受 7 天窗口限制（主人明确要的"长期沉淀"入口）
    _dump("archive.json", {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "items": [_item_view(d) for d in ctx.db.archive_items()],
    })

    counts = ctx.db.counts()
    write_stats(ctx)

    ctx.stats["publish"] = {"published": len(published), "pending": len(pending),
                            "db_totals": counts}
    print(f"  publish: 发布 {len(published)}，待审 {len(pending)}，"
          f"库内累计 {sum(counts.values())} 条")
    if pending:
        print(f"  [HITL] {len(pending)} 条待人工审核 → data/feed/pending.json"
              "（Week 2 起自动开 GitHub Issue 审批卡片）")
    return items
