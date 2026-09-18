"""HITL 人机协同：把"系统不确定的内容"变成一张可勾选的审批卡片。

为什么用 GitHub Issue（decisions.md D6）：零基建的异步审批收件箱——自带通知、
自带审计记录、Actions 有权限读写。对标企业级 Agent 平台里"高风险操作强制人工审批"的简化同构。

节奏（D37）：抓取每天跑，**审批每周一次**，每张单子 12 条分三块（D44）：
待审候选 7（按当前打分标准从高到低）、自动发布抽查 4（均匀随机，勾=撤下）、旧池捞回 1。
日更审批从没真正发生过：08-29 开的单子一直没关，而 open 遇到未关闭的单子就不再开新的，
之后 18 天送审的内容全部沉在库里；累计送审 239 条，人工审完 18 条。

闭环：
  pipeline 产出待审条目 → `hitl open` 开一张三块勾选清单 issue，同一份数据写成 data/feed/review.json
  → 人在 issue 里勾 ✅/❌ → 下次运行 `hitl collect` 读回勾选结果
  → 写 data/feedback.jsonl → 更新条目状态 → 按信源汇总边缘区通过率，给「送审门槛」建议
    （只调边缘区处理策略，绝不改 tier，见 D27 与 _source_hint）

用法：
  python3 -m pipeline.hitl open      # 开审批 issue
  python3 -m pipeline.hitl collect   # 回收上一轮勾选
  python3 -m pipeline.hitl review    # 本地 CLI 审批（无 GitHub 时的兜底）
"""
import json
import os
import random
import re
import sys
from datetime import datetime, timezone

import requests

from .config import ROOT, load_config
from .db import DB
from .stages import triage
from .stages.publish import FEED_DIR, write_stats

API = "https://api.github.com"
LABEL = "airadar-approval"
EXPIRED = "airadar-expired"
REVIEW_BATCH = 12      # 每周给人看的条数——总量不动，只改怎么分配（见 LANE_QUOTA）
# 三块清单的名额。为什么不是把 12 加到 30：Anthropic 的遥测显示逐条批准看得越多越不上心
# （权限弹窗通过率 93%），加量只会让每一条都更潦草。所以在同样的 12 条里换分配方式
LANE_QUOTA = {"queue": 7, "audit": 4, "recall": 1}
AUDIT_WINDOW_DAYS = 14   # 抽查只看最近两周自动发布的，太老的撤下也没意义
EXPIRE_DAYS = 6        # 上一张单子到下次开单时还开着，就按过期处理（留一天余量给定时任务的时间抖动）
FEEDBACK_PATH = os.path.join(ROOT, "data", "feedback.jsonl")
MARK = "<!-- airadar:item="
# lane 可选：老单子（Issue #3 还活着）没有这一段，按 queue 处理
_MARK_RE = re.compile(re.escape(MARK) + r"([0-9a-f]+)(?:;lane=([a-z]+))? -->")


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


