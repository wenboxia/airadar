# AIRadar — 项目上手总纲（任何 LLM/编程 Agent 请先读我）

> 读完本文件你将知道：这个项目是什么、为什么存在、架构长什么样、文件怎么读、过去做了什么决策、现在进行到哪、接下来要做什么。

## 一、这个项目是什么

**AIRadar：每天自动运行的 AI 行业情报 Agent**——从权威信源抓取 → 信源分层筛选 → LLM 摘要分类 → 置信度路由（自动发布 / 人工审核 / 丢弃）→ 沉淀进可检索的知识库 → 前端仪表盘展示。整条 pipeline 配有评测集、准确率报告和 HITL 反馈回流。

- **线上**：https://wenboxia.github.io/airadar/
- **仓库**：github.com/wenboxia/airadar
- **运行**：GitHub Actions 每日北京时间 05:00 自动跑（避开 DeepSeek 峰时），数据 commit 回仓库，同一个工作流里接着部署前端；审批每周一开一次单
- 本地文件夹与产品名统一为 `airadar` / **AIRadar**（2026-08-28 起，此前曾叫 ainews）

## 二、项目为什么存在（最终目标，不可忘记）

这是主人的**面试作品项目**，服务于 AI 产品经理 / AI 产品工程师 / AI Native Builder 岗位（目标公司：Moonshot、DeepSeek、字节、阿里、腾讯等）。每个技术决策有双重标准：

1. **真实有用**：主人是它的第一个重度用户，产品要真的每天跑、真的解决"AI 资讯过载 + 看完留不下东西"的问题
2. **面试可讲**：每个设计决策都要能转化为面试谈资，全部记录在 `docs/decisions.md`（已 48 条，开头有故事线）。堆名词、过度设计是天敌——"能清醒地说出为什么不用 LangGraph"比"用了 LangGraph"更有价值

四大核心差异化（对应目标 JD 最值钱的能力，任何改动不得削弱）：
①信源分层信誉体系 ②全 pipeline 评测 ③记忆分层沉淀 ④HITL 置信度路由 + 反馈飞轮

## 三、架构地图

```
每日 GitHub Actions cron（05:00 北京时间）
        │
        ▼
pipeline/main.py（AgentLoop：按序跑 stages，统一错误隔离与预算控制）
  fetch → dedupe → triage → summarize → classify → publish
  （抓取）（去重）（分层评分  （双层摘要  （8 类分类   （写库+出JSON）
              置信度路由） +幻觉自检）  +时间维度）
        │
        ├──► pipeline/memory.py（记忆层：话题热度 7/30/90 天窗口 + 生命周期 + 时间线）
        │      ↑ 依赖全库数据，所以在所有 stage 之后单独跑
        ├──► pipeline/hitl.py（每次运行前 collect 回收勾选；北京时间周一 open 开三块审批单：
        │      待审 7 / 抽查 4 / 捞回 1，同一份数据写成 data/feed/review.json）
        ▼
data/knowledge.db（SQLite）+ data/feed/*.json（latest/week/archive/trends/stats/review；pending 前端不读）
        │                                │
        ▼                                ▼
评测层（独立于 pipeline）              web/（React，GitHub Pages）
  evals/run_eval.py             每日随定时任务：规则校验 + 黄金集三路径（只评已落库的判断）
  evals/triage_prompt_eval.py   改打分标准 / 换模型：黄金集离线重打分，--replay 零成本扫权重
  evals/feed_tag_eval.py + tools/classify_stability.py   分类准确率（官网标签）+ 噪声底
  evals/judge_hallucination.py + summary_model_eval.py   Kimi 当裁判核验摘要忠实度
  evals/model_cost_latency.py   单次调用延迟与成本
  evals/prelabel.py → tools/prep_golden_doc.py → tools/build_golden_v2.py   黄金集 v2 采样与写入
```

关键设计原则：

