# CLAUDE.md — AIRadar 开发守则

先读 `start_prompt.md` 了解项目全貌。本文件只放：核心原则、常用命令、代码约定。

## 核心原则（按优先级排序）

1. **一切服务于面试叙事**：本项目是主人的面试作品。每个重要设计决策必须记入 `docs/decisions.md`（格式：背景/选项/取舍/为什么）。过度设计和堆名词是天敌。
2. **评测驱动**：改 prompt、换模型、调阈值 → 必须跑 `python evals/run_eval.py`，结果自动存 `evals/results/`。不许凭感觉说"效果变好了"。
3. **兜底优先**：任何外部依赖（信源、LLM、网络）都可能失败。单点失败不允许阻塞整条 pipeline；没有 API key 时 pipeline 必须能走降级路径跑通。
4. **依赖极简**：Python 端只允许 requests / feedparser / PyYAML / beautifulsoup4 + 标准库。加新依赖前先问：标准库能不能做？
5. **数据不手改**：`data/` 下所有文件由 pipeline 生成，人只通过 HITL 渠道（审批 issue / CLI）影响数据。

## 常用命令

```bash
# 跑完整 pipeline（抓真实信源；无 AIRADAR_LLM_API_KEY 时自动走降级路径）
python3 -m pipeline.main

# 限量试跑（每个信源最多抓 N 条，调试用）
python3 -m pipeline.main --limit 5

# 不调用 LLM（强制降级路径，零成本）
python3 -m pipeline.main --no-llm

# 跑评测
python3 evals/run_eval.py

# 跑单元测试（错误分类与降级链的回归测试）
python3 -m unittest discover evals -v

# 前端开发
cd web && npm run dev
```

## 环境变量（.env 不入库，参考 .env.example）

三个角色（分工理由见 docs/decisions.md D10）：

- **主力**（干活）：`AIRADAR_LLM_*` — DeepSeek `deepseek-v4-pro`
- **备用**（兜底）：`AIRADAR_FALLBACK_*` — GLM `glm-5.3`，主力永久性故障时自动接管
- **裁判**（评测）：`AIRADAR_JUDGE_*` — Kimi `kimi-k3`，须与主力不同家（避免 self-preference bias）

⚠️ 模型版本变化快，写死前先查 `curl $BASE_URL/models -H "Authorization: Bearer $KEY"`，别靠记忆。

## 代码约定

- Python 3.10 兼容（本机 pyenv 3.10.9），不用 3.11+ 特性
- 每个 pipeline stage = 一个 skill：文件头定义 `MANIFEST = {name, version, input, output, eval_cases}`，实现统一契约 `run(items: list[Item], ctx: Context) -> list[Item]`
- LLM 输出一律要求 JSON 并用 `llm.py` 里的宽容解析器解析；解析失败按降级处理，不许裸崩
- 中文注释；注释只写"为什么"，不写"是什么"
- 所有时间存 UTC ISO 格式，展示层再转
