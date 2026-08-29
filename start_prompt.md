# AIRadar — 项目上手总纲（任何 LLM/编程 Agent 请先读我）

> 读完本文件你将知道：这个项目是什么、为什么存在、架构长什么样、文件怎么读、过去做了什么决策、现在进行到哪、接下来要做什么。

## 一、这个项目是什么

**AIRadar：每天自动运行的 AI 行业情报 Agent**——从权威信源抓取 → 信源分层筛选 → LLM 摘要分类 → 置信度路由（自动发布 / 人工审核 / 丢弃）→ 沉淀进可检索的知识库 → 前端仪表盘展示。整条 pipeline 配有评测集、准确率报告和 HITL 反馈回流。

- **线上**：https://wenboxia.github.io/airadar/
- **仓库**：github.com/wenboxia/airadar
- **运行**：GitHub Actions 每日北京时间 07:00 自动跑，数据 commit 回仓库，前端自动重新部署
- 本地文件夹与产品名统一为 `airadar` / **AIRadar**（2026-08-28 起，此前曾叫 ainews）

## 二、项目为什么存在（最终目标，不可忘记）

这是主人的**面试作品项目**，服务于 AI 产品经理 / AI 产品工程师 / AI Native Builder 岗位（目标公司：Moonshot、DeepSeek、字节、阿里、腾讯等）。每个技术决策有双重标准：

1. **真实有用**：主人是它的第一个重度用户，产品要真的每天跑、真的解决"AI 资讯过载 + 看完留不下东西"的问题
2. **面试可讲**：每个设计决策都要能转化为面试谈资，全部记录在 `docs/decisions.md`（已 34 条）。堆名词、过度设计是天敌——"能清醒地说出为什么不用 LangGraph"比"用了 LangGraph"更有价值

四大核心差异化（对应目标 JD 最值钱的能力，任何改动不得削弱）：
①信源分层信誉体系 ②全 pipeline 评测 ③记忆分层沉淀 ④HITL 置信度路由 + 反馈飞轮

## 三、架构地图

```
每日 GitHub Actions cron（07:00 北京时间）
        │
        ▼
pipeline/main.py（AgentLoop：按序跑 stages，统一错误隔离与预算控制）
  fetch → dedupe → triage → summarize → classify → publish
  （抓取）（去重）（分层评分  （双层摘要  （多标签分类 （写库+出JSON
              置信度路由） +幻觉自检）  +时间维度）  +开审批issue）
        │
        ├──► pipeline/memory.py（记忆层：话题热度 7/30/90 天窗口 + 生命周期 + 时间线）
        │      ↑ 依赖全库数据，所以在所有 stage 之后单独跑
        ▼
data/knowledge.db（SQLite）+ data/feed/*.json（latest/week/archive/pending/trends/stats）
        │                                │
        ▼                                ▼
评测层（独立于 pipeline，按需跑）      web/（React，GitHub Pages）
  evals/run_eval.py         规则校验 + 黄金集三路径评测
  evals/judge_hallucination.py  Kimi 当裁判核验摘要忠实度
  evals/prelabel.py → review_golden.py  黄金集分层采样与人工标注
```

关键设计原则：

- **LLM 不联网**。抓取由确定性代码完成（信源可控、原文可存、幻觉可查）；LLM 只处理喂给它的文本。三个模型角色：主力 DeepSeek v4-pro / 备用 GLM 5.3（跨厂商兜底）/ 裁判 Kimi k3（评测，故意不同家避免 self-preference bias）
- **每个 stage 就是一个 skill**：统一契约 `run(items, ctx) -> items`，文件头有 MANIFEST
- **系统判断与人的判断分离**：`auto_status`（triage 原判，只有 triage 能写）vs `status`（当前状态，HITL 可改）。评测一律看 `auto_status`——这是 D28 踩坑换来的铁律
- **兜底降级无处不在**（`pipeline/guards.py`）：三条同构降级链（内容获取 / 模型调用 / 业务降级）。没有 API key 时整条 pipeline 也必须能跑通
- **评测驱动**：改 prompt / 换模型必须跑 `evals/run_eval.py`，结果存 `evals/results/` 供跨版本对比