def _extra(row) -> dict:
    try:
        return json.loads(row.get("extra") or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def _hitl(row) -> dict:
    return _extra(row).get("hitl") or {}


def _current_score(row) -> float:
    """排队看的是最新一次打分。没重打过就用原始分。"""
    rs = _extra(row).get("rescore") or {}
    return float(rs.get("score", row.get("score") or 0))


def _retired_sources() -> set:
    """已停抓（X 级）的信源。和本周精选同一条规则：刚决定不再收的东西不该继续占人的注意力（D36）。"""
    import yaml
    from .stages.fetch import SOURCES_PATH
    with open(SOURCES_PATH, encoding="utf-8") as f:
        return {s["name"] for s in yaml.safe_load(f)["sources"] if s.get("tier") == "X"}


def _shelved_reason(row, cfg, retired: set = None) -> str:
    """这条为什么不进本周队列。返回空串表示它照常排队。

    为什么要有这个：每周新增约 95 条待审，而人每周只看 12 条——队列必然无限涨。
    按 Meta 的经验（放弃按时间顺序审、按优先级排、容量以外不假装会审），
    与其让旧条目永远排在队尾假装还会被审，不如明确让它们出队、靠"捞回"给第二次机会。
    """
    # ⓪ 来自已停抓的信源：比如按提交时间抓的 arXiv，D36 已经认定那个入口本身就是错的
    if retired and row.get("source") in retired:
        return "retired"
    # ① 时效类过了保质期：沿用 D30 的同一条规则和同一个天数——
    #    两周前的时效新闻就算现在审了也没人看了；长期价值类不受此限
    if row.get("horizon") == "short" and row.get("published_at") and \
            _age_days(row["published_at"]) > cfg.short_ttl_days:
        return "stale"
    # ② 新标准下不达标。只认当前 prompt 版本的结论，换版本重打分前旧结论自动失效
    rs = _extra(row).get("rescore") or {}
    if rs.get("prompt") == triage.PROMPT_VERSION and rs.get("verdict") == "discarded":
        return "below_bar"
    return ""


def _apply_source_quota(rows: list, quota: int) -> list:
    """一张单子里同一信源最多几条。放在队列层而不是抓取层：
    抓取层限量会直接丢数据、知识库和趋势跟着受影响；队列层只影响"本周谁占人的注意力"，可逆。"""
    seen, out = {}, []
    for r in rows:
        s = r.get("source") or ""
        if seen.get(s, 0) >= quota:
            continue
        seen[s] = seen.get(s, 0) + 1
        out.append(r)
    return out


def _pending(db: DB, limit: int = 0, cfg=None) -> list:
    """本周真正给人看的队列。被规则挡下的不丢、也不改状态，进捞回池（见 _excluded）。"""
    cfg = cfg or load_config()
    retired = _retired_sources()
    rows = [dict(r) for r in db.conn.execute("SELECT * FROM items WHERE status='review'")]
    live = [r for r in rows if not _shelved_reason(r, cfg, retired)]
    live.sort(key=lambda r: -_current_score(r))
    live = _apply_source_quota(live, cfg.review_source_quota)
    return live[:limit] if limit else live


def _excluded(db: DB, cfg=None) -> list:
    """被规则挡在队列外的池子。它们仍是 status='review'，只是不打扰人。"""
    cfg = cfg or load_config()
    retired = _retired_sources()
    out = []
    for r in db.conn.execute("SELECT * FROM items WHERE status='review'"):
        r = dict(r)
        reason = _shelved_reason(r, cfg, retired)
        if reason:
            out.append((r, reason))
    return out


def _recall_pick(db: DB, n: int, cfg=None) -> list:
    """二次机会（照搬 HN 的 second-chance pool）：被挡下的里面分最高的先给一次曝光。
    人留空就是"确认不要"，永久出队，下周轮到下一条。"""
    # 停抓信源的不给第二次机会——不是没轮到，是入口本身被否定了
    pool = [r for r, why in _excluded(db, cfg)
            if why != "retired" and "recall_declined_at" not in _hitl(r)]
    pool.sort(key=lambda r: -_current_score(r))
    return pool[:n]


def _audit_sample(db: DB, n: int, week_key: int, cfg=None) -> list:
    """自动发布里的抽样复审。必须是**均匀随机**，不能按分数分层——
    只有均匀抽样出来的撤下率，才能和黄金集离线重放里「当前这套标准」的自动发布认同率直接对照
    （D35 的教训）：triage-0.3.0 + deepseek-flash 为 73%（n=26，docs/eval_report.md）。
    62% 是旧标准的原判，不是同一个对象；换标准或换模型后基线要跟着换。

    这同时是全项目唯一一个**无偏**的人工反馈来源：审批队列只覆盖中间分数段（D27），
    高分段从来没人看过。撤下率补上的正是那一段。
    """
    cfg = cfg or load_config()
    rows = [dict(r) for r in db.conn.execute(
        "SELECT * FROM items WHERE status='published' AND auto_status='published' "
        "AND published_at >= datetime('now', ?) ORDER BY id",
        (f"-{AUDIT_WINDOW_DAYS} days",))]
    rows = [r for r in rows if not ({"kept_at", "retracted_at"} & set(_hitl(r)))]
    # 只抽当前这版打分标准自动发布的：撤下率测的是"现在这套标准"，混进旧标准的样本就说不清在测谁
    rows = [r for r in rows
            if json.loads(r.get("score_detail") or "{}").get("prompt") == triage.PROMPT_VERSION]
    random.Random(week_key).shuffle(rows)   # 同一周重开单子抽到同一批，跨周自动换批
    return rows[:n]


def _week_key() -> int:
    return int(datetime.now(timezone.utc).strftime("%Y%W"))


def _age_days(iso: str) -> float:
    d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - d).total_seconds() / 86400


