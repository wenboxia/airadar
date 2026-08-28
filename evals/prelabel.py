"""黄金集预标注：把 pipeline 的真实决策导出成待复核清单。

流程（人机协同，见 evals/golden_set/README.md）：
  1. Claude/pipeline 预标注 → 生成 golden_draft.jsonl（含 pipeline 的判断和理由）
  2. **主人逐条复核**：改 include / category，写自己的 note
  3. 复核后另存为 golden.jsonl，run_eval.py 即以它为标准答案算 precision/recall

用法：python3 evals/prelabel.py [--n 30]
"""
import argparse
import json
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "knowledge.db")
OUT = os.path.join(ROOT, "evals", "golden_set", "golden_draft.jsonl")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30, help="导出条数")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # 覆盖三种决策各取一部分：发布/待审/丢弃——边界样本才是评测的价值所在
    rows = []
    for status, n in (("published", args.n // 2),
                      ("review", args.n // 3),
                      ("discarded", args.n - args.n // 2 - args.n // 3)):
        rows += conn.execute(
            "SELECT * FROM items WHERE status=? ORDER BY score DESC LIMIT ?",
            (status, n)).fetchall()

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("// 黄金集待复核草稿。逐条检查 include / category，改完把本文件另存为 golden.jsonl\n")
        f.write("// include=true 的判断标准：三个月后你还愿意在知识库里搜到它吗？\n")
        for r in rows:
            detail = json.loads(r["score_detail"] or "{}")
            f.write(json.dumps({
                "url": r["url"],
                "title": r["title"],
                "source": r["source"],
                "tier": r["tier"],
                # 预标注：pipeline 没丢弃即视为建议收录，等人复核
                "include": r["status"] != "discarded",
                "category": r["category"],
                "note": "【待复核】pipeline 判断：" + str(detail.get("llm_reason", ""))[:120],
                "_pipeline": {"status": r["status"], "score": r["score"],
                              "summary_short": r["summary_short"]},
            }, ensure_ascii=False) + "\n")

    print(f"已导出 {len(rows)} 条到 {os.path.relpath(OUT, ROOT)}")
    print("请逐条复核 include / category / note，确认后另存为 golden.jsonl")


if __name__ == "__main__":
    main()
