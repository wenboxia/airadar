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
| `archive.json` | **知识库**：长期留存内容（时效类未经人工认可的 14 天后退出） |
| `pending.json` | 待人工审批队列 |
| `trends.json` | 话题热度 7/30/90 天窗口 + 生命周期 + 时间线 |
| `stats.json` | 运行统计 + **信源注册表**（前端据此渲染分层表，不硬编码） |

## 五个视图

- **今日 / 本周**：分层漏斗——Top 精选 + 完整列表，双轴筛选（分类 × 时效）
- **知识库**：不受 7 天限制，全库搜索
- **趋势**：话题热度榜 + 可展开的时间线。**知识库跨度不足 21 天时不给生命周期结论**——数据太短时任何话题都像"刚萌芽"，那是错觉不是发现
- **待审**：中置信度队列，跳转 GitHub Issue 审批
- **机制**：产品的自我说明书——pipeline 流程、信源分层、置信度路由、三条降级链、评测方法、最近一次运行的真实读数

## 设计

「仪表盘 / 信号情报终端」风格（decisions.md D20）。签名元素是右栏的**雷达示波器**——绑真实数据：
半径 = 综合分（越靠中心越可信）、角度 = 分类扇区、颜色 = 时效（琥珀）/ 长期价值（青绿）。
筛选时雷达同步更新，切到待审队列时光点全部落在外圈。

字体：Instrument Serif（刊头）+ IBM Plex Mono（数据读数）+ IBM Plex Sans SC（中文正文）。

## 部署

GitHub Pages（`.github/workflows/pages.yml`），数据更新后自动重新部署。
`vite.config.ts` 的 `base` 由 `AIRADAR_BASE` 环境变量切换，因此同一份代码也能直接部署到 Vercel 根路径。
