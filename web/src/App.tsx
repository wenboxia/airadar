import { useMemo, useState } from 'react'
import { ItemCard } from './components/ItemCard'
import { Masthead } from './components/Masthead'
import { Mechanism } from './components/Mechanism'
import { Trends } from './components/Trends'
import { RadarScope } from './components/RadarScope'
import { useArchive, useLatest, usePending, useStats, useTrends, useWeek } from './data'
import type { Item } from './types'

type View = 'today' | 'week' | 'archive' | 'trends' | 'pending' | 'mechanism'

function FilterChip({
  active,
  tone = 'signal',
  onClick,
  children,
}: {
  active: boolean
  tone?: 'signal' | 'moss'
  onClick: () => void
  children: React.ReactNode
}) {
  const on = tone === 'moss' ? 'text-moss' : 'text-signal'
  return (
    <button
      onClick={onClick}
      className={`relative pb-0.5 text-[12px] transition-colors ${
        active ? on : 'text-ink-faint hover:text-ink-dim'
      }`}
    >
      {children}
      {active && (
        <span
          className={`absolute inset-x-0 -bottom-0.5 h-px ${
            tone === 'moss' ? 'bg-moss' : 'bg-signal'
          }`}
        />
      )}
    </button>
  )
}

const VIEWS: { id: View; label: string; sub: string }[] = [
  { id: 'today', label: '今日', sub: 'TODAY' },
  { id: 'week', label: '本周', sub: 'WEEK' },
  { id: 'archive', label: '知识库', sub: 'ARCHIVE' },
  { id: 'trends', label: '趋势', sub: 'TRENDS' },
  { id: 'pending', label: '待审', sub: 'QUEUE' },
  { id: 'mechanism', label: '机制', sub: 'HOW' },
]

