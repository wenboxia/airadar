"""数据模型：Item 是贯穿整条 pipeline 的唯一数据单元。"""
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Item:
    id: str = ""                # 规范化 URL 的 sha1 前 16 位
    url: str = ""
    title: str = ""
    source: str = ""            # 信源 name（对应 sources.yaml）
    tier: str = "D"             # S/A/B/C/D/X
    published_at: str = ""      # UTC ISO
    fetched_at: str = ""
    content: str = ""           # 原文文本（截断）。留着它才能对照查幻觉
    summary_short: str = ""     # 一句话摘要
    summary_long: str = ""      # ~300 字摘要
    key_points: list = field(default_factory=list)
    topics: list = field(default_factory=list)
    category: str = ""            # 主分类（categories[0]），保留给旧数据与排序
    categories: list = field(default_factory=list)  # 多分类：一条内容常属于多个类别
    horizon: str = ""           # short=时效新闻 / long=长期方法论
    score: float = 0.0
    score_detail: dict = field(default_factory=dict)
    status: str = "new"         # 当前状态，人工审批会改写它
    # 系统自主判断的原始结论，**人工审批永不覆盖**。
    # 没有它，任何经过人工审批的条目都无法再用于评测——
    # 因为 status 已经变成了人的答案，拿它去评测等于"人评人自己"（decisions.md D28）
    auto_status: str = ""
    notes: list = field(default_factory=list)  # 过程审计：降级原因、幻觉标记等
    extra: dict = field(default_factory=dict)  # 信源特有字段（HN 分数、GitHub star 等）

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Context:
    """随 pipeline 传递的运行上下文。"""
    cfg: Any
    llm: Any            # LLMClient（可能不可用，stage 必须自己降级）
    db: Any
    run_id: str
    stats: dict = field(default_factory=dict)   # 各 stage 往里记数字
    errors: list = field(default_factory=list)  # 非致命错误集中记录

    def note_error(self, where: str, err: str):
        self.errors.append({"where": where, "error": err[:300]})
        print(f"  [warn] {where}: {err[:200]}")
