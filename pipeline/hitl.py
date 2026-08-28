"""HITL 人机协同：把"系统不确定的内容"变成一张可勾选的审批卡片。

为什么用 GitHub Issue（decisions.md D6）：零基建的异步审批收件箱——自带通知、
自带审计记录、Actions 有权限读写。对标企业级 Agent 平台里"高风险操作强制人工审批"的简化同构。

闭环：
  pipeline 产出待审条目 → `hitl open` 开一张勾选清单 issue
  → 人在 issue 里勾 ✅/❌ → 下次运行 `hitl collect` 读回勾选结果
  → 写 data/feedback.jsonl → 更新条目状态 → 周期性汇总成信源 tier 调整建议（数据飞轮）

用法：
  python3 -m pipeline.hitl open      # 开审批 issue
  python3 -m pipeline.hitl collect   # 回收上一轮勾选
  python3 -m pipeline.hitl review    # 本地 CLI 审批（无 GitHub 时的兜底）
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

import requests

from .config import ROOT
from .db import DB
from .stages.publish import write_stats

API = "https://api.github.com"
LABEL = "airadar-approval"
FEEDBACK_PATH = os.path.join(ROOT, "data", "feedback.jsonl")
MARK = "<!-- airadar:item="


def _gh(method: str, path: str, **kw):
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        raise RuntimeError("缺少 GITHUB_TOKEN / GITHUB_REPOSITORY 环境变量")
    r = requests.request(
        method, f"{API}/repos/{repo}{path}",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json"},
        timeout=30, **kw)
    r.raise_for_status()
    return r.json() if r.text else {}


def _log_feedback(rows: list):
    os.makedirs(os.path.dirname(FEEDBACK_PATH), exist_ok=True)
    with open(FEEDBACK_PATH, "a", encoding="utf-8") as f:
        for row in rows:
            row["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _pending(db: DB) -> list:
    rows = db.conn.execute(
        "SELECT * FROM items WHERE status='review' ORDER BY score DESC").fetchall()
    return [dict(r) for r in rows]


def cmd_open():
    """把待审条目开成一张勾选清单 issue。已有未关闭的审批 issue 则不重复开。"""
    db = DB()
    items = _pending(db)
    if not items:
        print("没有待审条目，跳过")
        return

    existing = _gh("GET", f"/issues?state=open&labels={LABEL}")
    if existing:
        print(f"已有未处理的审批 issue #{existing[0]['number']}，本次不重复开")
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        "综合分落在 **50–75** 之间的内容——系统知道自己不确定，所以交给你。",
        "",
        "### 怎么做",
        "",
        "1. **勾上** = 收录进知识库　**留空** = 丢弃",
        "2. 全部看完后，**点页面底部的 `Close issue` 按钮**",
        "",
        "> ⚠️ **关闭 issue 才算提交。** 只勾选不关闭，系统不会回收你的决策"
        "——这一步相当于点「确认」。",
        "",
        "---",
        "",
    ]
    for it in items:
        detail = json.loads(it["score_detail"] or "{}")
        lines.append(f"- [ ] **[{it['tier']}]** [{it['title']}]({it['url']}) "
                     f"· `{it['score']}` · {it['source']}")
        if it["summary_short"]:
            lines.append(f"      {it['summary_short']}")
        if detail.get("llm_reason"):
            lines.append(f"      <sub>模型判断：{detail['llm_reason']}</sub>")
        lines.append(f"      {MARK}{it['id']} -->")
        lines.append("")

    issue = _gh("POST", "/issues", json={
        "title": f"待审批 · {today} · {len(items)} 条",
        "body": "\n".join(lines),
        "labels": [LABEL],
    })
    print(f"已开审批 issue #{issue['number']}（{len(items)} 条）")


def cmd_collect():
    """回收所有已关闭的审批 issue 里的勾选结果。"""
    db = DB()
    closed = _gh("GET", f"/issues?state=closed&labels={LABEL}&per_page=10")
    if not closed:
        print("没有已关闭的审批 issue")
        return

    total_ok = total_no = 0
    feedback = []
    for issue in closed:
        if any(l["name"] == "airadar-collected" for l in issue.get("labels", [])):
            continue
        body = issue.get("body") or ""
        # 逐条解析：勾选状态在条目行，id 在紧随其后的注释里
        for block in body.split("- [")[1:]:
            m = re.search(re.escape(MARK) + r"([0-9a-f]+) -->", block)
            if not m:
                continue
            item_id = m.group(1)
            approved = block[0].lower() == "x"
            db.conn.execute("UPDATE items SET status=? WHERE id=?",
                            ("published" if approved else "discarded", item_id))
            feedback.append({"item_id": item_id, "decision":
                             "approve" if approved else "reject",
                             "issue": issue["number"], "by": "human"})
            total_ok += approved
            total_no += not approved
        db.conn.commit()
        _gh("POST", f"/issues/{issue['number']}/labels",
            json={"labels": ["airadar-collected"]})

    if feedback:
        _log_feedback(feedback)
        write_stats_safe(db)
    print(f"已回收人工审批：通过 {total_ok} 条，否决 {total_no} 条 → data/feedback.jsonl")
    _source_hint(db)


def write_stats_safe(db: DB):
    from .config import load_config
    from .models import Context
    write_stats(Context(cfg=load_config(), llm=None, db=db, run_id="hitl"))


def _source_hint(db: DB):
    """数据飞轮：人的决策回流成信源信誉的修正建议。"""
    if not os.path.exists(FEEDBACK_PATH):
        return
    stats = {}
    with open(FEEDBACK_PATH, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            r = db.conn.execute("SELECT source, tier FROM items WHERE id=?",
                                (row["item_id"],)).fetchone()
            if not r:
                continue
            s = stats.setdefault(r["source"], {"tier": r["tier"], "ok": 0, "no": 0})
            s["ok" if row["decision"] == "approve" else "no"] += 1

    hints = []
    for name, s in stats.items():
        n = s["ok"] + s["no"]
        if n < 5:      # 样本太少不下结论
            continue
        rate = s["ok"] / n
        if rate < 0.3:
            hints.append(f"  ↓ {name}（{s['tier']} 级）近 {n} 条人工通过率仅 {rate:.0%}，建议降级")
        elif rate > 0.85 and s["tier"] not in ("S", "A"):
            hints.append(f"  ↑ {name}（{s['tier']} 级）近 {n} 条通过率 {rate:.0%}，建议升级")
    if hints:
        print("信源调整建议（数据飞轮）：")
        print("\n".join(hints))


def cmd_review():
    """本地 CLI 审批：没有 GitHub 时的兜底通道。"""
    db = DB()
    items = _pending(db)
    if not items:
        print("没有待审条目")
        return
    print(f"{len(items)} 条待审。y=收录 / n=丢弃 / s=跳过 / q=退出\n")
    feedback = []
    for i, it in enumerate(items, 1):
        detail = json.loads(it["score_detail"] or "{}")
        print(f"[{i}/{len(items)}] [{it['tier']}] {it['score']} · {it['source']}")
        print(f"  {it['title']}")
        print(f"  {it['summary_short']}")
        if detail.get("llm_reason"):
            print(f"  模型判断：{detail['llm_reason']}")
        print(f"  {it['url']}")
        ans = input("  > ").strip().lower()
        if ans == "q":
            break
        if ans == "s":
            print()
            continue
        approved = ans == "y"
        db.conn.execute("UPDATE items SET status=? WHERE id=?",
                        ("published" if approved else "discarded", it["id"]))
        feedback.append({"item_id": it["id"],
                         "decision": "approve" if approved else "reject",
                         "by": "human-cli"})
        print()
    db.conn.commit()
    if feedback:
        _log_feedback(feedback)
        write_stats_safe(db)
        print(f"已记录 {len(feedback)} 条决策 → data/feedback.jsonl")
        _source_hint(db)


COMMANDS = {"open": cmd_open, "collect": cmd_collect, "review": cmd_review}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "review"
    fn = COMMANDS.get(cmd)
    if not fn:
        print(f"用法：python3 -m pipeline.hitl [{'|'.join(COMMANDS)}]")
        sys.exit(1)
    try:
        fn()
    except RuntimeError as e:
        # 缺 GitHub 凭据不算失败——CI 里没配也不该让整条流水线红
        print(f"跳过（{e}）")
