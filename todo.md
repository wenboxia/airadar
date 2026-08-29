# AIRadar TODO

> 线上：https://wenboxia.github.io/airadar/ ｜ 仓库：github.com/wenboxia/airadar
> 每日北京时间 07:00 由 GitHub Actions 自动运行。

## 🔴 主人现在要做的（只有两件）

- [ ] **黄金集标注**：`python3 evals/review_golden.py` —— 草稿 25 条已按分数段分层就绪，约 15 分钟，随时可停（每条自动存盘）
- [ ] **处理审批 [Issue #2](https://github.com/wenboxia/airadar/issues/2)**（14 条，定时任务自动创建）—— 勾选后**必须点 Close issue** 才算提交

无需处理：DeepSeek 已充值 · GitHub Pages 已上线 · Issue #1 的 18 条决策已回收入库

---

## Week 1 — pipeline 跑通 ✅

- [x] 项目骨架 + start_prompt / CLAUDE / todo / docs
- [x] 六 stage 全链路跑通，SQLite 入库，无 API key 降级路径验证
- [x] 三家模型接入：DeepSeek 主力 / GLM 备用 / Kimi 裁判（D10）
- [x] 跨厂商降级链 + 错误语义分类（修 429≠限流 的真实 bug，D11）
- [x] 模型适配层：推理模型 token 语义 + kimi 温度约束 + 截断自愈（D12、D17）
- [x] 内容获取降级链：直抓 → Jina Reader → RSS 简介（修 OpenAI 403，D13）
- [x] 幻觉根因修复：信息不足走"简介模式"（D14）、元数据与正文分离（D18）
- [x] 并发化（吞吐 ×6.5，D15）+ 限流调参（D16）
- [x] evals/run_eval.py + PRD（含竞品扫描）+ decisions.md

## Week 2 — 前端 + HITL + 上线 ✅

- [x] React 前端：今日 / 本周 / 待审 / 机制 四视图 + 雷达可视化 + 双轴筛选（D20）
- [x] HITL 闭环：GitHub Issue 勾选卡片 → 回读 → feedback.jsonl → 信源策略建议
- [x] GitHub 仓库上线 + Secrets/Variables 配置 + Actions 每日 cron
- [x] **GitHub Pages 部署**（Vercel 改为可选，见 SETUP.md）
- [x] 信源补齐至 19 个、五级全活（D21）+ 信源体检机制（D22）
- [x] 分类改多标签 + 新增「模型训练」（D25）
- [x] 修 workflow 回写竞态 + 前端硬编码信源改为数据驱动
- [x] 数据飞轮的统计陷阱修复（有偏样本，只调边缘区策略不调 tier，D27）

## Week 3 — 记忆/趋势 + 评测深化

### Phase A：评测可信度 ✅
- [x] `auto_status` 冻结系统自主判断，HITL 只改 `status`（D28）——26 条黄金集全部恢复有效
- [x] 从 feedback.jsonl 反推历史数据，18 条被改写记录 100% 还原
- [x] **三分类路由评测**：自动发布 / 自动丢弃 / 送审三条路径分开评（D29）
- [x] prelabel 改为按分数段分层采样，补低分段缺口
- [x] 4 个回归测试锁住 auto_status 隔离（共 27 个测试）

### Phase B：记忆与幻觉评测 ✅
- [x] 知识库视图 + 全库搜索（不受 7 天限制）
- [x] 内容过期机制：时效类未经人工认可的 14 天后退出，长期价值与人工认可的永不过期（D30）
- [x] `pipeline/memory.py`：话题热度 7/30/90 天窗口 + 生命周期 + 时间线
- [x] 数据跨度守卫：不足 21 天不给趋势结论（D31）
- [x] `evals/judge_hallucination.py`：Kimi 当裁判，判 3 次取多数票 + 记录裁判分歧率（D33）
- [x] 修「基础设施故障被统计成模型质量问题」（D32）
- [x] summarize 不再向模型传信源名——结构防护优于指令防护（D34）

### Phase C：三家模型对比（等黄金集 ≥ 60 条）
- [ ] 配对实验设计（同一批内容跑三家，只看分歧对，McNemar 检验）
- [ ] 成本与延迟对比（`deepseek-v4-flash` 实测比 pro 快一倍多）
- [ ] 产出 `docs/eval_report.md`：哪个 stage 用哪个模型的选型建议

## Week 4 — 打磨 + 面试武装

- [ ] decisions.md 整理成完整故事线（34 条 = 34 道面试题答案）
- [ ] 运行数据量化（连续天数 / 处理条数 / 筛除率 / 审批通过率 / 幻觉率）
- [ ] 3 分钟 demo 演示路径 + 简历条目
- [ ] （余力才做）对话式问答或语义搜索

---

## 已知待办：等数据够了再动（不是遗漏，是刻意等）

- **送审门槛偏低**：送审命中率仅 20%，被否的 8 条里 5 条属两种可识别类型（营销宣传 3、无创新仿造品 2）。修法是在评分标准里加这两个维度，而非调阈值——**等黄金集 ≥ 60 条再动**，现在改了不知是变好还是运气。
- **GitHub Trending 无 star 门槛**：现规则是"近 30 天新建 + 按 star 取前 5"，抓到的从 1,135 到 201,459 star 都有。但主人收了 1,135 star 的 pi-from-scratch、否了 6,602 star 的 kimi-k3-in-c——**star 与偏好相关性存疑，等数据验证后再决定是否纳入评分**。
- **内容过期机制尚未真正触发**：知识库数据仅 18 天，还没有内容满 14 天。规则已就位，几天后自然生效。
- **趋势生命周期判断未解锁**：需 21 天跨度，还差 3 天。

## 日常（上线后每天）

- [ ] 【主人】~2 分钟 HITL 审批（勾完记得关 issue）+ 真实使用吐槽
