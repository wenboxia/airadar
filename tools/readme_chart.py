"""README 运行数据图：每次定时运行的三路去向 + 耗时，存 docs/images/runs-daily(.en).png。

数据直接取 tools/run_stats.py 的逐次明细，和 README 表格同一个来源、同一个口径。
为什么是上下两张图而不是一张双纵轴：条数和分钟是两种量纲，双纵轴会让读者误读两条线的相对高低。
配色用 dataviz 参考色板深色档的前三个（蓝 / 橙 / 青），跑过校验器：
网站自己的琥珀 / 青 / 灰在色觉异常模拟下青和灰分不开，所以没用。

用法：python3 tools/readme_chart.py
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BJ = timezone(timedelta(hours=8))
SURFACE, INK, INK_DIM, INK_FAINT, RULE = "#0a0d0e", "#eceae4", "#adb9bb", "#7a8a8d", "#232c30"
SERIES = {"published": "#3987e5", "review": "#d95926", "discarded": "#199e70"}
TEXT = {
    "zh": {"published": "自动发布", "review": "送人工审", "discarded": "自动丢弃",
           "top": "每次定时运行进入打分的条目，按去向", "bottom": "单次运行耗时（分钟）",
           "x": "运行日期（北京时间）", "split": "09-18 起新打分标准\n09-19 起主力换成 V4.1 Flash",
           "file": "runs-daily.png"},
    "en": {"published": "Auto-published", "review": "Sent to review", "discarded": "Auto-discarded",
           "top": "Items scored per scheduled run, by route", "bottom": "Run time (minutes)",
           "x": "Run date (Beijing time)", "split": "09-18: new scoring standard\n09-19: main model → V4.1 Flash",
           "file": "runs-daily.en.png"},
}


def per_run() -> list:
    out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "run_stats.py"), "--json"]
                         + sys.argv[1:], capture_output=True, text=True, check=True).stdout
    return json.loads(out)["per_run"]


def draw(runs: list, lang: str):
    t = TEXT[lang]
    plt.rcParams.update({"font.family": ["Hiragino Sans GB", "Arial Unicode MS", "sans-serif"],
                         "axes.unicode_minus": False})
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(12, 6.2), dpi=150, sharex=True,
                                 gridspec_kw={"height_ratios": [2.3, 1], "hspace": 0.28})
    fig.patch.set_facecolor(SURFACE)
    xs = list(range(len(runs)))
    days = [datetime.fromisoformat(r["started"]).astimezone(BJ).strftime("%m-%d") for r in runs]

    bottom = [0] * len(runs)
    for key, color in SERIES.items():
        vals = [r[key] for r in runs]
        # 段与段之间用背景色描边留出 2px 缝，不画边框
        ax.bar(xs, vals, bottom=bottom, width=0.62, color=color, edgecolor=SURFACE,
               linewidth=1.2, label=t[key], zorder=2)
        bottom = [b + v for b, v in zip(bottom, vals)]
    bx.bar(xs, [r["minutes"] for r in runs], width=0.62, color=INK_FAINT, zorder=2)

    # 虚线画在新打分标准生效处：换模型只晚一次运行，两件事分不开，文字里写明
    split = next(i for i, r in enumerate(runs) if r["new_standard"])
    for a in (ax, bx):
        a.set_facecolor(SURFACE)
        a.axvline(split - 0.5, color=INK_DIM, linestyle=(0, (3, 3)), linewidth=1, zorder=3)
        a.grid(axis="y", color=RULE, linewidth=0.8, zorder=0)
        a.tick_params(colors=INK_FAINT, labelsize=9, length=0)
        for s in a.spines.values():
            s.set_visible(False)
    top = max(bottom) * 1.22           # 给虚线旁的说明留出头顶空间，免得压在柱子上
    ax.set_ylim(0, top)
    ax.text(split - 0.2, top * 0.99, t["split"], color=INK_DIM, fontsize=9, va="top",
            ha="left", linespacing=1.5)
    ax.set_title(t["top"], color=INK, fontsize=12, loc="left", pad=26)
    bx.set_title(t["bottom"], color=INK, fontsize=11, loc="left", pad=8)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3, frameon=False,
              labelcolor=INK_DIM, fontsize=9.5, handlelength=1.2, borderaxespad=0.2)
    step = 3
    bx.set_xticks(xs[::step], days[::step])
    bx.set_xlabel(t["x"], color=INK_FAINT, fontsize=9, labelpad=8)

    path = os.path.join(ROOT, "docs", "images", t["file"])
    fig.savefig(path, facecolor=SURFACE, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    print(f"  {t['file']}: {os.path.getsize(path) // 1024} KB")


if __name__ == "__main__":
    data = per_run()
    for lang in ("zh", "en"):
        draw(data, lang)