- **LLM 不联网**。抓取由确定性代码完成（信源可控、原文可存、幻觉可查）；LLM 只处理喂给它的文本。三个模型角色：主力 DeepSeek V4.1 Flash（`deepseek-flash`，09-18 从 v4-pro 换过来，D48）/ 备用 GLM 5.3（跨厂商兜底）/ 裁判 Kimi k3（评测，故意不同家避免 self-preference bias）
- **每个 stage 就是一个 skill**：统一契约 `run(items, ctx) -> items`，文件头有 MANIFEST
- **系统判断与人的判断分离**：`auto_status`（triage 原判，只有 triage 能写）vs `status`（当前状态，HITL 可改）。评测一律看 `auto_status`——这是 D28 踩坑换来的铁律
- **兜底降级无处不在**（`pipeline/guards.py`）：三条同构降级链（内容获取 / 模型调用 / 业务降级）。没有 API key 时整条 pipeline 也必须能跑通
- **评测驱动**：改 prompt / 换模型先在黄金集上离线对比（`evals/triage_prompt_eval.py`、`evals/feed_tag_eval.py`、`evals/summary_model_eval.py`）；`evals/run_eval.py` 只评已落库的判断、每天随定时任务跑。结果都存 `evals/results/` 供跨版本对比

## 四、文件阅读顺序（新 Agent 上手路线）

1. `start_prompt.md`（本文件）— 全局认知
2. `CLAUDE.md` — 核心原则与开发命令（**干活前必读**）
3. `todo.md` — 当前进度与主人待办
4. `docs/decisions.md` — D1–D48 全部设计决策（**改架构前必读，别推翻已有结论**）
5. `docs/mechanisms.md` — 六大机制的人话讲解（主人面试脱稿用）；`docs/demo.md` — 3 分钟演示路径；`README.md` — 对外的一页介绍；`docs/eval_report.md` — 模型选型报告（D47/D48：为什么主力换成 V4.1 Flash、代价是什么）；`SETUP.md` — 运维与故障排查
6. `docs/PRD.md` — 产品定义、竞品分析、指标体系
7. `pipeline/main.py` → `pipeline/stages/*.py` — 代码主线（按 fetch→publish 顺序读）
8. `pipeline/sources.yaml` — 信源注册表（**核心资产**）
9. `pipeline/memory.py` — 记忆层（话题热度与时间线）
10. `pipeline/hitl.py` — 人工审批闭环：每周一张三块审批单（GitHub Issue），同一份数据写成 `data/feed/review.json`；本地 CLI 兜底
11. `evals/run_eval.py` — 评测引擎（三分类路由评法）
12. `evals/judge_hallucination.py` — LLM-as-Judge 幻觉评测
13. `evals/triage_prompt_eval.py` — 改打分标准 / 换模型的离线评测（D45、D48）；`evals/summary_model_eval.py` + `evals/model_cost_latency.py` — 选型时的摘要忠实度与成本延迟对比（D47、D48）；`evals/feed_tag_eval.py` + `tools/classify_stability.py` — 分类准确率与一致率（D43）；`evals/prelabel.py` + `tools/prep_golden_doc.py` + `tools/build_golden_v2.py` — 黄金集 v2 采样、资料与写入（`review_golden.py` / `import_golden_docx.py` 是备用标注路径）；`tools/backfill_categories.py`、`tools/rescore_review_backlog.py` — 分类回填与待审积压重算的一次性工具
14. `web/` — 前端（React+Vite+TS+Tailwind，设计说明见 decisions.md D20）；「待审」页 `web/src/components/ReviewPanel.tsx` 只读展示 review.json（D46 追加）

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

- **[2026-09-16]** 收口成可被点开看的作品集（D35–D41）：
  - 线上数据自 08-29 起没更新——每日回写的提交不会触发 pages.yml，部署并入 daily.yml
  - 信源重构：取消 D 级、arXiv 按时间抓的入口关停、HN 关键词表扩充、新增 Raschka / Mollick、X 级三个实例；体检按各源自身节奏报警并读云端运行记录
  - 分类拆出「安全与防护」、新增「落地案例」，493 条历史分类回填（旧值存 categories_v1）
  - 审批改为每周一次、每次 12 条（09-18 又改成三块，D44）；过期单子不再被当成人工否决
  - 黄金集 v1 作废，v2 按成文标准随机分层采样 100 条，资料已准备，生成了 Word 标注表
  - README、演示路径 `docs/demo.md` 完成

- **[2026-09-17]** 黄金集 v2 标完（D42）：100 条语音标注 + 11 条 v1 复用，共 111 条。理由从语音聊天记录重新提取（代填的理由 94 条加了主人没说过的话），每条记来源；聊天记录全文只留本地。黄金集不标分类
- **[2026-09-17]** 分类改为平铺 8 类（D43，撤回 D39 的安全拆分）：模型 / Agent 与开发 / 评测 / 安全 / 产品与应用 / 行业动态 / 开源项目 / 研究论文，后两个按链接判定。
  同一 prompt 主类一致率 73% → 94%；官网标签测准确率 88%；536 条历史分类回填（旧值存 categories_v2）。
  当晚又修了一处边界（客户案例被 Agent 与开发 吃掉），换一批样本做配对对照后重跑回填，见 D43 追加
