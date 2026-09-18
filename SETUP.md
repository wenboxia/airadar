# 运维手册

> 项目**已经上线并每天自动运行**，本文件是日常操作与故障排查参考。
> 建仓库、配密钥、部署这些一次性工作 Claude 已经代做完了。

## 现状

| | |
|---|---|
| 线上地址 | https://wenboxia.github.io/airadar/ |
| 仓库 | https://github.com/wenboxia/airadar |
| 自动运行 | 每天北京时间 **05:00**（GitHub Actions；避开 DeepSeek 峰时） |
| 部署方式 | **GitHub Pages**：每日工作流 `daily.yml` 跑完数据后接着部署；`pages.yml` 只在人手推送前端代码或手动触发时部署 |

---

## 日常两件事

### 1. 处理审批 issue（约 3 分钟）

每周一（北京时间早上）定时任务开一张审批单，共 12 条，分三块：待审候选 7 条（系统拿不准的，按最新打分标准从高到低）、自动发布抽查 4 条（从最近两周自动发布的里随机抽）、旧池捞回 1 条（已出队的旧条目）。
网站「待审」页显示的就是这张单子，只能看，勾选在 GitHub 上做。
上一张单子开了 6 天以上还没关，会先按过期关掉——**没勾的条目保持待审，不会被当成你否决的**。

1. 打开 https://github.com/wenboxia/airadar/issues （找带 `airadar-approval` 标签的）
2. **每块的勾选含义不一样，先看小标题**：待审候选勾上 = 收录；**自动发布抽查勾上 = 撤下**（留空 = 认可它留着）；旧池捞回勾上 = 收录（留空 = 永久出队）。不用写理由
3. **滑到底部点 `Close issue`**

> ⚠️ **关闭 issue 才算提交。** 只勾不关，系统不会回收你的决策——关闭相当于点「确认」。

次日运行会自动回收决策、更新知识库、并根据通过率给出信源策略建议。

### 2. 黄金集扩充（可选；v2 已于 09-17 标完 111 条）

只增不删；要扩充时用下面的命令导出新一批，标错的用 `"deprecated": true` 标记。

```bash
cd ~/Desktop/airadar
python3 evals/prelabel.py --n 25      # 按分数段分层导出新一批草稿
python3 evals/review_golden.py        # 逐条标注
```

按 `y`（收录）/ `n`（筛掉）/ `s`（跳过）/ `q`（存盘退出）；收录的会问分类（v2 起黄金集不标分类，直接回车跳过）并让你写一句理由。
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
Actions 的每日工作流里 scan → build → deploy 三个 job 依次跑完才算更新。三个都绿了但页面还旧，多半是浏览器缓存，强制刷新（Cmd+Shift+R）。
注意：不能指望数据提交去触发 `pages.yml`——每日回写用的是 GITHUB_TOKEN，这类提交不会触发其他工作流（线上数据曾因此停在 08-29）。

**想换模型或加信源？**
- 换模型：先在黄金集上离线对比（`evals/triage_prompt_eval.py --model …`、`evals/summary_model_eval.py`，见 CLAUDE.md），再改 `.env`（本地）+ GitHub 仓库 Settings → Secrets and variables → Actions（云端）
- 加信源：改 `pipeline/sources.yaml`。**按信源本身是什么直接定级**（官方 S / 专家 A / 媒体 B / 跨界 C），发不发交给模型打分；记得填 `expected_cadence_days` 给信源体检用。评估后决定不抓的写成 X 级并写明理由（D38、D40）

---

## 可选：额外接一个 Vercel 域名

GitHub Pages 已经够用。如果想要更简洁的网址（`airadar.vercel.app`）：

1. vercel.com 用 GitHub 账号登录 → Add New → Project → 选 `airadar` → Import
2. **配置全默认**（仓库根目录的 `vercel.json` 已配好构建命令与输出目录）
3. Deploy

两者可共存，都指向同一个仓库，代码一行不用改（`vite.config.ts` 的 base 路径由 `AIRADAR_BASE` 环境变量切换）。