def _expire(issue: dict, n_new: int):
    """关掉没人处理的旧单子。**先打过期标签再关**：collect 对过期单子只认勾选的，
    没勾的保持待审——否则「没勾」会被当成「否决」，等于替人把整张单子判死。"""
    num = issue["number"]
    _gh("POST", f"/issues/{num}/labels", json={"labels": [EXPIRED]})
    _gh("POST", f"/issues/{num}/comments", json={"body": (
        f"这张单子开了 {_age_days(issue['created_at']):.0f} 天没有关闭，按过期处理。\n\n"
        "- 已勾选的条目仍会按「通过」回收\n"
        "- **没勾选的条目不会被当成否决**，保持待审，重新参与排序\n\n"
        f"本周新单子共 {n_new} 条（待审候选 + 自动发布抽查 + 旧池捞回）。")})
    _gh("PATCH", f"/issues/{num}", json={"state": "closed", "state_reason": "not_planned"})
    print(f"旧审批 issue #{num} 已按过期关闭")


def _entry(it, lane: str, extra_line: str = "") -> list:
    detail = json.loads(it["score_detail"] or "{}")
    prefix = "🔻 撤下 · " if lane == "audit" else ""
    lines = [f"- [ ] {prefix}**[{it['tier']}]** [{it['title']}]({it['url']}) "
             f"· `{it['score']}` · {it['source']}" + extra_line]
    if it["summary_short"]:
        lines.append(f"      {it['summary_short']}")
    if detail.get("llm_reason"):
        lines.append(f"      <sub>模型判断：{detail['llm_reason']}</sub>")
    lines.append(f"      {MARK}{it['id']};lane={lane} -->")
    lines.append("")
    return lines


