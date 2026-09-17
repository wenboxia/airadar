"""一次性工具：把黄金集 v2 的标注结果写进 evals/golden_set/golden.jsonl（D42）。

主人是边和 ChatGPT 语音聊天边标的：GPT 念资料、查原文，主人口头说收不收、为什么，
GPT 代填 Word 表。Word 里的【理由】是 GPT 润色过的，100 条里 94 条加了主人没说过的内容，
所以理由改从聊天记录里重新提取：

  第一步（workflow）：逐条提取主人原话、理由来源、GPT 的建议，并核对 Word 的收录 y/n
  第二步（workflow）：以主人原意为主体，补上 GPT 提到而主人没说到、又重要合理的点，每个补点记出处

用法：
  # 把两步 workflow 的结果合成公开证据文件（聊天记录全文只留本地）
  python3 tools/build_golden_v2.py evidence <提取结果.json> <合并结果.json>
  # 用证据文件 + Word 表 + 草稿，生成 golden.jsonl（100 条 v2 + 11 条 v1 复用）
  python3 tools/build_golden_v2.py build

黄金集不标分类（主人决定，分类准确率改用官网自带标签测），所以 categories 一律为空。
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GS = os.path.join(ROOT, "evals", "golden_set")
DOC = os.path.join(GS, "黄金集v2_标注表_已标注至100条.docx")
DRAFT = os.path.join(GS, "golden_draft.jsonl")
V1 = os.path.join(GS, "golden.deprecated-20260916.jsonl")
OUT = os.path.join(GS, "golden.jsonl")
EVIDENCE = os.path.join(GS, "golden_v2_extraction.json")
LABELED_AT = "2026-09-17"

# v1 里与 v2 不重复、不违背 v2 判断逻辑、信源仍在抓的 11 条（主人 09-17 逐条确认复用）。
# 排除的：与 v2 重复的 9 条（以 v2 为准）；arXiv 4 条（入口已停，D36）；
# 与 v2 判断冲突的 2 条——《Breaking Claude Code》v1 不收 vs v2 第 22 条同主题收录，
# HF 多向量嵌入训练教程 v1 收 vs v2 第 24 条"只是个教程"不收。
# 值是只修错字后的理由；v1 原文另存 note_v1_raw。
V1_REUSE = {
    "Funding better evaluations of AI’s impact on wellbeing":
        "公益活动，和 AI 进步无关",
    "How to evaluate LLMs before production":
        "为了让 LLM 进入生产环境之后依然表现良好，介绍了上线之前如何做评测",
    "Intelligent transcription with Gemini 3.5 Transcribe":
        "三个月后这个功能大概率又被迭代到别的程度了，语音转文本本身没那么重要",
    "OpenClaw went viral. Meet the maintainers building and securing it.":
        "我个人感觉讲的是大家使用 AI 工具过程中普遍存在、大家都知道的问题，"
        "就是自己有没有真正思考过，不值得保留在知识库中",
    "Better answers, broader thinking: What students gain from ChatGPT and critical-thinking training":
        "一项社会实验，没啥用，垃圾信息",
    "Expanding OpenAI’s presence in Brazil":
        "纯营销宣传",
    "Learning never stops: How AI makes learning continuous":
        "营销宣传",
    "SaladDay/pi-from-scratch":
        "本身的价值一般，但它牵连带出一个很重要的产品 pi agent（前一段时间很火），起到抛砖引玉的作用，"
        "不了解 pi agent 的人可以通过这个小项目进一步了解它",
    "fuxicodex/Fuxi":
        "感觉就是一个盗版仿造的 Claude Code，不具备创新性，也不具备牵连带性",
    "The turbulent AI era is here":
        "感觉就是政策评论类的，和 AI 进步无关",
    "FareedKhan-dev/kimi-k3-in-c":
        "技术声称明显夸张且可信度低",
}


def _jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip() and not l.startswith("//")]


def _lines(*texts) -> list:
    """从 "[1234] ..." 这类引用里取行号，作为证据定位。"""
    found = set()
    for t in texts:
        found.update(int(x) for x in re.findall(r"\[(\d{2,5})\]", t or ""))
    return sorted(found)


def assemble_evidence(extraction_path, merge_path):
    ext = {it["n"]: it for it in json.load(open(extraction_path, encoding="utf-8"))}
    merged = json.load(open(merge_path, encoding="utf-8"))
    merged = {it["n"]: it for it in (merged.get("items") if isinstance(merged, dict) else merged)}
    drafts = _jsonl(DRAFT)
    assert sorted(ext) == sorted(merged) == list(range(1, len(drafts) + 1)), "条目不全"

    out = []
    for n in range(1, len(drafts) + 1):
        e, m, d = ext[n], merged[n], drafts[n - 1]
        ev, mv = e.get("verify") or {}, m.get("verify") or {}
        points = mv.get("note_ai_points_fixed", m["note_ai_points"])
        out.append({
            "n": n, "url": d["url"], "title": d["title"], "source": d["source"],
            "decision": ev.get("decision_transcript_fixed") or e["decision_transcript"],
            "note": mv.get("note_fixed") or m["note"],
            "note_user": m["note_user"],
            "note_verbatim": e["note_verbatim"],
            "note_ai_points": points,
            "note_source": mv.get("note_source_fixed") or m["note_source"],
            "ai_recommended_first": m["ai_recommended_first"],
            "ai_recommendation": e["ai_recommendation"],
            "user_quotes": e["user_quotes"],
            "note_docx": e["docx_reason"],
            "docx_adds_content": e["docx_adds_content"],
            "evidence_lines": _lines(*e["user_quotes"], e["ai_reason_quote"],
                                     *(p["source"] for p in points)),
            "issues": e["issues"],
        })
    with open(EVIDENCE, "w", encoding="utf-8") as f:
        json.dump({
            "about": "黄金集 v2 每条理由的来源证据。行号指向主人本地保存的语音聊天记录"
                     "（含无关闲聊与本机路径，未公开）。生成方式见 tools/build_golden_v2.py。",
            "items": out,
        }, f, ensure_ascii=False, indent=1)
    print(f"证据文件：{os.path.relpath(EVIDENCE, ROOT)}（{len(out)} 条）")


def _docx_decisions():
    from docx import Document
    table = Document(DOC).tables[0]
    rows = [[c.text.strip() for c in r.cells] for r in table.rows[1:]]
    return [(r[1], r[8]) for r in rows]


def build():
    if any(True for _ in _jsonl(OUT)):
        sys.exit("golden.jsonl 里已经有标注了。黄金集只增不删，这个工具只用于首次写入。")
    drafts = _jsonl(DRAFT)
    evidence = json.load(open(EVIDENCE, encoding="utf-8"))["items"]
    decisions = _docx_decisions()
    assert len(drafts) == len(evidence) == len(decisions)

    records = []
    for d, e, (title, flag) in zip(drafts, evidence, decisions):
        # 三方必须对得上：草稿、证据、Word 表
        if title[:20] != d["title"][:20] or e["url"] != d["url"]:
            sys.exit(f"第 {e['n']} 条对不上：{title[:20]} / {d['title'][:20]}")
        if flag not in ("y", "n") or (flag == "y") != (e["decision"] == "y"):
            sys.exit(f"第 {e['n']} 条收录判断不一致：Word={flag} 聊天记录={e['decision']}")
        records.append({
            "url": d["url"], "title": d["title"], "source": d["source"], "tier": d["tier"],
            "include": flag == "y",
            "note": e["note"],
            "note_user": e["note_user"],
            "note_ai_points": e["note_ai_points"],
            "note_source": e["note_source"],
            "ai_recommended_first": e["ai_recommended_first"],
            "note_docx": e["note_docx"],
            "categories": [],
            "labeled_via": "voice-gpt-v2",
            "labeled_at": LABELED_AT,
            "evidence": f"golden_v2_extraction.json#n={e['n']}",
        })

    seen = {r["url"] for r in records}
    reused = []
    for g in _jsonl(V1):
        key = next((k for k in V1_REUSE if g["title"].startswith(k)), None)
        if not key:
            continue
        assert g["url"] not in seen, f"v1 复用条目与 v2 重复：{g['title']}"
        reused.append({
            "url": g["url"], "title": g["title"], "source": g["source"], "tier": g["tier"],
            "include": g["include"],
            "note": V1_REUSE[key],
            "note_v1_raw": g.get("note", ""),
            "note_source": "本人（v1 手写）",
            "v1_categories": g.get("categories") or [],
            "categories": [],
            "labeled_via": "v1-reused",
            "labeled_at": LABELED_AT,
        })
    if len(reused) != len(V1_REUSE):
        sys.exit(f"v1 复用应为 {len(V1_REUSE)} 条，实际找到 {len(reused)} 条")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("// 黄金集 v2（2026-09-16 起，标准见 README.md）。旧版见 golden.deprecated-20260916.jsonl\n")
        f.write(f"// {len(records)} 条语音标注（理由来源见 golden_v2_extraction.json）"
                f" + {len(reused)} 条 v1 复用；不标分类（D42）\n")
        for r in records + reused:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    inc = sum(r["include"] for r in records + reused)
    print(f"已写入 {len(records) + len(reused)} 条（收录 {inc}）→ {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["evidence"] and len(sys.argv) == 4:
        assemble_evidence(sys.argv[2], sys.argv[3])
    elif sys.argv[1:] == ["build"]:
        build()
    else:
        sys.exit(__doc__)
