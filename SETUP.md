# 上线操作清单（需要主人自己做，约 10 分钟）

涉及账号和密钥的操作只能你来做。照着做完，AIRadar 就每天自动跑并在线可访问了。

## 1. 建 GitHub 仓库并推送（3 分钟）

在 github.com 新建一个仓库，名字填 `airadar`，**不要**勾选任何初始化选项（README/gitignore/license 都别勾）。
建好后回到终端，把下面命令里的 `你的用户名` 换成你的 GitHub 用户名：

```bash
git remote add origin https://github.com/你的用户名/airadar.git && git branch -M main && git push -u origin main
```

## 2. 配 GitHub Secrets 和 Variables（4 分钟）

进仓库页面 → Settings → Secrets and variables → Actions。

**Secrets 标签页**（点 New repository secret，加 2 个）：

| Name | Value |
|---|---|
| `AIRADAR_LLM_API_KEY` | 你的 DeepSeek key |
| `AIRADAR_FALLBACK_API_KEY` | 你的 GLM key |

**Variables 标签页**（点 New repository variable，加 4 个）：

| Name | Value |
|---|---|
| `AIRADAR_LLM_BASE_URL` | `https://api.deepseek.com` |
| `AIRADAR_LLM_MODEL` | `deepseek-v4-pro` |
| `AIRADAR_FALLBACK_BASE_URL` | `https://open.bigmodel.cn/api/paas/v4` |
| `AIRADAR_FALLBACK_MODEL` | `glm-5.3` |

## 3. 手动触发一次验证（2 分钟）

仓库页面 → Actions 标签 → 左侧点「AIRadar 每日扫描」→ 右侧 Run workflow 按钮 → 绿色 Run workflow。

跑完（约 15–20 分钟）应该全绿，并且：
- 仓库里多一个 commit「扫描 2026-XX-XX」
- Issues 里多一张待审批清单

之后每天北京时间早上 7 点会自动跑。

## 4. 连 Vercel 部署前端（3 分钟）

1. 打开 vercel.com，用 GitHub 账号登录
2. Add New → Project → 选 `airadar` 仓库 → Import
3. **所有配置保持默认**（仓库根目录的 `vercel.json` 已经配好了构建命令和输出目录）
4. 点 Deploy

部署完会给你一个 `airadar-xxx.vercel.app` 的网址——这就是可以写进简历、发给面试官的链接。

之后每次 pipeline 跑完把数据 commit 回仓库，Vercel 会自动重新部署，网站数据自动更新。

## 日常使用

- **每天早上**：打开网站看今日精选；「待审」标签有数字就去 GitHub Issues 勾选审批（约 2 分钟）
- **本地调试**：`python3 -m pipeline.main --limit 5`（每源限 5 条）
- **本地审批**（不想开 GitHub 时）：`python3 -m pipeline.hitl review`
- **本地看前端**：`cd web && npm run dev`