export default function App() {
  const [view, setView] = useState<View>('today')
  const [cat, setCat] = useState<string | null>(null)
  const [horizon, setHorizon] = useState<'short' | 'long' | null>(null)
  const [q, setQ] = useState('')

  const latest = useLatest()
  const week = useWeek()
  const archive = useArchive()
  const pending = usePending()
  const trends = useTrends()
  const stats = useStats()

  const source =
    view === 'week' ? week
      : view === 'archive' ? archive
        : view === 'pending' ? pending
          : latest
  const allItems = source.data?.items ?? []

  /** 一条内容可属于多个分类，筛选按"包含"匹配 */
  const catsOf = (it: Item) =>
    it.categories?.length ? it.categories : it.category ? [it.category] : []

  const categories = useMemo(
    () => Array.from(new Set(allItems.flatMap(catsOf))).sort(),
    [allItems],
  )

  const items = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return allItems.filter((it) => {
      if (cat && !catsOf(it).includes(cat)) return false
      if (horizon && it.horizon !== horizon) return false
      if (!needle) return true
      return (
        it.title.toLowerCase().includes(needle) ||
        it.summary_short?.toLowerCase().includes(needle) ||
        it.summary_long?.toLowerCase().includes(needle) ||
        it.source.toLowerCase().includes(needle) ||
        it.topics?.some((t) => t.includes(needle))
      )
    })
  }, [allItems, cat, horizon, q])

  const top: Item[] = view === 'today' ? (latest.data?.top ?? []) : []
  const filtered = cat || horizon || q.trim()
  const pendingCount = pending.data?.items?.length ?? 0

  return (
    <div className="min-h-screen">
      <Masthead stats={stats.data} date={latest.data?.date} />

      {/* 导航 */}
      <nav className="sticky top-0 z-20 border-b border-rule bg-void/92 backdrop-blur-sm">
        <div className="mx-auto max-w-6xl px-6 lg:px-10">
          <div className="flex items-stretch gap-1">
            {VIEWS.map((v) => (
              <button
                key={v.id}
                onClick={() => setView(v.id)}
                className={`relative px-3 py-3.5 transition-colors first:pl-0 sm:px-4 ${
                  view === v.id ? 'text-ink' : 'text-ink-faint hover:text-ink-dim'
                }`}
              >
                <span className="text-[13px]">{v.label}</span>
                <span className="ml-1.5 hidden font-mono text-[9px] tracking-[0.15em] sm:inline">
                  {v.sub}
                </span>
                {v.id === 'pending' && pendingCount > 0 && (
                  <span className="ml-1.5 font-mono text-[10px] text-signal">
                    {pendingCount}
                  </span>
                )}
                {view === v.id && (
                  <span className="absolute inset-x-0 bottom-0 h-px bg-signal" />
                )}
              </button>
            ))}

            {/* 桌面端搜索跟导航同行；窄屏另起一行，避免溢出 */}
            {view !== 'mechanism' && view !== 'trends' && (
              <div className="ml-auto hidden items-center sm:flex">
                <input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="搜索标题 / 摘要 / 信源…"
                  className="w-52 border-b border-rule bg-transparent py-1 font-mono text-[11px] text-ink outline-none transition-all placeholder:text-ink-faint focus:w-72 focus:border-signal/60"
                />
              </div>
            )}
          </div>

          {view !== 'mechanism' && view !== 'trends' && (
            <div className="pb-2.5 sm:hidden">
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="搜索标题 / 摘要 / 信源…"
                className="w-full border-b border-rule bg-transparent py-1 font-mono text-[11px] text-ink outline-none placeholder:text-ink-faint focus:border-signal/60"
              />
            </div>
          )}
        </div>
      </nav>

      {view === 'mechanism' ? (
        <Mechanism stats={stats.data} />
      ) : view === 'trends' ? (
        <main className="mx-auto max-w-4xl px-6 py-8 lg:px-10">
          <div className="mb-5">
            <h2
              className="text-[30px] leading-none text-ink"
              style={{ fontFamily: 'var(--font-display)' }}
            >
              话题趋势
            </h2>
            <p className="mt-2 font-mono text-[11px] tracking-wide text-ink-faint">
              中期记忆：同一话题在 7 / 30 / 90 天窗口里的热度演变 · 展开看时间线
            </p>
          </div>
          <Trends data={trends.data} />
        </main>
      ) : (
        <main className="mx-auto max-w-6xl px-6 py-8 lg:px-10">
          {source.error && (
            <div className="border border-alert/40 bg-alert/5 p-5 font-mono text-[12px] text-alert">
              数据加载失败（{source.error}）。请先在项目根目录跑一次
              <span className="text-ink"> python3 -m pipeline.main</span>，
              然后 <span className="text-ink">npm run sync</span>。
            </div>
          )}

          <div className="grid gap-10 lg:grid-cols-[1fr_340px]">
            {/* 主列 */}
            <div className="lg:order-1 lg:pl-9">
              {/* 筛选：两条独立的轴，分行排布，别挤成一团 */}
              {categories.length > 0 && (
                <div className="mb-7 space-y-2.5 border-b border-rule pb-5">
                  <div className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
                    <span className="w-8 shrink-0 font-mono text-[9px] tracking-[0.18em] text-ink-faint uppercase">
                      分类
                    </span>
                    <FilterChip active={!cat} onClick={() => setCat(null)}>
                      全部 <span className="opacity-60">{allItems.length}</span>
                    </FilterChip>
                    {categories.map((c) => (
                      <FilterChip
                        key={c}
                        active={cat === c}
                        onClick={() => setCat(cat === c ? null : c)}
                      >
                        {c}
                      </FilterChip>
                    ))}
                  </div>
                  <div className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
                    <span className="w-8 shrink-0 font-mono text-[9px] tracking-[0.18em] text-ink-faint uppercase">
                      时效
                    </span>
                    <FilterChip active={!horizon} onClick={() => setHorizon(null)}>
                      不限
                    </FilterChip>
                    {(['short', 'long'] as const).map((h) => (
                      <FilterChip
                        key={h}
                        active={horizon === h}
                        tone={h === 'long' ? 'moss' : 'signal'}
                        onClick={() => setHorizon(horizon === h ? null : h)}
                      >
                        {h === 'long' ? '长期价值' : '时效新闻'}
                      </FilterChip>
                    ))}
                  </div>
                </div>
              )}

              {/* 今日精选 */}
              {view === 'today' && top.length > 0 && !filtered && (
                <section className="mb-10">
                  <div className="mb-3 flex items-baseline gap-3">
                    <h2
                      className="text-[30px] leading-none text-ink"
                      style={{ fontFamily: 'var(--font-display)' }}
                    >
                      今日精选
                    </h2>
                    <span className="font-mono text-[10px] tracking-wider text-ink-faint">
                      30 秒读完
                    </span>
                  </div>
                  <div className="border-t border-signal/25">
                    {top.map((it, i) => (
                      <div key={it.id} className="rise" style={{ animationDelay: `${i * 60}ms` }}>
                        <ItemCard it={it} rank={i} />
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* 全部 */}
              <section>
                <div className="mb-3 flex items-baseline gap-3">
                  <h2
                    className="text-[30px] leading-none text-ink"
                    style={{ fontFamily: 'var(--font-display)' }}
                  >
                    {view === 'pending'
                      ? '待人工审批'
                      : view === 'archive'
                        ? '知识库'
                        : filtered
                          ? '筛选结果'
                          : '完整列表'}
                  </h2>
                  <span className="font-mono text-[10px] tracking-wider text-ink-faint">
                    {items.length} 条
                  </span>
                </div>

                {view === 'archive' && (
                  <p className="mb-4 border-l-2 border-moss/40 pl-3 text-[12.5px] leading-relaxed text-ink-dim">
                    长期沉淀的全部内容，不受 7 天窗口限制，可全库搜索。
                    <span className="text-ink">时效类内容会自动过期退出</span>
                    （发布超过 14 天且未经人工认可的），
                    但数据仍留在库里可审计——机器无权替你遗忘你亲手认可过的东西。
                  </p>
                )}

                {view === 'pending' && (
                  <p className="mb-4 border-l-2 border-scope/40 pl-3 text-[12.5px] leading-relaxed text-ink-dim">
                    综合分落在 50–75 之间的内容——
                    <span className="text-ink">系统知道自己不确定，所以交给人</span>。
                    待观察等级（D）的信源即使高分也强制进这里。
                  </p>
                )}

                {items.length === 0 && !source.error ? (
                  <p className="py-10 text-center font-mono text-[12px] text-ink-faint">
                    {source.data ? '没有匹配的内容' : '加载中…'}
                  </p>
                ) : (
                  <div className="border-t border-rule">
                    {items.map((it, i) => (
                      <div
                        key={it.id}
                        className="rise"
                        style={{ animationDelay: `${Math.min(i, 12) * 35}ms` }}
                      >
                        <ItemCard it={it} rank={i} />
                      </div>
                    ))}
                  </div>
                )}
              </section>
            </div>

            {/* 右栏：雷达 */}
            <aside className="lg:order-2 lg:sticky lg:top-20 lg:self-start">
              <div className="mb-3 font-mono text-[9px] tracking-[0.25em] text-ink-faint uppercase">
                Signal Scope · {items.length} 个目标
              </div>
              <RadarScope items={items} />

              <div className="mt-6 border-t border-rule pt-4">
                <div className="mb-2 font-mono text-[9px] tracking-[0.25em] text-ink-faint uppercase">
                  信源等级
                </div>
                {[
                  ['S', '官方一手', 'text-signal'],
                  ['A', '公认专家', 'text-scope'],
                  ['B', '垂直媒体', 'text-ink-dim'],
                  ['D', '待观察 · 强制送审', 'text-ink-faint'],
                ].map(([t, d, c]) => (
                  <div key={t} className="flex gap-3 py-0.5 font-mono text-[11px]">
                    <span className={`w-3 ${c}`}>{t}</span>
                    <span className="text-ink-faint">{d}</span>
                  </div>
                ))}
                <button
                  onClick={() => setView('mechanism')}
                  className="mt-3 font-mono text-[11px] text-scope transition-colors hover:text-signal"
                >
                  完整机制说明 →
                </button>
              </div>
            </aside>
          </div>
        </main>
      )}

      <footer className="border-t border-rule py-8">
        <div className="mx-auto max-w-6xl px-6 font-mono text-[10px] leading-relaxed tracking-wide text-ink-faint lg:px-10">
          AIRadar · 抓取 → 分层筛选 → 摘要 → 置信度路由 → 知识沉淀
          <br />
          数据由 pipeline 自动生成，人只通过审批渠道介入。
          {stats.data && (
            <> 最近更新 {new Date(stats.data.generated_at).toLocaleString('zh-CN')}</>
          )}
        </div>
      </footer>
    </div>
  )
}
