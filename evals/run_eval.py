"""AIRadar 评测引擎 v1 —— 规则校验 + 黄金集对比。

用法：python3 evals/run_eval.py
每次改 prompt / 换模型 / 调阈值后必须跑；结果存 evals/results/{ts}.json，
跨版本可对比（回归测试思想）。Week 3 加 LLM-as-Judge 幻觉评测与多模型对比。
"""
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "knowledge.db")
FEED_DIR = os.path.join(ROOT, "data", "feed")
GOLDEN_PATH = os.path.join(ROOT, "evals", "golden_set", "golden.jsonl")
RESULTS_DIR = os.path.join(ROOT, "evals", "results")

VALID_TIERS = {"S", "A", "B", "C", "D", "X"}
VALID_STATUS = {"published", "review", "discarded", "new"}


def rule_checks(rows: list) -> dict:
    """确定性规则校验：不花钱、每次必跑、违规零容忍。"""
    problems = []
    ids = set()
    for r in rows:
        rid = r["id"]
        if rid in ids:
            problems.append(f"重复 id: {rid}")
        ids.add(rid)
        for f in ("url", "title", "source", "tier", "status"):
            if not r[f]:
                problems.append(f"{rid} 缺字段 {f}")
        if r["url"] and not re.match(r"https?://", r["url"]):
            problems.append(f"{rid} url 非法: {r['url'][:60]}")
        if r["tier"] not in VALID_TIERS:
            problems.append(f"{rid} tier 非法: {r['tier']}")
        if r["status"] not in VALID_STATUS:
            problems.append(f"{rid} status 非法: {r['status']}")
        if r["summary_short"] and len(r["summary_short"]) > 80:
            problems.append(f"{rid} 一句话摘要超长 ({len(r['summary_short'])})")
        if r["status"] == "published" and not r["summary_short"]:
            # 允许降级（notes 里有 no_summary 标记），否则算违规
            if "no_summary" not in (r["notes"] or ""):
                problems.append(f"{rid} 已发布但无摘要且无降级标记")
    # feed 文件完整性
    for name in ("latest.json", "week.json", "pending.json", "stats.json"):
        path = os.path.join(FEED_DIR, name)
        if not os.path.exists(path):
            problems.append(f"缺 feed 文件: {name}")
            continue
        try:
            json.load(open(path, encoding="utf-8"))
        except json.JSONDecodeError:
            problems.append(f"feed 文件损坏: {name}")
    return {"total_items": len(rows), "violations": len(problems),
            "problems": problems[:50]}


