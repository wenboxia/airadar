"""黄金集预标注：按分数段分层采样，导出待复核清单。

**为什么必须分层**（decisions.md D29）：首版按 status 采样，结果 80-90 分段标了 11 条，
而「自动丢弃」区间只标到 1 条——意味着"该丢的有没有丢掉"这个指标从未被真正测试。
低分段是唯一能测出误杀与漏杀的区域，必须强制配额。

流程（人机协同）：
  1. 本脚本按分数段分层导出 golden_draft.jsonl（自动跳过已标注的）
  2. 主人跑 `python3 evals/review_golden.py` 逐条判断
  3. `python3 evals/run_eval.py` 算三条路径各自的准确率

用法：python3 evals/prelabel.py [--n 25]
"""
import argparse
import json
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("AIRADAR_DB_PATH") or os.path.join(ROOT, "data", "knowledge.db")
OUT = os.path.join(ROOT, "evals", "golden_set", "golden_draft.jsonl")
DONE = os.path.join(ROOT, "evals", "golden_set", "golden.jsonl")

# 分数段配额权重。低分段权重高于其占比——因为那里样本稀缺但信息量最大：
# 「该丢的丢掉了吗」只能在低分段测出来
BANDS = [
    (90, 999, 1.0),
    (80, 90, 1.5),
    (70, 80, 1.5),
    (60, 70, 1.5),
    (50, 60, 2.0),
    (0, 50, 2.0),
]


def _labeled_urls() -> set:
    if not os.path.exists(DONE):
        return set()
    urls = set()
    with open(DONE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("//"):
                urls.add(json.loads(line)["url"])
    return urls


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=25, help="本批导出条数")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    labeled = _labeled_urls()
    # 按 auto_status 取（系统的自主判断），不是 status——后者可能已被人工改写
    all_rows = [dict(r) for r in conn.execute("SELECT * FROM items")
                if r["url"] not in labeled]
    if not all_rows:
        print("库里所有条目都已标注完。等 pipeline 再跑几天攒新内容。")
        return

    # 先按段分组，再按权重分配配额；某段不够就把余额让给其他段
    groups = {}
    for lo, hi, w in BANDS:
        groups[(lo, hi)] = sorted(
            [r for r in all_rows if lo <= (r["score"] or 0) < hi],
            key=lambda r: -(r["score"] or 0))

    weights = {(lo, hi): w for lo, hi, w in BANDS}
    total_w = sum(weights[k] for k in groups if groups[k])
    picked, leftover = [], 0
    for k in sorted(groups, reverse=True):
        avail = groups[k]
        if not avail:
            continue
        quota = round(args.n * weights[k] / total_w) + leftover
        take = avail[:quota]
        leftover = max(0, quota - len(take))
        picked.extend(take)
    picked = picked[: args.n]

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("// 黄金集待复核草稿。跑 python3 evals/review_golden.py 逐条判断。\n")
        f.write("// 判断标准：三个月后你还愿意在知识库里搜到它吗？\n")
        for r in picked:
            detail = json.loads(r["score_detail"] or "{}")
            cats = json.loads(r["categories"] or "null") or (
                [r["category"]] if r["category"] else [])
            f.write(json.dumps({
                "url": r["url"], "title": r["title"], "source": r["source"],
                "tier": r["tier"],
                "include": (r["auto_status"] or r["status"]) != "discarded",
                "categories": cats,
                "category": cats[0] if cats else "",
                "note": "【待复核】pipeline 判断：" + str(detail.get("llm_reason", ""))[:120],
                "_pipeline": {"auto_status": r["auto_status"] or r["status"],
                              "score": r["score"], "summary_short": r["summary_short"]},
            }, ensure_ascii=False) + "\n")

    print(f"已导出 {len(picked)} 条 → {os.path.relpath(OUT, ROOT)}（已跳过 {len(labeled)} 条已标注的）")
    print("\n本批的分数段分布：")
    for lo, hi, _ in BANDS:
        n = sum(1 for r in picked if lo <= (r["score"] or 0) < hi)
        rest = sum(1 for r in all_rows if lo <= (r["score"] or 0) < hi) - n
        name = f"{lo}-{hi}" if hi < 999 else f"{lo}+"
        print(f"  {name:<8} 本批 {n:>2} 条   （该段还剩 {rest} 条未标）")
    print(f"\n接下来跑：python3 evals/review_golden.py")


if __name__ == "__main__":
    main()
