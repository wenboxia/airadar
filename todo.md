# AIRadar TODO（随进度勾选，完成的打 x）

## Week 1 — pipeline 跑通（目标：本地一条命令出当日真实数据 + 评测报告）

- [x] 项目骨架：start_prompt.md / CLAUDE.md / todo.md / docs/ / pipeline/ / evals/（2026-08-27）
- [x] sources.yaml 首批 Tier S/A/B 信源 13 个（Anthropic 用社区镜像，见 decisions.md D8）
- [x] pipeline 六个 stage 全链路跑通（真实抓取 53 条入库）
- [x] SQLite 入库 + data/feed/*.json 产出
- [x] 无 API key 降级路径验证（--no-llm 两次运行，跨天去重与保守降级路由均正常）
- [x] 三家 key 配置就位（DeepSeek 主力 / GLM 备用 / Kimi 裁判，见 decisions.md D10）
- [x] 跨厂商降级链 + 错误语义分类（修复 429≠限流 的真实 bug，见 D11）
- [x] 12 个回归测试（`python3 -m unittest discover evals`）全绿
- [x] 【主人】三家账户充值完成
- [x] 模型适配层：kimi-k3 温度约束 + 三家推理模型 token 倍率 + 空输出自愈（D12）
- [x] 内容获取降级链：直抓 → Jina Reader → RSS 简介（修 OpenAI 403，D13）
- [x] 幻觉根因修复：信息不足时走"简介模式"，不许模型硬编（D14）
- [x] 并发化：抓取与三个 LLM stage 线程池并发 + 共享状态加锁（D15）
- [x] 带 LLM 全量跑通（36 条 / 13 信源 / 0 错误），人工抽查摘要质量
- [x] 幻觉第二根因修复：元数据被当正文（D18），受控实验验证
- [x] docs/mechanisms.md 六大机制人话讲解（面试脱稿用）
- [x] 黄金集草稿已导出（26 条）→ `evals/golden_set/golden_draft.jsonl`
- [ ] 🔴【主人】复核黄金集草稿，改完另存为 golden.jsonl
- [x] evals/run_eval.py v1（规则校验通过 0 违规；黄金集对比逻辑就绪，等标注数据）
- [x] docs/PRD.md 初版（含 2026-08-27 联网竞品扫描：TLDR AI/AlphaSignal/smol.ai/开源三项目）
- [x] docs/decisions.md D1–D19（每条=一道面试题答案）
- [ ] 【主人】Week 1 验收

## Week 2 — 前端 + HITL + 上线

- [ ] React 前端：feed 页（Top 精选 + 列表 + tier 徽章）、筛选（主题 × 时间维度）、「机制」页
- [ ] 【主人】建 GitHub 仓库（repo 名 airadar）并首次 push
- [ ] GitHub Actions daily.yml 每日 cron + 数据 commit 回 repo
- [ ] HITL：中置信度条目 → GitHub Issue 勾选卡片 → 下次运行回读 → feedback.jsonl
- [ ] 【主人】连 Vercel 部署前端
- [ ] 【主人】Week 2 验收：线上 URL 可访问，pipeline 每天自动跑

## Week 3 — 记忆/趋势 + 评测深化

- [ ] memory.py：话题热度 7/30/90 天滚动窗口 + 生命周期标注 + 话题时间线
- [ ] 前端趋势页
- [ ] LLM-as-Judge 幻觉评测（裁判用另一家模型）
- [ ] DeepSeek / Kimi / GLM 多模型对比实验 → eval_report.md
- [ ] 反馈回流：feedback.jsonl → 信源 tier 调整建议
- [ ] 信源扩充（含 RSSHub 公众号尽力而为）+ 关键词搜索
- [ ] 【主人】Week 3 验收

## Week 4 — 打磨 + 面试武装

- [ ] decisions.md 整理成完整故事线
- [ ] 运行数据量化统计（连续天数/处理条数/筛除率/审核通过率/幻觉率）
- [ ] 3 分钟 demo 演示路径 + 简历条目产出
- [ ] （进度超前才做）最简对话式问答或语义搜索

## 日常（上线后每天）

- [ ] 【主人】~2 分钟 HITL 审批 + 真实使用吐槽