def golden_compare(rows: list) -> dict:
    """黄金集对比：人工标注 vs pipeline 决策 → 筛选 precision/recall + 分类准确率。"""
    if not os.path.exists(GOLDEN_PATH):
        return {"skipped": "黄金集不存在"}
    golden = []
    with open(GOLDEN_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("//"):
                golden.append(json.loads(line))
    if not golden:
        return {"skipped": "黄金集为空（待标注）"}

    by_id = {r["id"]: r for r in rows}
    by_url = {r["url"]: r for r in rows}
    tp = fp = fn = tn = 0
    cat_right = cat_total = 0
    overlap_sum = 0.0
    missing = no_auto = 0
    human_touched = 0
    pub_total = pub_right = dis_total = dis_right = rev_total = rev_right = 0
    route = {}
    missed = []
    for g in golden:
        r = by_id.get(g.get("id")) or by_url.get(g.get("url"))
        if not r:
            missing += 1
            continue
        # 关键：用 auto_status（系统自主判断），而不是 status（可能已被人工审批改写）。
        # 用 status 会让所有经过 HITL 的条目变成"人评人自己"，指标虚高（decisions.md D28）
        auto = r.get("auto_status")
        if not auto:
            no_auto += 1
            continue
        if auto != r["status"]:
            human_touched += 1

        # 三分类路由不能用二分类指标评价。系统有三种输出：
        #   published = "我认为该收"　discarded = "我认为该丢"　review = "我不确定，你来定"
        # 把 review 算进任何一边都会失真——算成"该收"会把谨慎当成错误，
        # 算成"该丢"会把求助当成拒绝。所以三条路径分开评（decisions.md D29）
        route[auto] = route.get(auto, 0) + 1
        if auto == "published":
            pub_total += 1
            pub_right += g["include"]
        elif auto == "discarded":
            dis_total += 1
            dis_right += not g["include"]
            if g["include"]:
                missed.append(g["title"][:60])   # 真正的漏网之鱼
        else:  # review
            rev_total += 1
            rev_right += g["include"]

        # 兼容口径：把 review 视作"未拒绝"，便于跟历史结果对比
        pipeline_include = auto != "discarded"
        if g["include"] and pipeline_include:
            tp += 1
        elif g["include"] and not pipeline_include:
            fn += 1
        elif not g["include"] and pipeline_include:
            fp += 1
        else:
            tn += 1
        gold_cats = g.get("categories") or ([g["category"]] if g.get("category") else [])
        if gold_cats:
            cat_total += 1
            try:
                pipe_cats = json.loads(r.get("categories") or "null") or []
            except (json.JSONDecodeError, TypeError):
                pipe_cats = []
            if not pipe_cats and r.get("category"):
                pipe_cats = [r["category"]]
            # 多标签下"准确"的定义：人工标的主分类被系统命中即算对。
            # 系统多标一个不算错（信息更全），漏掉主分类才算错。
            if gold_cats[0] in pipe_cats:
                cat_right += 1
            inter = len(set(gold_cats) & set(pipe_cats))
            union = len(set(gold_cats) | set(pipe_cats))
            overlap_sum += inter / union if union else 0
    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    return {"labeled": len(golden),
            "usable": len(golden) - missing - no_auto,   # 真正参与评测的条数
            "missing_in_db": missing,
            "no_auto_status": no_auto,                   # >0 说明有条目缺系统原判，评测被削弱
            "human_touched": human_touched,              # 其中被人工审批改写过 status 的条数
                                                          # （用 auto_status 后它们依然有效）
            "filter_precision": round(prec, 3) if prec is not None else None,
            "filter_recall": round(rec, 3) if rec is not None else None,
            "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
            "category_primary_hit": round(cat_right / cat_total, 3) if cat_total else None,
            "category_jaccard": round(overlap_sum / cat_total, 3) if cat_total else None,
            # 三条路径分别评价（这才是三分类路由的正确评法）
            "routing": {
                "auto_publish": {
                    "n": pub_total,
                    # 自动发布的里面人认可的比例——错发代价最高，这是最重要的指标
                    "precision": round(pub_right / pub_total, 3) if pub_total else None},
                "auto_discard": {
                    "n": dis_total,
                    # 自动丢弃的里面人也认为该丢的比例
                    "precision": round(dis_right / dis_total, 3) if dis_total else None,
                    "missed": missed},          # 被错杀的好内容，逐条列出
                "sent_to_human": {
                    "n": rev_total,
                    # 送审的里面人最终认可的比例——太低说明在浪费人的注意力
                    "hit_rate": round(rev_right / rev_total, 3) if rev_total else None},
                "route_distribution": route,
            }}


def main():
    if not os.path.exists(DB_PATH):
        print("知识库不存在，先跑 pipeline")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM items")]

    result = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rules": rule_checks(rows),
        "golden": golden_compare(rows),
    }

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(
        RESULTS_DIR,
        datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + ".json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    print(json.dumps(result, ensure_ascii=False, indent=1))
    print(f"\n结果已存 {os.path.relpath(out_path, ROOT)}")
    if result["rules"]["violations"]:
        print(f"⚠️  规则违规 {result['rules']['violations']} 处")
        sys.exit(2)
    print("✅ 规则校验全部通过")


if __name__ == "__main__":
    main()
