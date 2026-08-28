"""LLM-as-Judge 幻觉评测：用另一家的模型核验摘要是否忠于原文。

三个设计要点：

1. **裁判必须与主力不同家**（decisions.md D4）。主力是 DeepSeek，裁判用 Kimi。
   模型给自己的输出打分会系统性偏高（self-preference bias），自己人评自己人的结果不可信。

2. **只测有原文可对照的条目**。「简介模式」的条目（原文抓不到，只有信源简介）本来就
   被禁止扩写，拿它测幻觉是测错了对象——没有原文就没有"忠实"可言（呼应 D14）。

3. **多次采样取多数票**。kimi-k3 强制 temperature=1（D12），同一条内容重复问会得到
   不同答案。单次采样的"幻觉率"是个随机数，不是指标。所以每条判 K 次投票，
   并记录**裁判自己的分歧率**——分歧率高说明这条本来就是灰色地带，
   而不是"模型不稳定"。这也是 pass@k 稳定性思路的简化应用。

用法：
  python3 evals/judge_hallucination.py            # 抽样 12 条，每条判 3 次
  python3 evals/judge_hallucination.py --n 20 --k 3
"""
import argparse
import json
import os
import random
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.config import ROOT, load_config          # noqa: E402
from pipeline.guards import Budget, parallel_map        # noqa: E402
from pipeline.llm import LLMClient, LLMError, Provider  # noqa: E402

DB_PATH = os.environ.get("AIRADAR_DB_PATH") or os.path.join(ROOT, "data", "knowledge.db")
RESULTS_DIR = os.path.join(ROOT, "evals", "results")
PACE_SECONDS = 2.0   # 同一条的多次判断之间停顿，避开裁判的速率限制

_SYS = """你是严格的事实核查员。判断【摘要】是否完全忠于【原文】。

判为 unfaithful 的情形：
- 原文没有的信息（编造数字、人物、结论）
- 把原文的限定条件去掉，使结论被放大（例如原文说"在特定条件下有效"，摘要说"有效"）
- 把元数据当成内容（例如把信源名写成文章作者）
- 自行外推结论（原文没下的判断，如"将改变行业格局"）

只要有一处即判 unfaithful。摘要写得简略但准确 → faithful。

输出 JSON：{"faithful": true/false, "problem": "若不忠实，一句话指出问题；忠实则留空"}"""


def _judge_client(cfg) -> LLMClient:
    if not (cfg.judge_api_key and cfg.judge_base_url and cfg.judge_model):
        raise SystemExit("未配置裁判模型（AIRADAR_JUDGE_*）。见 .env.example")
    p = Provider("judge", cfg.judge_base_url, cfg.judge_api_key, cfg.judge_model)
    return LLMClient([p], Budget(400_000, 400), {})


# 至少要有这么多次成功判断才敢下结论。裁判调用失败（限流/超时）绝不能
# 被当成"判为不忠实"——那会把基础设施故障统计成模型质量问题（见下方 D32）
MIN_VOTES = 2


