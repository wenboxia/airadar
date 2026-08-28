import { useState } from 'react'
import { relTime } from '../data'
import type { Item, Tier } from '../types'

const TIER_STYLE: Record<Tier, string> = {
  S: 'text-signal border-signal/45 bg-signal/8',
  A: 'text-scope border-scope/45 bg-scope/8',
  B: 'text-ink-dim border-rule-bright bg-white/3',
  C: 'text-ink-faint border-rule bg-white/2',
  D: 'text-ink-faint border-rule bg-white/2',
  X: 'text-alert border-alert/40 bg-alert/8',
}

/** 内容完整度：抓到全文 vs 只有信源简介。诚实标注比假装完整重要（decisions.md D14） */
function ContentBadge({ it }: { it: Item }) {
  if (it.extra?.summary_mode !== 'brief') return null
  return (
    <span
      title="原文抓取失败，仅依据信源简介生成——刻意不扩写，避免模型编造"
      className="font-mono text-[10px] tracking-wider text-alert/85"
    >
      简介级
    </span>
  )
}

export function ItemCard({ it, rank }: { it: Item; rank?: number }) {
  const [open, setOpen] = useState(false)
  const hasLong = Boolean(it.summary_long)

  return (
    <article className="group relative border-b border-rule py-5 transition-colors hover:bg-panel/40">
      {rank !== undefined && (
        <span className="absolute -left-9 top-5 hidden font-mono text-[11px] text-ink-faint lg:block">
          {String(rank + 1).padStart(2, '0')}
        </span>
      )}

      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1">
        <span
          className={`border px-1.5 py-px font-mono text-[10px] font-medium tracking-widest ${TIER_STYLE[it.tier]}`}
        >
          {it.tier}
        </span>
        <span className="font-mono text-[11px] tracking-wide text-ink-dim">
          {it.source}
        </span>
        <span className="font-mono text-[11px] text-ink-faint">
          {relTime(it.published_at)}
        </span>
        <span className="ml-auto flex items-center gap-3">
          <ContentBadge it={it} />
          <span
            className="font-mono text-[11px] text-ink-faint"
            title="综合分 = 信源等级 55% + 模型价值评分 45%"
          >
            {it.score.toFixed(1)}
          </span>
        </span>
      </div>

      <h3 className="mb-2 text-[16.5px] leading-[1.45]">
        <a
          href={it.url}
          target="_blank"
          rel="noreferrer"
          className="text-ink decoration-signal/60 underline-offset-4 transition-colors hover:text-signal hover:underline"
        >
          {it.title}
        </a>
      </h3>

      {it.summary_short && (
        <p className="mb-2.5 text-[14px] leading-[1.7] text-ink-dim">
          {it.summary_short}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[10px] tracking-wider text-scope/80">
          {it.category}
        </span>
        <span className="text-ink-faint">·</span>
        <span
          className={`font-mono text-[10px] tracking-wider ${
            it.horizon === 'long' ? 'text-moss' : 'text-signal/70'
          }`}
        >
          {it.horizon === 'long' ? '长期价值' : '时效'}
        </span>
        {it.topics?.slice(0, 3).map((t) => (
          <span key={t} className="font-mono text-[10px] text-ink-faint">
            #{t}
          </span>
        ))}
        {hasLong && (
          <button
            onClick={() => setOpen((v) => !v)}
            className="ml-auto font-mono text-[10px] tracking-wider text-ink-faint transition-colors hover:text-signal"
          >
            {open ? '收起 —' : '展开 +'}
          </button>
        )}
      </div>

      {open && hasLong && (
        <div className="mt-3 border-l-2 border-signal/30 pl-4">
          <p className="text-[13.5px] leading-[1.9] text-ink-dim">{it.summary_long}</p>
          {it.key_points?.length > 0 && (
            <ul className="mt-3 space-y-1">
              {it.key_points.map((k, i) => (
                <li key={i} className="flex gap-2 text-[12.5px] text-ink-faint">
                  <span className="font-mono text-scope/60">›</span>
                  {k}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </article>
  )
}