- **[2026-09-18]** 打分标准与收录标准对齐（D45）：triage 先判 kind、营销通稿按类型封顶价值分、权重经交叉验证重定（信源 0.55→0.4，长期价值 0.25→0.5）；
  送审里该收 28%→47%。审批改成周单 + 抽查撤下 + 旧池捞回（D44）；队列加时效与每信源配额，248 条里 158 条出队但不丢弃（D46）。
  论文入口改成"被别处提到才抓"（D36 落地），正文抽取保留外链，feed 官方标签入库；GitHub star 决定不纳入评分（黄金集 8 条，AUC 0.40）
- **[2026-09-18]** 模型选型（D47、D48）：三家旗舰对比的决定性差异是完成率；下午补测各家最新便宜档后，
  主力从 V4 Pro 换成 V4.1 Flash——理由是 Pro 要退役。Flash 分类与 Pro 打平、快 3 倍、成本约 1/4；打分多漏 2 条、10 篇摘要 1 篇意思反了
- **[2026-09-18]** 运行与界面：定时任务从北京 07:00 提前到 05:00——GitHub 定时会拖约 2 小时，原来正落进 DeepSeek 工作日峰时（UTC 01:00–04:00，价格翻倍）。
  网站「待审」窗口改读 `data/feed/review.json`，和周一的审批单是同一份数据，没开单时只写下一张何时开（D46 追加）；停抓信源的条目出队，抽查只抽当前打分标准自动发布的。
  冒烟测试抓到论文入口从没被调用过（被当普通信源派发，只留一条非致命错误），修好后 0 篇也留运行记录（D40 那类沉默失败）

**当前指标**：连续定时运行 20 天、20 次全部成功（08-29 → 09-17，口径同 README；09-18 第 21 次也成功）· 122 个测试全绿 · 规则校验 0 违规（608 条） · 分类主类一致率 93%（V4.1 Flash；V4 Pro 时 94%）、官网标签命中 88%。
黄金集 111 条，改打分标准前（09-16 标准，库里原判）：自动发布认同 62% · 自动丢弃认同 95%（漏 1 条）· 送审里该收的 28%。
改版后离线重打分：V4 Pro 64% · 98% · 47%；现主力 V4.1 Flash 73% · 93% · 44%，漏杀 1→3（D48）。区间都很宽，只看方向。

**下一步**：每周一处理三块审批单；抽查会持续给出 V4.1 Flash 自动发布的撤下率，攒到 30 条以上再看要不要动门槛（D48）；arXiv 按作者查询做不做，等主人拍板（见 todo.md）。

## 六、期望完成度

- **Week 1** ✅ 本地全链路 + 评测 v1
- **Week 2** ✅ 前端上线、每日自动运行、HITL 闭环
- **Week 3** Phase A+B ✅ ｜ 黄金集 v2 ✅（111 条）｜ Phase C ✅（三家配对对比 D47；补测各家便宜档后主力换成 V4.1 Flash，D48，见 `docs/eval_report.md`）
- **Week 4** 面试武装 ✅：decisions 故事线、运行数据量化、3 分钟 demo 路径、README
- **最终状态**：连续运行数十天、有真实数据、主人能脱稿讲清每个机制的线上产品

## 七、和主人协作的规矩

- 用**简单易懂的中文**沟通；主人的问题要正面明确回答，不许绕（答漏了会被指出来）
- 涉及账号/付款/API key 的操作只能主人做，你负责给手把手操作清单
- 每完成一个核心模块，给主人写一份"人话讲解"，确认其能脱稿讲再进下一步
- 黄金集标注必须由主人本人做——那是他的品味，面试要被追问；**绝不可让 LLM 代标**（会变成"LLM 评 LLM"的循环论证）。主人借助 AI 标注时，每条要记清哪部分是人的判断、哪部分来自 AI（D42）
- **文档里每一个具体断言，都应该能在代码或数据里指到对应的东西**（D21 的教训：文档描述了六级信源体系，实际只跑了三级）