def cmd_open():
    """开一张三块清单的 issue：待审候选、自动发布抽查、旧池捞回。

    为什么是三块而不是一张纯待审清单：人逐条点批准会退化成橡皮图章（Anthropic 遥测：
    权限弹窗通过率 93%，"批准看得越多越不上心"）。改成 Hacker News 版主那种"只补两侧"——
    正向挑一点、反向撤一点、漏掉的捞一点，人的注意力花在纠错上，而不是盖章上。
    """
    db = DB()
    cfg = load_config()
    queue = _pending(db, LANE_QUOTA["queue"], cfg)
    audit = _audit_sample(db, LANE_QUOTA["audit"], _week_key(), cfg)
    recall = _recall_pick(db, LANE_QUOTA["recall"], cfg)
    if not (queue or audit or recall):
        print("没有待审条目，跳过")
        return
    backlog = db.conn.execute("SELECT COUNT(*) FROM items WHERE status='review'").fetchone()[0]
    shelved = len(_excluded(db, cfg))

    for issue in _gh("GET", f"/issues?state=open&labels={LABEL}"):
        if _age_days(issue["created_at"]) < EXPIRE_DAYS:
            print(f"已有未处理的审批 issue #{issue['number']}，本次不重复开")
            return
        _expire(issue, len(queue) + len(audit) + len(recall))

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # 空的那一块不出现：抽查只抽当前标准自动发布的，刚换标准的头几天可能一条都没有
    blocks = [
        ("queue", queue, f"### 一 · 待审候选 —— 勾上 = 收录进知识库（{len(queue)} 条）",
         ["系统当初拿不准、交给你定的内容，按最新一版打分标准从高到低排。"]),
        ("audit", audit, f"### 二 · 自动发布抽查 —— 勾上 = **撤下**（{len(audit)} 条）",
         ["> 这几条是系统自己发的，没经过你。**勾上 = 这条不该发，会从知识库撤下**；",
          "> 留空 = 你认可它留着。抽样是均匀随机的（本周种子 "
          f"{_week_key()}），撤下可逆，记录在 feedback.jsonl。"]),
        ("recall", recall, f"### 三 · 旧池捞回 —— 勾上 = 收录（{len(recall)} 条）",
         ["> 这条按时效或新标准已经退出队列，给它第二次机会。",
          "> **留空 = 确认不要，永久出队**，以后不再打扰你。"]),
    ]
    blocks = [b for b in blocks if b[1]]
    lines = [
        f"本周 {len(blocks)} 件事，**每块的勾选含义不一样，勾之前先看小标题**。",
        "",
        "> ⚠️ **关闭 issue 才算提交。** 只勾选不关闭，系统不会回收你的决策。",
        "",
        "---",
        "",
    ]
    retired = _retired_sources()
    for lane, rows, title, intro in blocks:
        lines += [title, ""] + intro + [""]
        for it in rows:
            tail = f" · {_shelved_reason(it, cfg, retired)}" if lane == "recall" else ""
            lines += _entry(it, lane, tail)

    lines += [
        "---",
        "",
        f"待审队列共 {backlog} 条，其中 {shelved} 条已出队（时效已过、新标准不达标，或信源已停抓）——"
        "前两类会轮流出现在「旧池捞回」里，停抓信源的不再打扰你。**容量以外的条目不会假装还会被审。**",
    ]

    issue = _gh("POST", "/issues", json={
        "title": f"本周 · {today} · 待审 {len(queue)} + 抽查 {len(audit)} + 捞回 {len(recall)}",
        "body": "\n".join(lines),
        "labels": [LABEL],
    })
    _write_review_feed(issue, {"queue": queue, "audit": audit, "recall": recall},
                       backlog, shelved, cfg)
    print(f"已开审批 issue #{issue['number']}"
          f"（待审 {len(queue)} / 抽查 {len(audit)} / 捞回 {len(recall)}）")


REVIEW_FEED = "review.json"
REVIEW_DIR = FEED_DIR        # 测试里 patch 成临时目录，免得把假单子写进真实的 data/feed


