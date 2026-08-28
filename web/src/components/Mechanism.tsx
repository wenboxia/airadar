import type { Stats } from '../types'

/** 「机制」页 —— 产品的自我说明书。
 *  面试官打开这一页就能看懂整个系统怎么运转、数据从哪来、质量怎么保证。 */

const PIPELINE = [
  { n: 'fetch', zh: '抓取', d: '13 个分层信源。LLM 不联网，联网由确定性代码完成——信源可控、原文可存、幻觉可查。' },
  { n: 'dedupe', zh: '去重', d: 'URL 规范化 + 标题相似度；同一事件多源报道时保留高等级信源。' },
  { n: 'triage', zh: '分层评分', d: '综合分 = 信源等级 55% + 模型价值评分 45% → 三分支路由。' },
  { n: 'summarize', zh: '双层摘要', d: '一句话 + 300 字，生成后对照原文做幻觉自检。原文不足时降为「简介模式」，不许扩写。' },
  { n: 'classify', zh: '分类归档', d: '主题分类 + 时间维度（时效 / 长期价值）。' },
  { n: 'publish', zh: '入库发布', d: '写 SQLite 知识库 + 生成前端数据 + 开人工审批队列。' },
]

const TIERS = [
  { t: 'S', label: '官方一手', d: 'OpenAI / Anthropic / DeepMind 官方博客、arXiv、MCP 官方', base: 90 },
  { t: 'A', label: '公认专家', d: 'Simon Willison、Karpathy、Lilian Weng 等个人博客；HN 高分帖', base: 78 },
  { t: 'B', label: '垂直媒体', d: 'GitHub Trending、机器之心等', base: 62 },
  { t: 'C', label: '跨界视角', d: '投资 / 研究机构', base: 48 },
  { t: 'D', label: '待观察', d: '新发现信源——永远不能自动发布，强制人工过审', base: 30 },
  { t: 'X', label: '已屏蔽', d: '验证过的低质源，直接过滤', base: 0 },
]

const FALLBACKS = [
  { name: '内容获取', chain: ['直接抓取', 'Jina Reader', '保留 RSS 简介'], real: 'OpenAI 官网 Cloudflare 403，靠 Reader 拿到 6000 字正文' },
  { name: '模型调用', chain: ['DeepSeek v4-pro', 'GLM 5.3', '标记不可用'], real: '真实运行中触发过 6 次跨厂商切换' },
  { name: '业务降级', chain: ['正常发布', 'S/A 放行·其余送审', '仅标题入库'], real: '三家账户欠费时做过完整降级演练，pipeline 照常跑完' },
]

function Section({
  label,
  title,
  children,
}: {
  label: string
  title: string
  children: React.ReactNode
}) {
  return (
    <section className="border-b border-rule py-10">
      <div className="mb-5">
        <div className="font-mono text-[10px] tracking-[0.25em] text-scope uppercase">
          {label}
        </div>
        <h2
          className="mt-1.5 text-[32px] leading-tight text-ink"
          style={{ fontFamily: 'var(--font-display)' }}
        >
          {title}
        </h2>
      </div>
      {children}
    </section>
  )
}

