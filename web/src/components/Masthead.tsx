import type { Stats } from '../types'

/** 刊头：编辑部气质的衬线刊名 + 仪器读数带。
 *  "编辑部 × 仪器"的张力就是这个产品的定位——它做的是资深编辑的活，用的是机器的方式。 */
export function Masthead({ stats, date }: { stats: Stats | null; date?: string }) {
  const totals = stats?.totals ?? {}
  const runs = stats?.runs?.length ?? 0
  const all = Object.values(totals).reduce((a, b) => a + b, 0)
  const kept = totals.published ?? 0
  const rate = all ? Math.round((kept / all) * 100) : 0

  return (
    <header className="relative overflow-hidden border-b border-rule">
      {/* 扫描光带 */}
      <div
        className="pointer-events-none absolute inset-y-0 w-1/3 bg-gradient-to-r from-transparent via-scope/[0.05] to-transparent"
        style={{ animation: 'scanline 9s ease-in-out infinite' }}
      />

      <div className="relative mx-auto max-w-6xl px-6 py-8 lg:px-10">
        {/* 回仓库的入口：面试官从网站进来，要一步就能看到源码、评测和设计决策 */}
        <a
          href="https://github.com/wenboxia/airadar"
          target="_blank"
          rel="noreferrer"
          aria-label="在 GitHub 上查看源码、评测与设计决策"
          className="absolute right-6 top-3 flex items-center gap-1.5 font-mono text-[11px] tracking-wide text-ink-faint transition-colors hover:text-signal lg:right-10"
        >
          <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true">
            <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
          </svg>
          GitHub
        </a>

        <div className="flex flex-wrap items-end justify-between gap-6">
          <div>
            <div className="mb-1 flex items-baseline gap-3">
              <h1
                className="text-[42px] leading-none tracking-tight text-ink lg:text-[52px]"
                style={{ fontFamily: 'var(--font-display)' }}
              >
                AIRadar
              </h1>
              <span className="font-mono text-[10px] tracking-[0.25em] text-scope">
                v0.1
              </span>
            </div>
            <p className="font-mono text-[11px] tracking-wide text-ink-dim">
              每日定时运行的 AI 行业情报工作流
            </p>
          </div>

          {/* 仪器读数 */}
          <dl className="flex gap-7">
            {[
              { k: '已收录', v: kept, unit: '条' },
              { k: '通过率', v: rate, unit: '%' },
              { k: '运行', v: runs, unit: '次' },
            ].map((m) => (
              <div key={m.k}>
                <dt className="font-mono text-[9px] tracking-[0.2em] text-ink-faint uppercase">
                  {m.k}
                </dt>
                <dd className="mt-0.5 font-mono text-2xl leading-none text-signal">
                  {m.v}
                  <span className="ml-0.5 text-[11px] text-ink-faint">{m.unit}</span>
                </dd>
              </div>
            ))}
          </dl>
        </div>

        {date && (
          <div className="mt-5 font-mono text-[10px] tracking-[0.2em] text-ink-faint uppercase">
            最近扫描 · {date}
          </div>
        )}
      </div>
    </header>
  )
}