def _write_json(name: str, obj):
    os.makedirs(REVIEW_DIR, exist_ok=True)
    with open(os.path.join(REVIEW_DIR, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def _review_view(it: dict, lane: str, cfg) -> dict:
    return {"id": it["id"], "url": it["url"], "title": it["title"], "source": it["source"],
            "tier": it["tier"], "score": it["score"], "current_score": _current_score(it),
            "summary_short": it.get("summary_short") or "", "lane": lane,
            "reason": _shelved_reason(it, cfg, _retired_sources()) if lane == "recall" else ""}


def _write_review_feed(issue: dict, lanes: dict, backlog: int, shelved: int, cfg):
    """把这张单子原样写给网站。网站和 GitHub 读的是同一份数据，所以两边永远一致——
    以前网站的「待审」读的是"今天新送审的"，跟人周一真正要审的不是一回事，还显示成同一个名字。"""
    _write_json(REVIEW_FEED, {
        "status": "open", "issue": issue.get("number"), "url": issue.get("html_url", ""),
        "opened_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lanes": {lane: [_review_view(it, lane, cfg) for it in rows]
                  for lane, rows in lanes.items()},
        "queue_total": backlog, "shelved": shelved,
    })


def _mark_review_feed_collected(numbers: set):
    path = os.path.join(REVIEW_DIR, REVIEW_FEED)
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        feed = json.load(f)
    if feed.get("issue") in numbers and feed.get("status") == "open":
        feed["status"] = "collected"
        feed["collected_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _write_json(REVIEW_FEED, feed)


def _mark_hitl(db: DB, item_id: str, key: str, now: str):
    """只往 extra.hitl 里记一笔，不动状态。"""
    row = db.conn.execute("SELECT extra FROM items WHERE id=?", (item_id,)).fetchone()
    extra = json.loads((row["extra"] if row else None) or "{}")
    extra.setdefault("hitl", {})[key] = now
    db.conn.execute("UPDATE items SET extra=? WHERE id=?",
                    (json.dumps(extra, ensure_ascii=False), item_id))


def _apply(db: DB, lane: str, item_id: str, checked: bool, issue_no: int, now: str) -> dict:
    """按 lane 决定勾选的含义。三个 lane 都只写 status 和 extra，**绝不碰 auto_status**（D28）。"""
    if lane == "audit":
        if checked:
            # 撤下 = status 改 discarded 而 auto_status 仍是 published。
            # 这一格本来就是空的，不需要新状态：两个字段合起来已经能区分"系统丢的"和"人撤的"
            db.conn.execute("UPDATE items SET status='discarded' WHERE id=?", (item_id,))
            _mark_hitl(db, item_id, "retracted_at", now)
            return {"decision": "retract", "lane": lane}
        _mark_hitl(db, item_id, "kept_at", now)
        return {"decision": "keep", "lane": lane}
    if checked:
        db.conn.execute("UPDATE items SET status='published' WHERE id=?", (item_id,))
        return {"decision": "approve", "lane": lane}
    if lane == "recall":
        # 捞回里留空 = 确认不要，永久出队；但不改 status——它没被人否决，只是不再打扰人
        _mark_hitl(db, item_id, "recall_declined_at", now)
        return {"decision": "reject", "lane": lane}
    db.conn.execute("UPDATE items SET status='discarded' WHERE id=?", (item_id,))
    return {"decision": "reject", "lane": lane}


def cmd_collect(dry_run: bool = False):
    """回收所有已关闭的审批 issue 里的勾选结果。"""
    db = DB()
    closed = _gh("GET", f"/issues?state=closed&labels={LABEL}&per_page=10")
    if not closed:
        print("没有已关闭的审批 issue")
        return

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    counts = {}
    feedback = []
    collected = set()
    for issue in closed:
        labels = {l["name"] for l in issue.get("labels", [])}
        if "airadar-collected" in labels:
            continue
        expired = EXPIRED in labels
        body = issue.get("body") or ""
        # 逐条解析：勾选状态在条目行，id 和 lane 在紧随其后的注释里
        rows = []
        for block in body.split("- [")[1:]:
            m = _MARK_RE.search(block)
            if m:
                rows.append((m.group(1), m.group(2) or "queue", block[0].lower() == "x"))

        audit = [r for r in rows if r[1] == "audit"]
        # 至少 3 条才谈得上"整列勾选"：抽查只有一两条时，全勾很可能是真的都该撤
        mass_check = len(audit) >= 3 and sum(c for _, _, c in audit) > len(audit) / 2
        if mass_check:
            # 整列勾选多半是手滑（另两块的肌肉记忆是"勾好的"，这块勾的是"撤下"）。
            # 本次不撤任何一条，也不写 kept_at——它们下周会被重新抽到，机制自愈
            print(f"  [warn] issue #{issue['number']} 抽查区勾选过半，按误操作处理，本次不撤下")
            if not dry_run:
                _gh("POST", f"/issues/{issue['number']}/comments", json={"body": (
                    "抽查区勾中超过一半，按误操作处理：**本次没有撤下任何条目**。\n\n"
                    "这一块的勾选含义和另外两块相反（勾 = 撤下）。这几条下周会重新出现。")})

        for item_id, lane, checked in rows:
            if expired and not checked:
                continue    # 过期单子里没勾的 = 人没看过，既不是否决、也不是认可
            if mass_check and lane == "audit":
                continue
            if dry_run:
                print(f"  [dry] {lane} {item_id} {'勾选' if checked else '留空'}")
                counts[lane] = counts.get(lane, 0) + 1
                continue
            fb = _apply(db, lane, item_id, checked, issue["number"], now)
            fb.update({"item_id": item_id, "issue": issue["number"], "by": "human"})
            feedback.append(fb)
            key = f"{lane}:{fb['decision']}"
            counts[key] = counts.get(key, 0) + 1
        if dry_run:
            continue
        db.conn.commit()
        _gh("POST", f"/issues/{issue['number']}/labels",
            json={"labels": ["airadar-collected"]})
        collected.add(issue["number"])

    if dry_run:
        print("dry-run：没有写库，也没有改 issue 标签")
        return
    if collected:
        _mark_review_feed_collected(collected)
    if feedback:
        _log_feedback(feedback)
        write_stats_safe(db)
    print("已回收人工审批：" + "，".join(f"{k} {v}" for k, v in sorted(counts.items()))
          + " → data/feedback.jsonl")
    _source_hint(db)


def write_stats_safe(db: DB):
    from .config import load_config
    from .models import Context
    write_stats(Context(cfg=load_config(), llm=None, db=db, run_id="hitl"))


def _source_hint(db: DB):
    """数据飞轮：人的决策回流成信源处理策略的修正建议。

    **关键的统计陷阱**（decisions.md D27）：人工反馈只存在于**中间区**（50-75 分）
    ——高分内容自动发布了、低分内容直接丢了，都没人打过分。所以这里算出来的
    通过率是**条件概率**：P(人工认可 | 这条落在边缘区)，而不是这个信源的整体质量。

    实测例子：OpenAI News 边缘区通过率 0/4，但它同时有多条高分内容被自动发布且
    主人在黄金集里认可。真实含义不是"OpenAI 不可信"，而是
    **"OpenAI 的边缘内容是营销稿"**——它的好东西都在高分区，边缘区全是软文。

    因此本函数只给「边缘区处理策略」的建议，**绝不建议改 tier**——
    改 tier 会连带影响该信源的高分内容，那是用有偏样本去推翻无偏结论。
    """
    if not os.path.exists(FEEDBACK_PATH):
        return
    stats = {}
    with open(FEEDBACK_PATH, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            # 只吃边缘区那条通道的反馈（lane 缺省的是历史记录，当时只有这一种）。
            # 抽查（audit）是在高分段均匀抽样的，采样分布完全不同：混进来会让
            # "边缘区通过率"这个条件概率变成一个看起来更有说服力的错误数字，
            # 而这个函数的全部克制（绝不建议改 tier）正是建立在那条前提上（D27）
            if row.get("lane", "queue") != "queue":
                continue
            r = db.conn.execute("SELECT source, tier FROM items WHERE id=?",
                                (row["item_id"],)).fetchone()
            if not r:
                continue
            s = stats.setdefault(r["source"], {"tier": r["tier"], "ok": 0, "no": 0})
            s["ok" if row["decision"] == "approve" else "no"] += 1

    hints = []
    for name, s in stats.items():
        n = s["ok"] + s["no"]
        if n < 8:      # 样本太少不下结论（边缘区数据本就稀疏，门槛要更高）
            continue
        rate = s["ok"] / n
        if rate < 0.2:
            hints.append(
                f"  ⤵ {name}（{s['tier']} 级）边缘区 {n} 条只通过 {rate:.0%}——"
                f"它的边缘内容多半没价值，可考虑对该信源提高送审门槛，少占人工注意力。"
                f"\n     （注意：这不代表该信源整体质量差，它的高分内容未参与此统计）")
        elif rate > 0.8:
            hints.append(
                f"  ⤴ {name}（{s['tier']} 级）边缘区 {n} 条通过 {rate:.0%}——"
                f"边缘内容质量意外地好，可考虑降低其送审门槛让更多内容直接发布。")
    if hints:
        print("\n边缘区处理策略建议（基于人工审批的条件概率，不用于调整 tier）：")
        print("\n".join(hints))
    elif stats:
        n_total = sum(s["ok"] + s["no"] for s in stats.values())
        print(f"\n（已积累 {n_total} 条人工决策，各信源样本均不足 8 条，暂不给建议）")


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
        # 同上：只改 status，auto_status 保持系统原判
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