def _vote(llm, item, k: int) -> dict:
    """判 k 次取多数票，并记录裁判自身的分歧。

    **错误与判断严格分开**：调用失败进 `errors`，模型的实际判断进 `votes`。
    混在一起的话，一次限流就会被记成一条幻觉——指标会被基础设施故障污染。
    """
    prompt = (f"【原文】\n{(item['content'] or '')[:4000]}\n\n"
              f"【摘要】\n{item['summary_short']}\n{item['summary_long']}")
    votes, problems, errors = [], [], []
    for i in range(k):
        try:
            out = llm.json_chat(_SYS, prompt, max_tokens=400)
        except LLMError as e:
            errors.append(str(e)[:120])
            continue
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {str(e)[:100]}")
            continue
        if isinstance(out.get("faithful"), bool):
            votes.append(out["faithful"])
            if not out["faithful"] and out.get("problem"):
                problems.append(str(out["problem"])[:200])
        else:
            errors.append("judge_json_invalid")
        if i < k - 1:
            time.sleep(PACE_SECONDS)      # 裁判有速率限制，主动降速

    if len(votes) < MIN_VOTES:
        # 票数不够 → 这条不参与统计，且明确记录原因，而不是默认判它有问题
        return {"verdict": None, "votes": votes, "problems": [], "errors": errors,
                "skipped_reason": f"有效判断仅 {len(votes)} 次（需 >= {MIN_VOTES}）"}
    tally = Counter(votes)
    faithful = tally[True] >= tally[False]
    return {
        "verdict": faithful,
        "votes": votes,
        # 裁判内部分歧：k 次判断不一致 → 这条属于灰色地带，不是"模型不稳定"
        "split": len(set(votes)) > 1,
        "problems": problems[:2],
        "errors": errors,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12, help="抽样条数")
    ap.add_argument("--k", type=int, default=3, help="每条判几次（取多数票）")
    ap.add_argument("--seed", type=int, default=42, help="抽样随机种子，保证可复现")
    args = ap.parse_args()

    cfg = load_config()
    llm = _judge_client(cfg)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # 只测有完整原文的：简介模式没有原文可对照，测它是测错对象
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM items WHERE summary_long != '' AND content != '' "
        "AND status IN ('published','review')")]
    rows = [r for r in rows
            if (json.loads(r["extra"] or "{}").get("summary_mode")) != "brief"]
    if not rows:
        print("没有可核验的条目（需要有长摘要且有原文）")
        return 1

    random.seed(args.seed)
    sample = random.sample(rows, min(args.n, len(rows)))
    print(f"裁判：{cfg.judge_model}（主力是 {cfg.llm_model}，故意不同家）")
    print(f"抽样 {len(sample)} 条，每条判 {args.k} 次取多数票…\n")

    # 裁判限流严格，串行跑。慢一点无所谓，评测不是每天都要跑
    results = [(r, _vote(llm, r, args.k)) for r in sample]
    results = [x for x in results if x]

    judged = [(r, v) for r, v in results if v["verdict"] is not None]
    skipped = [(r, v) for r, v in results if v["verdict"] is None]
    unfaithful = [(r, v) for r, v in judged if not v["verdict"]]
    split = [(r, v) for r, v in judged if v.get("split")]

    for r, v in judged:
        mark = "✅" if v["verdict"] else "⚠️"
        flag = "  [裁判有分歧]" if v.get("split") else ""
        print(f"{mark} {r['source'][:18]:<19} {r['title'][:44]}{flag}")
        if not v["verdict"] and v["problems"]:
            print(f"     └ {v['problems'][0][:110]}")

    rate = len(unfaithful) / len(judged) if judged else None
    out = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "judge_model": cfg.judge_model,
        "primary_model": cfg.llm_model,
        "sampled": len(sample), "judged": len(judged),
        "skipped": len(skipped), "min_votes": MIN_VOTES, "k": args.k,
        "hallucination_rate": round(rate, 3) if rate is not None else None,
        "judge_split_rate": round(len(split) / len(judged), 3) if judged else None,
        "unfaithful": [{"id": r["id"], "title": r["title"], "source": r["source"],
                        "problems": v["problems"]} for r, v in unfaithful],
    }
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR,
                        datetime.now(timezone.utc).strftime("judge-%Y%m%d-%H%M%S.json"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    for r, v in skipped:
        print(f"⏭  {r['source'][:18]:<19} {r['title'][:40]}  [跳过：{v['skipped_reason']}]")

    print(f"\n{'─' * 60}")
    if not judged:
        print("没有条目取得足够的有效判断——多半是裁判限流。降低 --n 或稍后重试。")
        print("（注意：调用失败绝不计入幻觉率，否则是把基础设施故障统计成模型质量问题）")
        return 1
    print(f"幻觉率 {rate:.0%}（{len(unfaithful)}/{len(judged)} 条被判不忠实）")
    if skipped:
        print(f"跳过 {len(skipped)} 条（裁判调用失败，**不计入**幻觉率）")
    print(f"裁判分歧率 {out['judge_split_rate']:.0%}"
          f"（{len(split)} 条 {args.k} 次判断不一致——属灰色地带，不是模型不稳）")
    print(f"结果存 {os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
