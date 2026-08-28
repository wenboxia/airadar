# 黄金测试集标注协议

`golden.jsonl` 每行一条人工标注，是评测 pipeline 筛选/分类准确率的"标准答案"。

## 标注格式

```json
{"url": "https://...", "include": true, "category": "Agent 工程", "tier_expect": "S", "note": "为什么该收/不该收（一句话，面试会被追问）"}
```

- `include`：这条内容**值不值得进知识库**（true/false）。判断标准：三个月后还愿意在库里搜到它吗？纯营销、旧闻炒冷饭、与关注领域无关 → false
- `category`：正确分类（可选，从 pipeline/stages/classify.py 的 CATEGORIES 里选）
- `tier_expect`：该信源应属层级（可选，用于校准 sources.yaml）
- `note`：标注理由，必填——这是主人的品味记录，也是面试素材

## 标注流程（人机协同）

1. Claude 从真实抓取数据中挑候选并**预标注**（给出建议 include + 理由）
2. **主人逐条复核**：同意/修改，修改时写自己的理由
3. 存入 golden.jsonl；黄金集只增不删（错误标注用 `"deprecated": true` 标记）

## 目标规模

Week 1：30 条起步 → Week 3：80–100 条（覆盖各信源类型、各分类、edge case：
营销软文、重复报道、标题党、高质量但领域无关……）