export function Mechanism({ stats }: { stats: Stats | null }) {
  const last = stats?.runs?.[0]?.stats
  const budget = last?.budget

  return (
    <div className="mx-auto max-w-3xl px-6 pb-24 lg:px-0">
      <Section label="Architecture" title="一条 pipeline，六个可插拔环节">
        <ol className="space-y-0">
          {PIPELINE.map((s, i) => (
            <li key={s.n} className="flex gap-4 border-t border-rule py-3.5">
              <span className="w-6 shrink-0 pt-0.5 font-mono text-[11px] text-ink-faint">
                {String(i + 1).padStart(2, '0')}
              </span>
              <div>
                <div className="flex items-baseline gap-2">
                  <span className="font-mono text-[12px] text-signal">{s.n}</span>
                  <span className="text-[13px] text-ink">{s.zh}</span>
                </div>
                <p className="mt-1 text-[13.5px] leading-[1.8] text-ink-dim">{s.d}</p>
              </div>
            </li>
          ))}
        </ol>
        <p className="mt-4 font-mono text-[11px] leading-relaxed text-ink-faint">
          每个环节都是一个 skill：统一契约 run(items, ctx) → items，自带 MANIFEST 与评测用例。
          手写编排而非用 LangGraph —— 拓扑是线性链加一个条件路由，框架的复杂度换不来收益。
        </p>
      </Section>

      <Section label="Trust" title="信源分层：谁值得被相信">
        <div className="space-y-0">
          {TIERS.map((t) => (
            <div key={t.t} className="flex items-baseline gap-4 border-t border-rule py-3">
              <span
                className={`w-7 shrink-0 font-mono text-[13px] ${
                  t.t === 'S' ? 'text-signal' : t.t === 'A' ? 'text-scope' : 'text-ink-faint'
                }`}
              >
                {t.t}
              </span>
              <div className="flex-1">
                <div className="text-[13px] text-ink">{t.label}</div>
                <div className="mt-0.5 text-[12px] text-ink-dim">{t.d}</div>
              </div>
              <span className="font-mono text-[11px] text-ink-faint">基础分 {t.base}</span>
            </div>
          ))}
        </div>
        <p className="mt-4 text-[13.5px] leading-[1.8] text-ink-dim">
          <span className="text-signal">载体无罪，看运营主体。</span>
          微信公众号不是原罪——字节跳动技术团队的公众号与其官网博客权威性等价。
          这里建的是「运营主体信誉库」，不是「平台黑名单」。
        </p>
      </Section>

      <Section label="Human-in-the-loop" title="系统知道自己什么时候不确定">
        <div className="grid gap-3 sm:grid-cols-3">
          {[
            { r: '≥ 75 分', a: '自动发布', c: 'text-signal border-signal/40' },
            { r: '50 – 75 分', a: '人工审批', c: 'text-scope border-scope/40' },
            { r: '< 50 分', a: '丢弃留档', c: 'text-ink-faint border-rule' },
          ].map((b) => (
            <div key={b.r} className={`border p-4 ${b.c}`}>
              <div className="font-mono text-[11px]">{b.r}</div>
              <div className="mt-1 text-[14px] text-ink">{b.a}</div>
            </div>
          ))}
        </div>
        <p className="mt-4 text-[13.5px] leading-[1.8] text-ink-dim">
          中间区开成 GitHub Issue 勾选卡片等人审批，决策写回 feedback 日志。
          某信源若连续被否，系统给出降级它的建议——人的判断回流成信源信誉的修正，形成数据飞轮。
          被丢弃的内容同样入库：<span className="text-ink">「筛掉了什么」和「收了什么」同样是筛选器质量的证据。</span>
        </p>
      </Section>

      <Section label="Resilience" title="三条同构的降级链">
        <div className="space-y-4">
          {FALLBACKS.map((f) => (
            <div key={f.name} className="border-t border-rule pt-3">
              <div className="mb-2 text-[13px] text-ink">{f.name}</div>
              <div className="flex flex-wrap items-center gap-2">
                {f.chain.map((c, i) => (
                  <span key={c} className="flex items-center gap-2">
                    <span
                      className={`border px-2 py-1 font-mono text-[11px] ${
                        i === 0
                          ? 'border-signal/40 text-signal'
                          : i === f.chain.length - 1
                            ? 'border-rule text-ink-faint'
                            : 'border-scope/40 text-scope'
                      }`}
                    >
                      {c}
                    </span>
                    {i < f.chain.length - 1 && (
                      <span className="font-mono text-ink-faint">→</span>
                    )}
                  </span>
                ))}
              </div>
              <p className="mt-2 font-mono text-[11px] text-ink-faint">实测：{f.real}</p>
            </div>
          ))}
        </div>
        <p className="mt-4 text-[13.5px] leading-[1.8] text-ink-dim">
          降级方向永远是<span className="text-signal">收紧</span>而不是放宽——
          宁可多麻烦人，不可错发内容。所有降级都留痕可审计。
        </p>
      </Section>

      <Section label="Evaluation" title="怎么证明「效果变好了」不是错觉">
        <ul className="space-y-3">
          {[
            ['黄金测试集', '人工标注的标准答案，算筛选 precision / recall 与分类准确率。标注标准：三个月后你还愿意在库里搜到它吗？'],
            ['规则校验', '字段完整性、链接合法性、状态合法性——零成本，每次必跑。'],
            ['LLM-as-Judge', '摘要忠实度核查。裁判故意用与主力不同家的模型（Kimi 评 DeepSeek），避免模型给自己打高分的偏袒。'],
            ['回归对比', '每次结果存档，跨版本可比。改 prompt、换模型、调阈值 → 必须重跑。'],
          ].map(([k, v]) => (
            <li key={k} className="border-t border-rule pt-3">
              <div className="text-[13px] text-signal">{k}</div>
              <p className="mt-1 text-[13.5px] leading-[1.8] text-ink-dim">{v}</p>
            </li>
          ))}
        </ul>
      </Section>

      {budget && (
        <Section label="Telemetry" title="最近一次运行的真实读数">
          <dl className="grid grid-cols-2 gap-px border border-rule bg-rule sm:grid-cols-4">
            {[
              ['抓取', last?.fetch?.total, '条'],
              ['LLM 调用', budget.calls_made, '次'],
              ['Token', (budget.tokens_used / 1000).toFixed(1), 'K'],
              ['幻觉打标', last?.summarize?.hallucination_flagged, '条'],
            ].map(([k, v, u]) => (
              <div key={String(k)} className="bg-panel p-4">
                <dt className="font-mono text-[9px] tracking-[0.2em] text-ink-faint uppercase">
                  {k}
                </dt>
                <dd className="mt-1 font-mono text-xl text-ink">
                  {String(v ?? '—')}
                  <span className="ml-0.5 text-[11px] text-ink-faint">{u}</span>
                </dd>
              </div>
            ))}
          </dl>
          {last?.calls_by_provider && (
            <p className="mt-3 font-mono text-[11px] text-ink-faint">
              供应商分布：
              {Object.entries(last.calls_by_provider)
                .map(([k, v]) => `${k === 'primary' ? '主力' : '备用'} ${v} 次`)
                .join(' · ')}
              {(last.calls_by_provider.fallback ?? 0) > 0 && (
                <span className="text-scope">　← 降级链真实触发过</span>
              )}
            </p>
          )}
        </Section>
      )}
    </div>
  )
}