## 四、文件阅读顺序（新 Agent 上手路线）

1. `start_prompt.md`（本文件）— 全局认知
2. `CLAUDE.md` — 核心原则与开发命令（**干活前必读**）
3. `todo.md` — 当前进度与主人待办
4. `docs/decisions.md` — D1–D34 全部设计决策（**改架构前必读，别推翻已有结论**）
5. `docs/mechanisms.md` — 六大机制的人话讲解（主人面试脱稿用）
6. `docs/PRD.md` — 产品定义、竞品分析、指标体系
7. `pipeline/main.py` → `pipeline/stages/*.py` — 代码主线（按 fetch→publish 顺序读）
8. `pipeline/sources.yaml` — 信源注册表（**核心资产**）
9. `pipeline/memory.py` — 记忆层（话题热度与时间线）
10. `pipeline/hitl.py` — 人工审批闭环（GitHub Issue / 本地 CLI）
11. `evals/run_eval.py` — 评测引擎（三分类路由评法）
12. `evals/judge_hallucination.py` — LLM-as-Judge 幻觉评测
13. `evals/prelabel.py` + `evals/review_golden.py` — 黄金集分层采样与标注工具
14. `web/` — 前端（React+Vite+TS+Tailwind，设计说明见 decisions.md D20）

## 五、当前进度

- **[2026-08-27]** 项目启动，计划批准。Week 1 完成：六 stage 全链路跑通、13 信源、评测 v1。
- **[2026-08-28]** 三家模型接入并修复一批真实问题（D10–D18）：跨厂商降级链、429≠限流、模型适配层、内容获取降级链、两轮幻觉根因修复、并发化（吞吐 ×6.5）。
- **[2026-08-28]** Week 2 完成：React 前端上线 **GitHub Pages**、HITL 闭环跑通、Actions 每日 cron 生效。信源补至 19 个五级全活（D21），新增信源体检（D22），分类改多标签（D25），数据飞轮统计陷阱修复（D27）。
- **[2026-08-28/29]** Week 3 Phase A+B 完成：
  - **评测污染修复**（D28）：`auto_status` 隔离，26 条黄金集全部恢复有效
  - **三分类路由评测**（D29）：暴露出"送审命中率仅 20%"这个二分类指标看不见的问题
  - 知识库视图 + 全库搜索 + 内容过期（D30）
  - 话题热度与时间线 + 数据跨度守卫（D31）
  - LLM-as-Judge 幻觉评测（D32/D33）+ summarize 不再传信源名（D34）

**当前指标**：自动发布准确率 67% · 送审命中率 **20%** · 漏网之鱼 **0** · 幻觉率 25% · 分类主命中 83% · 27 个测试全绿

**当前卡点**：等主人把黄金集从 26 条标到 100 条（草稿 25 条已分层就绪）。够 60 条才能做 Phase C 的三家模型配对对比。

## 六、期望完成度

- **Week 1** ✅ 本地全链路 + 评测 v1
- **Week 2** ✅ 前端上线、每日自动运行、HITL 闭环
- **Week 3** Phase A+B ✅ ｜ Phase C（三家模型配对对比）等黄金集扩容
- **Week 4** 面试武装：decisions 故事线、运行数据量化、3 分钟 demo 路径
- **最终状态**：连续运行数十天、有真实数据、主人能脱稿讲清每个机制的线上产品

## 七、和主人协作的规矩

- 用**简单易懂的中文**沟通；主人的问题要正面明确回答，不许绕（答漏了会被指出来）
- 涉及账号/付款/API key 的操作只能主人做，你负责给手把手操作清单
- 每完成一个核心模块，给主人写一份"人话讲解"，确认其能脱稿讲再进下一步
- 黄金集标注必须由主人本人做——那是他的品味，面试要被追问；**绝不可让 LLM 代标**（会变成"LLM 评 LLM"的循环论证）
- **文档里每一个具体断言，都应该能在代码或数据里指到对应的东西**（D21 的教训：文档描述了六级信源体系，实际只跑了三级）
