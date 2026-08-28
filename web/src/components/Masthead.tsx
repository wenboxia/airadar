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
              每天扫描 AI 前沿、沉淀为知识库的情报 Agent
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
