"""把填好的 Word 标注表写回黄金集。

用法：python3 evals/import_golden_docx.py [表格路径]
  默认读 evals/golden_set/黄金集v2_标注表.docx，按「序号」对回 golden_draft.jsonl，
  把填了【收录】的行追加进 golden.jsonl（已在里面的 URL 跳过，可以分几次填、几次导）。

只认最右边三列：【收录】y/n、【分类】（顿号或逗号分隔）、【理由】。
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pipeline.stages.classify import CATEGORIES  # noqa: E402

GS = os.path.join(ROOT, "evals", "golden_set")
DRAFT = os.path.join(GS, "golden_draft.jsonl")
OUT = os.path.join(GS, "golden.jsonl")
DOC = os.path.join(GS, "黄金集v2_标注表.docx")

YES = {"y", "yes", "是", "收", "收录", "✓", "√"}
NO = {"n", "no", "否", "不", "不收", "✗", "×"}


def _read_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip() and not l.startswith("//")]


def parse_rows(doc_path):
    """返回 [(序号, 标题, 收录原文, 分类原文, 理由)]。"""
    from docx import Document
    table = Document(doc_path).tables[0]
    out = []
    for row in table.rows[1:]:
        c = [cell.text.strip() for cell in row.cells]
        if not c[0].isdigit():
            continue
        out.append((int(c[0]), c[1], c[8], c[9], c[10]))
    return out


def to_record(draft, include_raw, cats_raw, note):
    """单行 → 黄金集记录；返回 (记录或 None, 问题描述或 None)。"""
    flag = include_raw.strip().lower()
    if not flag:
        return None, None                       # 还没填，不算错
    if flag in YES:
        include = True
    elif flag in NO:
        include = False
    else:
        return None, f"【收录】填的是「{include_raw}」，只认 y / n"
    cats = [x.strip() for x in re.split(r"[、,，/;；\s]+", cats_raw) if x.strip()]
    bad = [x for x in cats if x not in CATEGORIES]
    if bad:
        return None, f"分类不在列表里：{'、'.join(bad)}"
    if include and not cats:
        return None, "收录了但没填分类"
    return {
        "url": draft["url"], "title": draft["title"], "source": draft["source"],
        "tier": draft["tier"], "include": include,
        "categories": cats[:3], "category": cats[0] if cats else "",
        "note": note, "labeled_via": "docx-v2",
    }, None


def main():
    doc = sys.argv[1] if len(sys.argv) > 1 else DOC
    drafts = _read_jsonl(DRAFT)
    done = {r["url"] for r in _read_jsonl(OUT)}
    added, problems, blank, skipped = [], [], 0, 0

    for n, title, inc, cats, note in parse_rows(doc):
        if not 1 <= n <= len(drafts):
            problems.append(f"第 {n} 行：序号超出草稿范围")
            continue
        d = drafts[n - 1]
        # 草稿重新导出过的话序号会错位，宁可停下也不能张冠李戴
        if title and title[:20] != d["title"][:20]:
            sys.exit(f"第 {n} 行标题和草稿对不上（表里「{title[:20]}」，草稿「{d['title'][:20]}」）。"
                     f"草稿可能在生成表格后被重新导出过，请重新生成表格。")
        rec, err = to_record(d, inc, cats, note)
        if err:
            problems.append(f"第 {n} 行：{err}")
        elif rec is None:
            blank += 1
        elif rec["url"] in done:
            skipped += 1
        else:
            if not rec["note"]:
                problems.append(f"第 {n} 行：没写理由（已导入，建议补上）")
            added.append(rec)
            done.add(rec["url"])

    if added:
        with open(OUT, "a", encoding="utf-8") as f:
            for r in added:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    total = len(_read_jsonl(OUT))
    print(f"本次导入 {len(added)} 条 · 未填 {blank} · 已存在跳过 {skipped}"
          f" · 黄金集现有 {total} 条")
    for p in problems:
        print("  ⚠️ " + p)
    if added:
        print("\n接下来跑：python3 evals/run_eval.py")


if __name__ == "__main__":
    main()
