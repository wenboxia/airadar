# AIRadar 前端

React + Vite + TypeScript + Tailwind v4。纯静态站，无后端——数据来自 pipeline 产出的 JSON。

## 开发

```bash
npm run dev      # 自动同步 ../data/feed → public/data 后启动
npm run build    # 构建（AIRADAR_BASE=/airadar/ 时走 GitHub Pages 子路径）
npm run sync     # 只同步数据，不启动
```

## 数据来源

pipeline 每次运行产出到 `../data/feed/`，构建时由 `scripts/sync-data.mjs` 拷进 `public/data/`：

| 文件 | 内容 |
|---|---|
| `latest.json` | 本次运行发布的条目 + Top 5 精选 |
| `week.json` | 近 7 天已发布 |
| `archive.json` | **知识库**：长期留存内容（时效类不是审批时勾收的，14 天后退出） |
| `review.json` | 本周审批单原样（`hitl open` 写出）：待审 7 / 抽查 4 / 捞回 1；没开单时文件不存在，前端只显示下一张何时开 |
| `pending.json` | 本次运行新送审的条目（前端不读，留作运行记录） |
| `trends.json` | 话题热度 7/30/90 天窗口 + 生命周期 + 时间线 |
| `stats.json` | 运行统计 + **信源注册表**（前端据此渲染分层表，不硬编码） |

## 五个视图

- **今日 / 本周**：分层漏斗——Top 精选 + 完整列表，双轴筛选（分类 × 时效）
- **知识库**：不受 7 天限制，全库搜索
- **趋势**：话题热度榜 + 可展开的时间线。**知识库跨度不足 21 天时不给生命周期结论**——数据太短时任何话题都像"刚萌芽"，那是错觉不是发现
- **待审**：本周那张审批单的只读镜像（三块：待审候选 / 自动发布抽查 / 旧池捞回），勾选在 GitHub Issue 里做
- **机制**：产品的自我说明书——pipeline 流程、信源分层、置信度路由、三条降级链、评测方法、最近一次运行的真实读数

## 设计

「仪表盘 / 信号情报终端」风格（decisions.md D20）。签名元素是右栏的**雷达示波器**——绑真实数据：
半径 = 综合分（越靠中心越可信）、角度 = 分类扇区、颜色 = 时效（琥珀）/ 长期价值（青绿）。
筛选时雷达同步更新。

字体：Instrument Serif（刊头）+ IBM Plex Mono（数据读数）+ IBM Plex Sans SC（中文正文）。

## 部署

GitHub Pages。每日数据由 `.github/workflows/daily.yml` 的 scan → build → deploy 一条龙部署；`pages.yml` 只在人手推送 `web/**` 或手动触发时部署。数据提交用的是 GITHUB_TOKEN，不会触发 pages.yml。
`vite.config.ts` 的 `base` 由 `AIRADAR_BASE` 环境变量切换，因此同一份代码也能直接部署到 Vercel 根路径。
