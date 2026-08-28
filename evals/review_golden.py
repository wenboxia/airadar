"""黄金集复核 CLI —— 让人工标注变成按几个键的事，而不是手改 JSONL。

用法：python3 evals/review_golden.py
  读 golden_set/golden_draft.jsonl，逐条问你三个问题，写出 golden_set/golden.jsonl。
  中途退出会保存进度，下次接着来。

判断标准只有一句话：**三个月后你还愿意在知识库里搜到它吗？**
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRAFT = os.path.join(ROOT, "evals", "golden_set", "golden_draft.jsonl")
OUT = os.path.join(ROOT, "evals", "golden_set", "golden.jsonl")

CATEGORIES = ["模型发布", "Agent 工程", "评测与基准", "上下文与记忆", "工程实践",
              "开源项目", "论文", "行业动态", "安全与对齐", "产品与商业"]

HELP = """
判断标准：三个月后你还愿意在知识库里搜到它吗？

  y = 收录     值得长期留存：一手发布 / 有方法论 / 有具体结论的研究
  n = 筛掉     纯商业动态 / 重复报道 / 泛泛而谈 / 与 AI Agent 领域无关
  s = 跳过     拿不准，这条不进黄金集
  q = 存盘退出 下次接着标
"""


def _load(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("//"):
                rows.append(json.loads(line))
    return rows


def main():
    draft = _load(DRAFT)
    if not draft:
        print(f"找不到草稿：{DRAFT}\n先跑 python3 evals/prelabel.py")
        return 1

    done = {r["url"]: r for r in _load(OUT)}
    todo = [r for r in draft if r["url"] not in done]
    if not todo:
        print(f"全部 {len(draft)} 条都已复核完毕 → {os.path.relpath(OUT, ROOT)}")
        print("接下来跑：python3 evals/run_eval.py")
        return 0

    print(HELP)
    print(f"待复核 {len(todo)} 条（已完成 {len(done)} 条）\n" + "─" * 62)

    for i, row in enumerate(todo, 1):
        pl = row.get("_pipeline", {})
        print(f"\n[{i}/{len(todo)}] {row['source']}（{row['tier']} 级）· 系统判断：{pl.get('status')} {pl.get('score')} 分")
        print(f"  标题：{row['title']}")
        if pl.get("summary_short"):
            print(f"  摘要：{pl['summary_short']}")
        print(f"  链接：{row['url']}")

        ans = ""
        while ans not in ("y", "n", "s", "q"):
            ans = input("\n  收录吗？[y/n/s/q] ").strip().lower()
            if ans == "?":
                print(HELP)
        if ans == "q":
            break
        if ans == "s":
            continue

        row["include"] = ans == "y"

        # 分类：只有收录的才需要（丢掉的东西分类没意义）
        if row["include"]:
            print("\n  分类：", "  ".join(f"{n+1}.{c}" for n, c in enumerate(CATEGORIES)))
            cur = row.get("category")
            default = CATEGORIES.index(cur) + 1 if cur in CATEGORIES else None
            pick = input(f"  选一个数字{f'（直接回车用系统给的「{cur}」）' if default else ''}：").strip()
            if pick.isdigit() and 1 <= int(pick) <= len(CATEGORIES):
                row["category"] = CATEGORIES[int(pick) - 1]
            elif default:
                row["category"] = cur
        else:
            row["category"] = ""

        note = input("  一句话理由（面试会被问到，用你自己的话）：").strip()
        row["note"] = note or "（未填写理由）"
        row.pop("_pipeline", None)
        done[row["url"]] = row

        # 每条都落盘，中断不丢进度
        with open(OUT, "w", encoding="utf-8") as f:
            for r in done.values():
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    if not done:
        print("\n本次没有标注任何条目，下次再来。")
        return 0
    inc = sum(1 for r in done.values() if r.get("include"))
    print(f"\n{'─' * 62}\n已标注 {len(done)} 条：收录 {inc} / 筛掉 {len(done) - inc}"
          f"（你的收录率 {inc / len(done):.0%}）")
    print(f"存到 {os.path.relpath(OUT, ROOT)}")
    if len(done) < len(draft):
        print(f"还剩 {len(draft) - len(done)} 条，再跑一次本命令即可接着标")
    else:
        print("全部完成！接下来跑：python3 evals/run_eval.py 看筛选准确率")
    return 0


if __name__ == "__main__":
    sys.exit(main())
