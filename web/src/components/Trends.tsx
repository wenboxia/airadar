import { useState } from 'react'
import { relTime } from '../data'
import type { Trends as TrendsData, TrendTopic } from '../types'

const PHASE_STYLE: Record<string, string> = {
  萌芽: 'text-signal border-signal/40',
  升温: 'text-signal border-signal/40',
  成熟: 'text-moss border-moss/40',
  平稳: 'text-ink-dim border-rule-bright',
  衰退: 'text-ink-faint border-rule',
  观察中: 'text-ink-faint border-rule',
}

/** 热度条：三个窗口的日均频次对比，比绝对数字更能看出趋势 */
function Sparkline({ counts }: { counts: TrendTopic['counts'] }) {
  const rates = [counts.d7 / 7, counts.d30 / 30, counts.d90 / 90]
  const max = Math.max(...rates, 0.001)
  return (
    <div className="flex h-6 items-end gap-1" title="日均频次：7 天 / 30 天 / 90 天">
      {rates.map((r, i) => (
        <div
          key={i}
          className={`w-2 ${i === 0 ? 'bg-signal' : i === 1 ? 'bg-scope/70' : 'bg-rule-bright'}`}
          style={{ height: `${Math.max(8, (r / max) * 100)}%` }}
        />
      ))}
    </div>
  )
}

export function Trends({ data }: { data: TrendsData | null }) {
  const [open, setOpen] = useState<string | null>(null)

  if (!data) {
    return (
      <p className="py-16 text-center font-mono text-[12px] text-ink-faint">加载中…</p>
    )
  }

  return (
    <div>
      {/* 诚实标注：跨度不够时不假装能判断趋势 */}
      <div
        className={`mb-6 border-l-2 pl-3 text-[12.5px] leading-relaxed ${
          data.lifecycle_ready ? 'border-moss/40 text-ink-dim' : 'border-signal/40 text-ink-dim'
        }`}
      >
        {data.lifecycle_ready ? (
          <>
            知识库已覆盖 <span className="text-ink">{data.span_days} 天</span>
            ，趋势判断基于 7 / 30 / 90 天窗口的<span className="text-ink">日均频次对比</span>
            ——比的是密度而不是绝对次数，否则长窗口永远数字更大。
          </>
        ) : (
          <>
            知识库目前只覆盖 <span className="text-ink">{data.span_days} 天</span>，
            还不足 {data.min_span_days} 天，
            <span className="text-ink">因此不给出生命周期判断</span>——
            数据太短时任何话题都像"刚萌芽"，那是错觉不是发现。
            下面的热度数字是真实的，趋势结论要等数据攒够。
          </>
        )}
      </div>

      <div className="border-t border-rule">
        {data.topics.map((t) => {
          const isOpen = open === t.topic
          return (
            <div key={t.topic} className="border-b border-rule py-4">
              <button
                onClick={() => setOpen(isOpen ? null : t.topic)}
                className="flex w-full items-center gap-4 text-left"
              >
                <Sparkline counts={t.counts} />
                <span className="min-w-0 flex-1">
                  <span className="text-[15px] text-ink">{t.topic}</span>
                  <span className="ml-3 font-mono text-[11px] text-ink-faint">
                    近 7 天 {t.counts.d7} 次 · 30 天 {t.counts.d30} · 90 天 {t.counts.d90}
                  </span>
                </span>
                <span
                  className={`shrink-0 border px-2 py-0.5 font-mono text-[10px] tracking-wider ${
                    PHASE_STYLE[t.phase] ?? 'text-ink-faint border-rule'
                  }`}
                >
                  {t.phase}
                </span>
                <span className="shrink-0 font-mono text-[10px] text-ink-faint">
                  {isOpen ? '收起 —' : '时间线 +'}
                </span>
              </button>

              <p className="mt-1.5 pl-[52px] text-[12px] text-ink-faint">{t.why}</p>

              {isOpen && (
                <ol className="mt-4 space-y-3 border-l border-rule pl-4 sm:ml-[52px]">
                  {t.timeline.map((it) => (
                    <li key={it.id} className="relative">
                      <span className="absolute -left-[21px] top-2 h-1.5 w-1.5 rounded-full bg-scope/60" />
                      <div className="flex flex-wrap items-baseline gap-x-3 font-mono text-[10px] text-ink-faint">
                        <span className="text-scope/80">{it.tier}</span>
                        <span>{it.source}</span>
                        <span>{relTime(it.published_at)}</span>
                        <span className="ml-auto">{it.score?.toFixed(1)}</span>
                      </div>
                      <a
                        href={it.url}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-0.5 block text-[13.5px] leading-snug text-ink transition-colors hover:text-signal"
                      >
                        {it.title}
                      </a>
                      {it.summary_short && (
                        <p className="mt-0.5 text-[12px] leading-relaxed text-ink-dim">
                          {it.summary_short}
                        </p>
                      )}
                    </li>
                  ))}
                </ol>
              )}
            </div>
          )
        })}
      </div>

      {data.topics.length === 0 && (
        <p className="py-16 text-center font-mono text-[12px] text-ink-faint">
          还没有出现 {data.min_mentions} 次以上的话题
        </p>
      )}
    </div>
  )
}
