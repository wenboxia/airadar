# CLAUDE.md — AIRadar 开发守则

先读 `start_prompt.md` 了解项目全貌。本文件只放：核心原则、常用命令、代码约定。

## 核心原则（按优先级排序）

1. **一切服务于面试叙事**：本项目是主人的面试作品。每个重要设计决策必须记入 `docs/decisions.md`（格式：背景/选项/取舍/为什么）。过度设计和堆名词是天敌。

2. **评测驱动**：改 prompt、换模型、调阈值 → 必须跑 `python3 evals/run_eval.py`，结果自动存 `evals/results/`。不许凭感觉说"效果变好了"。

3. **指标不会报错，只会说谎**（D22/D29/D31/D32 四次踩坑换来的）。新增或解读任何指标前先问三件事：
   - **匹配对象的性质吗？** 查询型信源没有"最新一篇"（D22）
   - **匹配输出的结构吗？** 三分类路由不能用二分类 precision/recall——把"我不确定"算成"我认为该收"是把谨慎当错误（D29）
   - **数据的跨度与来源可靠吗？** 18 天数据算不出 90 天趋势（D31）；裁判限流不能被统计成幻觉（D32）

   套错的指标不会抛异常，只会给出看似合理实则误导的数字——**"看起来合理"正是它危险的地方**。

4. **能从上下文删掉的信息，不要靠指令禁止使用**（D34）。指令是概率性防护，删除是结构性防护。摘要环节曾用"元数据（仅供参考）"标注信源名并加铁律禁止，模型照样把它写成文章发布方；改成根本不传之后问题消失，且摘要质量反而提升。

5. **兜底优先**：任何外部依赖（信源、LLM、网络）都可能失败。单点失败不允许阻塞整条 pipeline；没有 API key 时 pipeline 必须能走降级路径跑通。降级方向永远是**收紧**而非放宽（宁可送人工审，不可错发）。

6. **依赖极简**：Python 端只允许 requests / feedparser / PyYAML / beautifulsoup4 + 标准库。加新依赖前先问：标准库能不能做？

7. **数据不手改**：`data/` 下所有文件由 pipeline 生成，人只通过 HITL 渠道（审批 issue / CLI）影响数据。

## 常用命令

```bash
# ── pipeline ──────────────────────────────────────────
python3 -m pipeline.main                 # 完整运行（无 key 时自动降级）
python3 -m pipeline.main --limit 5       # 每信源限 5 条（调试）
python3 -m pipeline.main --no-llm        # 强制降级路径，零成本
python3 -m pipeline.sources_health       # 信源体检：找出"沉默死掉"的源
python3 -m pipeline.memory               # 单独重算话题热度与生命周期

# ── 人工审批（HITL）────────────────────────────────────
python3 -m pipeline.hitl review          # 本地 CLI 审批（无 GitHub 时兜底）
python3 -m pipeline.hitl open            # 把待审条目开成 GitHub Issue
python3 -m pipeline.hitl collect         # 回收已关闭 issue 的勾选结果

# ── 评测 ──────────────────────────────────────────────
python3 evals/run_eval.py                # 规则校验 + 黄金集三路径评测
python3 evals/prelabel.py --n 25         # 按分数段分层导出待标注草稿
python3 evals/review_golden.py           # 逐条标注（断点续标，自动跳过已标）
python3 evals/judge_hallucination.py --n 8 --k 3   # LLM-as-Judge 幻觉评测
python3 -m unittest discover evals -v    # 27 个回归测试

# ── 前端 ──────────────────────────────────────────────
cd web && npm run dev                    # 本地开发（自动同步 data/feed）
cd web && npm run build                  # 构建（AIRADAR_BASE=/airadar/ 走 Pages 子路径）
```

## 环境变量（.env 不入库，参考 .env.example）

三个角色（分工理由见 docs/decisions.md D10）：

- **主力**（干活）：`AIRADAR_LLM_*` — DeepSeek `deepseek-v4-pro`
- **备用**（兜底）：`AIRADAR_FALLBACK_*` — GLM `glm-5.3`，主力永久性故障时自动接管
- **裁判**（评测）：`AIRADAR_JUDGE_*` — Kimi `kimi-k3`，须与主力不同家（避免 self-preference bias）

云端同名配置在 GitHub Secrets（两个 key）与 Variables（四个 URL/model）。

⚠️ **任何版本号都先查再写，不许凭记忆**（这个错误犯过两次，见 decisions.md D26）：
- 模型 ID：`curl $BASE_URL/models -H "Authorization: Bearer $KEY"`
- npm 包版本：从 `node_modules/<pkg>/package.json` 读实际值，改完在干净目录跑一遍 `npm ci` 验证
- 训练知识对版本这类高频变动信息天然过期，失败方式常是"本地好好的，一上线就挂"

## 代码约定

- Python 3.10 兼容（本机 pyenv 3.10.9），不用 3.11+ 特性。注意 `datetime.fromisoformat` 不认 `Z` 后缀，统一用 `fetch.parse_iso`
- 每个 pipeline stage = 一个 skill：文件头定义 `MANIFEST = {name, version, input, output, eval_cases}`，实现统一契约 `run(items: list[Item], ctx: Context) -> list[Item]`
- **系统的判断与人的判断必须分字段存储、永不互相覆盖**（D28）：`auto_status` 是 triage 的原始结论，只有 triage 能写；`status` 是当前状态，HITL 可改。评测一律看 `auto_status`——用 `status` 会让经过审批的条目变成"人评人自己"
- LLM 输出一律要求 JSON 并用 `llm.py` 的宽容解析器解析；解析失败按降级处理，不许裸崩
- 加字段要写 `db.py` 的 `_migrate()` 迁移，**不许靠重跑 pipeline 补数据**——已有运行记录是项目资产
- 中文注释；注释只写"为什么"，不写"是什么"
- 所有时间存 UTC ISO 格式（`+00:00` 形式），展示层再转
