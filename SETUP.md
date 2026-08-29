# 运维手册

> 项目**已经上线并每天自动运行**，本文件是日常操作与故障排查参考。
> 建仓库、配密钥、部署这些一次性工作 Claude 已经代做完了。

## 现状

| | |
|---|---|
| 线上地址 | https://wenboxia.github.io/airadar/ |
| 仓库 | https://github.com/wenboxia/airadar |
| 自动运行 | 每天北京时间 **07:00**（GitHub Actions） |
| 部署方式 | **GitHub Pages**（`.github/workflows/pages.yml`，数据更新后自动重新部署） |

---

## 日常两件事

### 1. 处理审批 issue（约 3 分钟）

每天定时任务会把「系统不确定」的内容（综合分 50–75）开成一张勾选清单。

1. 打开 https://github.com/wenboxia/airadar/issues （找带 `airadar-approval` 标签的）
2. **想收录就打勾，不想要就留空**，不用写理由
3. **滑到底部点 `Close issue`**

> ⚠️ **关闭 issue 才算提交。** 只勾不关，系统不会回收你的决策——关闭相当于点「确认」。

次日运行会自动回收决策、更新知识库、并根据通过率给出信源策略建议。

### 2. 黄金集标注（有空再做，目标 100 条）

```bash
cd ~/Desktop/airadar
python3 evals/prelabel.py --n 25      # 按分数段分层导出新一批草稿
python3 evals/review_golden.py        # 逐条标注
```

按 `y`（收录）/ `n`（筛掉）/ `s`（跳过）/ `q`（存盘退出）；收录的会让你选分类并写一句理由。
**每条自动存盘，随时可停**，下次跑同一条命令接着来，已标过的自动跳过。

标准只有一句：**三个月后你还愿意在知识库里搜到它吗？**

---

## 本地常用操作

```bash
cd ~/Desktop/airadar

python3 -m pipeline.main --limit 5     # 本地试跑（每信源 5 条）
python3 -m pipeline.sources_health     # 信源体检：找出停更/失效的源
python3 evals/run_eval.py              # 跑评测看当前准确率
python3 -m pipeline.hitl review        # 本地审批（不想开 GitHub 时）
cd web && npm run dev                  # 本地看前端
```

---

## 故障排查

**Actions 跑失败了？**
打开仓库 Actions 标签看红色那次的日志。常见原因：
- **模型余额不足** → 去对应平台充值（DeepSeek: platform.deepseek.com｜GLM: open.bigmodel.cn｜Kimi: platform.moonshot.cn）
- **数据回写被拒** → 已内置 3 次重试，仍失败说明有并发推送，重跑一次即可

**网站数据没更新？**
Actions 跑完会 commit 数据，Pages 工作流随后自动部署。两个工作流都绿了但页面还旧，多半是浏览器缓存，强制刷新（Cmd+Shift+R）。

**想换模型或加信源？**
- 换模型：改 `.env`（本地）+ GitHub 仓库 Settings → Secrets and variables → Actions（云端）
- 加信源：改 `pipeline/sources.yaml`。**新信源一律先放 D 级**——强制人工过审，通过率高了系统会建议升级

---

## 可选：额外接一个 Vercel 域名

GitHub Pages 已经够用。如果想要更简洁的网址（`airadar.vercel.app`）：

1. vercel.com 用 GitHub 账号登录 → Add New → Project → 选 `airadar` → Import
2. **配置全默认**（仓库根目录的 `vercel.json` 已配好构建命令与输出目录）
3. Deploy

两者可共存，都指向同一个仓库，代码一行不用改（`vite.config.ts` 的 base 路径由 `AIRADAR_BASE` 环境变量切换）。
