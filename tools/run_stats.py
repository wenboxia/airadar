"""README「运行数据」表的唯一来源：从 data/runs 逐次累加，输出中英两份表格行。

为什么要有这个脚本：README 里的数字以前是手算的，停在某一天就再没人更新，
也没人说得清它是怎么算出来的。现在表上写截止日，数字由这里生成，要刷新就重跑一次。
为什么不让每日任务自动改 README：数字天天变，主人面试时记住的数会和页面对不上；
而且 09-18 同时换了模型和打分标准，自动累加会把两段悄悄混在一起。

口径：
- 只算定时运行：运行记录由 airadar-bot 提交（本地调试跑的不算），且开始时间 ≥ 2026-08-29
- 按记录里的主力模型分段（mode 字段），不按日期猜
- 「全部成功」只能由 --check-gh 给出：失败的运行可能根本没留下记录文件

用法：
  python3 tools/run_stats.py                    # 截至最新一次
  python3 tools/run_stats.py --until 2026-09-25 # 截至某天（UTC，含当天）
  python3 tools/run_stats.py --check-gh         # 顺带核对 GitHub 上定时运行的成败（需要 gh 已登录）
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import statistics
import subprocess
from collections import Counter
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIRST_DAY = "2026-08-29"
BOT = "airadar-bot"


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _bot_files() -> set:
    """首次提交者是 airadar-bot 的运行记录 = 云端定时任务写的"""
    out = subprocess.run(
        ["git", "log", "--diff-filter=A", "--name-only", "--format=@%an", "--", "data/runs"],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout
    files, author = set(), None
    for line in out.splitlines():
        if line.startswith("@"):
            author = line[1:]
        elif line.strip() and author == BOT:
            files.add(os.path.basename(line.strip()))
    return files


def load_runs(until: str = None) -> list:
    bot = _bot_files()
    runs = []
    for p in sorted(glob.glob(os.path.join(ROOT, "data", "runs", "*.json"))):
        if os.path.basename(p) not in bot:
            continue
        d = json.load(open(p, encoding="utf-8"))
        day = d["started"][:10]
        if day < FIRST_DAY or (until and day > until):
            continue
        m = re.search(r"primary\(([^)]+)\)", d.get("mode", ""))
        d["_model"] = m.group(1) if m else "unknown"
        runs.append(d)
    return runs


def summarize(runs: list) -> dict:
    st = lambda d, k: d["stats"].get(k) or {}
    calls = sum(d["stats"].get("llm_calls", 0) for d in runs)
    fb = sum(st(d, "calls_by_provider").get("fallback", 0) for d in runs)
    mins = [(_ts(d["finished"]) - _ts(d["started"])).total_seconds() / 60 for d in runs]
    errors = [e for d in runs for e in d.get("errors", [])]
    return {
        "n": len(runs),
        "first": runs[0]["started"][:10], "last": runs[-1]["started"][:10],
        "fetched": sum(st(d, "fetch").get("total", 0) for d in runs),
        "kept": sum(st(d, "dedupe").get("kept", 0) for d in runs),
        "routes": {k: sum(st(d, "triage").get(k, 0) for d in runs)
                   for k in ("published", "review", "discarded")},
        "calls": calls,
        "tokens": sum(d["stats"].get("llm_tokens", 0) for d in runs),
        "fallback": fb,
        "fallback_runs": sum(1 for d in runs if st(d, "calls_by_provider").get("fallback", 0)),
        "errors": len(errors),
        "errors_by_stage": dict(Counter(e.get("where", "?") for e in errors)),
        "median_min": round(statistics.median(mins), 1) if mins else None,
    }


def knowledge_base() -> dict:
    db = sqlite3.connect(f"file:{os.path.join(ROOT, 'data', 'knowledge.db')}?mode=ro", uri=True)
    published = db.execute("SELECT COUNT(*) FROM items WHERE status='published'").fetchone()[0]
    stats = json.load(open(os.path.join(ROOT, "data", "feed", "stats.json"), encoding="utf-8"))
    archive = (stats.get("archive") or {}).get("total")
    return {"published": published, "archive": archive,
            "expired": published - archive if archive is not None else None}


def latest_rule_check() -> dict:
    files = sorted(f for f in glob.glob(os.path.join(ROOT, "evals", "results", "*.json"))
                   if re.fullmatch(r"\d{8}-\d{6}\.json", os.path.basename(f)))
    d = json.load(open(files[-1], encoding="utf-8"))
    return {"file": os.path.relpath(files[-1], ROOT), "ts": d["ts"], **d["rules"]}


def test_count() -> int:
    return sum(len(re.findall(r"^\s+def test_", open(p, encoding="utf-8").read(), re.M))
               for p in glob.glob(os.path.join(ROOT, "evals", "test_*.py")))


def check_gh(runs: list) -> str:
    out = subprocess.run(
        ["gh", "run", "list", "--workflow", "daily.yml", "--event", "schedule", "--limit", "300",
         "--json", "createdAt,conclusion"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    last = runs[-1]["started"]
    sched = [r for r in json.loads(out) if FIRST_DAY <= r["createdAt"][:10] and r["createdAt"] <= last]
    bad = [r for r in sched if r["conclusion"] != "success"]
    return (f"GitHub 定时运行 {len(sched)} 次，失败 {len(bad)} 次；本地记录 {len(runs)} 份"
            + ("" if len(sched) == len(runs) else "  ← 数量对不上，先查清再写「全部成功」"))


def _wan(n: int) -> str:
    return f"{n / 10000:.0f} 万"


def print_tables(all_: dict, eras: list, kb: dict, tests: int):
    r = all_["routes"]
    cut = f"{1 - all_['kept'] / all_['fetched']:.0%}"
    fb_pct = f"{all_['fallback'] / all_['calls']:.1%}"
    era_zh = " · ".join(f"{e['model']} {e['n']} 次中位 {e['median_min']} 分钟" for e in eras)
    era_en = " · ".join(f"{e['model']} ({e['n']} runs) {e['median_min']} min" for e in eras)
    print(f"\n口径：{all_['first']} → {all_['last']}（UTC）共 {all_['n']} 次定时运行\n")
    print("| 指标 | 值 |\n|---|---|")
    print(f"| 累计抓取 → 去重后进入打分 | {all_['fetched']:,} → {all_['kept']:,} 条（筛掉 {cut}） |")
    print(f"| 三路去向 | 自动发布 {r['published']} · 送审 {r['review']} · 自动丢弃 {r['discarded']} |")
    print(f"| LLM 调用 | {all_['calls']:,} 次，{_wan(all_['tokens'])} token |")
    print(f"| 切到备用模型 | {all_['fallback']} 次（{fb_pct}），{all_['n']} 次运行里 {all_['fallback_runs']} 次发生过 |")
    print(f"| 非致命错误 | {all_['errors']} 个（按阶段：{all_['errors_by_stage']}） |")
    print(f"| 单次运行耗时（中位） | {all_['median_min']} 分钟；{era_zh} |")
    if kb["archive"] is not None:
        print(f"| 知识库 | 已发布 {kb['published']} 条，其中 {kb['expired']} 条时效内容已按规则退出 |")
    print(f"| 回归测试 | {tests} 个 |")

    print(f"\nScope: {all_['n']} scheduled runs, {all_['first']} → {all_['last']} (UTC)\n")
    print("| Metric | Value |\n|---|---|")
    print(f"| Fetched → kept after dedupe | {all_['fetched']:,} → {all_['kept']:,} ({cut} filtered) |")
    print(f"| Routes | auto-published {r['published']} · sent to review {r['review']} · auto-discarded {r['discarded']} |")
    print(f"| LLM calls | {all_['calls']:,} calls, {all_['tokens'] / 1e6:.2f}M tokens |")
    print(f"| Fallback to backup model | {all_['fallback']} calls ({fb_pct}), in {all_['fallback_runs']} of {all_['n']} runs |")
    print(f"| Non-fatal errors | {all_['errors']} (by stage: {all_['errors_by_stage']}) |")
    print(f"| Run time (median) | {all_['median_min']} min; {era_en} |")
    if kb["archive"] is not None:
        print(f"| Knowledge base | {kb['published']} published, {kb['expired']} time-sensitive items expired by rule |")
    print(f"| Regression tests | {tests} |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", help="截止日（UTC，YYYY-MM-DD，含当天）")
    ap.add_argument("--check-gh", action="store_true")
    ap.add_argument("--json", action="store_true", help="只输出 JSON，给画图脚本用")
    args = ap.parse_args()

    runs = load_runs(args.until)
    all_ = summarize(runs)
    eras, cur = [], []
    for d in runs:                       # 按主力模型连续分段
        if cur and d["_model"] != cur[-1]["_model"]:
            eras.append({"model": cur[-1]["_model"], **summarize(cur)})
            cur = []
        cur.append(d)
    eras.append({"model": cur[-1]["_model"], **summarize(cur)})
    kb, tests, rules = knowledge_base(), test_count(), latest_rule_check()

    if args.json:
        print(json.dumps({"all": all_, "eras": eras, "kb": kb, "tests": tests, "rules": rules,
                          "per_run": [{"started": d["started"], "model": d["_model"],
                                       # 新打分标准（D45）起每次运行都记内容类型分布，旧标准没有
                                       "new_standard": "by_kind" in d["stats"].get("triage", {}),
                                       **{k: d["stats"].get("triage", {}).get(k, 0)
                                          for k in ("published", "review", "discarded")},
                                       "minutes": round((_ts(d["finished"]) - _ts(d["started"])).total_seconds() / 60, 2)}
                                      for d in runs]}, ensure_ascii=False, indent=1))
        return
    print_tables(all_, eras, kb, tests)
    for e in eras:
        er = e["routes"]
        print(f"\n[{e['model']}] {e['first']} → {e['last']}，{e['n']} 次：发布 {er['published']} / 送审 {er['review']} / "
              f"丢弃 {er['discarded']}，错误 {e['errors']}，中位 {e['median_min']} 分钟")
    print(f"\n规则校验（{rules['file']}）：{rules['total_items']} 条，{rules['violations']} 违规")
    if args.check_gh:
        print(check_gh(runs))


if __name__ == "__main__":
    main()
