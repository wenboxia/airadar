# AIRadar — 项目上手总纲（任何 LLM/编程 Agent 请先读我）

> 你正在进入 **AIRadar** 项目。读完本文件你将知道：这个项目是什么、为什么存在、架构长什么样、文件怎么读、过去做了什么决策、现在进行到哪、接下来要做什么、最终要做到什么程度。

## 一、这个项目是什么（一句话）

**AIRadar：每天自动运行的 AI 行业情报 Agent**——从权威信源抓取内容 → 信源分层筛选 → LLM 摘要分类 → 置信度路由（自动发布 / 人工审核 / 丢弃）→ 沉淀进可检索的知识库 → 前端仪表盘展示。整条 pipeline 配有评测集、准确率报告和 HITL 反馈回流。

本地文件夹与产品名统一为 `airadar` / **AIRadar**（2026-08-28 起，此前曾叫 ainews）。

## 二、项目为什么存在（最终目标，不可忘记）

这是主人（用户）的**面试作品项目**，服务于 AI 产品经理 / AI 产品工程师 / AI Native Builder 岗位的面试（目标公司：Moonshot、DeepSeek、字节、阿里、腾讯等）。因此本项目的每一个技术决策都有双重标准：

1. **真实有用**：主人是它的第一个重度用户，产品要真的每天跑、真的解决"AI 资讯信息过载 + 看完留不下东西"的问题；
2. **面试可讲**：每个设计决策都要能转化为面试谈资，全部记录在 `docs/decisions.md`。堆名词、过度设计是本项目的天敌——"能清醒地说出为什么不用 LangGraph"比"用了 LangGraph"更有价值。

四大核心差异化（对应目标 JD 最值钱的四块能力，任何改动不得削弱它们）：
①信源分层信誉体系（Tier S/A/B/C/D/X） ②全 pipeline 评测（黄金集 + LLM-as-Judge） ③记忆分层沉淀（短/中/长期，不是一次性 feed） ④HITL 置信度路由 + 反馈数据飞轮。

## 三、架构地图

```
每日 GitHub Actions cron
        │
        ▼
pipeline/main.py（AgentLoop：按序跑 stages，统一错误隔离与预算控制）
  fetch → dedupe → triage → summarize → classify → publish
  （抓取）（去重） （分层评分  （双层摘要   （主题分类  （写库+出JSON
                置信度路由）  +幻觉自检）  +时间维度）  +开审批issue）
        │
        ▼
data/knowledge.db（SQLite 知识库）+ data/feed/*.json（前端消费）
        │                                │
        ▼                                ▼
evals/run_eval.py（评测引擎）      web/（React 前端，Vercel 部署）
```

关键设计原则：
- **LLM 不联网**。抓取由我们的代码完成（这样信源可控、摘要可对照原文查幻觉、来源可溯源）；LLM 只负责处理喂给它的文本。主力模型 deepseek-chat，评测裁判故意用另一家模型（避免 self-preference bias）。
- **每个 stage 就是一个 skill**：统一契约 `run(items, ctx) -> items`，文件头有 MANIFEST（名称/版本/输入输出/评测用例位置）。
- **兜底降级无处不在**（pipeline/guards.py）：单信源失败不阻塞全局；LLM 调用重试→切备用模型→最终降级为"仅标题+链接入库"；单次运行有 token 预算上限。**没有 API key 时整条 pipeline 也必须能跑通**（走降级路径）。
- **评测驱动**：改 prompt / 换模型必须跑 `evals/run_eval.py`，结果存 `evals/results/` 供跨版本对比。

## 四、文件阅读顺序（新 Agent 上手路线）

1. `start_prompt.md`（本文件）— 全局认知
2. `CLAUDE.md` — 核心原则与开发命令（干活前必读）
3. `todo.md` — 当前任务清单与进度
4. `docs/decisions.md` — 过往所有设计决策及理由（改架构前必读，别推翻已有结论）
5. `docs/PRD.md` — 产品定义、竞品分析、信源分层设计、指标定义
6. `pipeline/main.py` → `pipeline/stages/*.py` — 代码主线（按 fetch→publish 顺序读）
7. `pipeline/sources.yaml` — 信源注册表（核心资产）
8. `evals/run_eval.py` — 评测引擎
9. `web/` — 前端（React+Vite+TS+Tailwind）

## 五、过往关键决策摘要（详见 docs/decisions.md）

- 手写 agent loop，不用 LangGraph/AutoGen（框架对比分析写在 decisions.md）
- 部署零成本架构：GitHub Actions cron + 数据 commit 回 repo + Vercel 静态前端
- HITL 用 GitHub Issue 审批卡片（勾选→下次运行读取→feedback.jsonl→定期回流调信源分）
- 范围裁剪：语义搜索、对话式问答推迟 phase 2；Skill 体系只做轻量版；不做多租户/沙箱
- 命名：AIRadar（用户定），slogan「每天扫描 AI 前沿、沉淀为知识库的情报 Agent」

## 六、当前进度（每次重大进展后必须更新本节）

- [2026-08-27] 项目启动，计划批准（4 周）。
- [2026-08-27] Week 1 主体完成：pipeline 六 stage 全链路跑通（降级模式，真实抓取 53 条入库）；13 个信源就绪（Anthropic 走社区镜像，见 decisions.md D8）；评测引擎 v1 规则校验 0 违规；PRD（含联网竞品扫描）与 decisions.md D1–D9 完成。
- [2026-08-28] 三家模型接入：DeepSeek v4-pro 主力 / GLM 5.3 备用 / Kimi k3 裁判（D10）。实现跨厂商降级链，并修复"429 未必是限流"的真实 bug（D11，Kimi/GLM 用 429 表示余额不足）；12 个回归测试全绿。
- [2026-08-28] **首次真实 LLM 全量运行成功**：13 信源 36 条、11 分钟、0 错误、107 次 LLM 调用（其中 6 次真实触发了备用模型）。由此暴露并修复 6 个真问题（D12–D17）：模型适配层（推理模型 token 语义 + kimi 温度约束）、内容获取降级链（OpenAI Cloudflare 403 → Jina Reader）、幻觉根因（信息不足逼模型硬编 → 简介模式）、并发化（吞吐 ×6.5）、限流调参、截断自愈。机制的人话讲解见 `docs/mechanisms.md`。

## 七、期望完成度（分阶段验收标准）

- **Week 1**：本地一条命令跑完全 pipeline，产出当日真实数据入 SQLite + 评测报告 v1
- **Week 2**：前端上线 Vercel，GitHub Actions 每日自动跑，HITL issue 审批闭环
- **Week 3**：话题热度/生命周期趋势页、LLM-as-Judge 幻觉评测、DeepSeek/Kimi/GLM 多模型对比报告、反馈回流
- **Week 4**：面试武装——decisions.md 故事线、运行数据量化（X 信源/Y 天/Z 条/准确率 N%）、3 分钟 demo 路径
- **最终状态**：一个连续运行数十天、有真实数据、主人能脱稿讲清每个机制的线上产品

## 八、和主人协作的规矩

- 用**简单易懂的中文**沟通；主人问的问题要正面明确回答，不许绕
- 涉及账号/付款/API key 的操作只能主人做，你负责给手把手操作清单
- 每完成一个核心模块，给主人写一份"人话讲解"，确认其能脱稿讲再进下一步
- 黄金测试集的标注结论必须由主人复核（那是主人的品味，面试要被追问的）
