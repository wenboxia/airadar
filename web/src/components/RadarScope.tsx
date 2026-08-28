import { useMemo, useState } from 'react'
import type { Item } from '../types'

/** 雷达示波器：不是装饰，是真实的数据可视化。
 *  半径 = 综合分（越高越靠中心）· 角度 = 分类扇区 · 光点大小 = 分数 · 颜色 = 时间维度
 *  这是产品隐喻的具象化：中心是最可信的信号，外围是杂波。 */
export function RadarScope({
  items,
  onPick,
}: {
  items: Item[]
  onPick?: (it: Item | null) => void
}) {
  const [hover, setHover] = useState<Item | null>(null)
  const size = 340
  const c = size / 2
  const maxR = c - 26

  const categories = useMemo(
    () => Array.from(new Set(items.map((i) => i.category))).filter(Boolean).sort(),
    [items],
  )

  const blips = useMemo(() => {
    // 同一分类内的条目沿扇区内均匀铺开，避免完全重叠
    const perCat = new Map<string, number>()
    return items.map((it) => {
      const ci = Math.max(0, categories.indexOf(it.category))
      const n = perCat.get(it.category) ?? 0
      perCat.set(it.category, n + 1)
      const sector = (2 * Math.PI) / Math.max(categories.length, 1)
      const angle = ci * sector + sector * (0.25 + ((n * 0.37) % 0.5)) - Math.PI / 2
      // 分数 60→外圈，95→内圈
      const t = Math.min(1, Math.max(0, (it.score - 58) / 38))
      const r = maxR * (1 - t * 0.78)
      return {
        it,
        x: c + Math.cos(angle) * r,
        y: c + Math.sin(angle) * r,
        r: 2.6 + (it.score - 58) / 14,
      }
    })
  }, [items, categories, c, maxR])

  const pick = (it: Item | null) => {
    setHover(it)
    onPick?.(it)
  }

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${size} ${size}`} className="w-full max-w-[340px]">
        <defs>
          <radialGradient id="scopeGlow">
            <stop offset="0%" stopColor="#5fb3c4" stopOpacity="0.16" />
            <stop offset="70%" stopColor="#5fb3c4" stopOpacity="0.03" />
            <stop offset="100%" stopColor="#5fb3c4" stopOpacity="0" />
          </radialGradient>
          <linearGradient id="sweepGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#5fb3c4" stopOpacity="0" />
            <stop offset="100%" stopColor="#5fb3c4" stopOpacity="0.30" />
          </linearGradient>
        </defs>

        <circle cx={c} cy={c} r={maxR + 12} fill="url(#scopeGlow)" />

        {/* 距离环 = 信号强度刻度 */}
        {[1, 0.75, 0.5, 0.25].map((k) => (
          <circle
            key={k}
            cx={c}
            cy={c}
            r={maxR * k}
            fill="none"
            stroke="#233034"
            strokeWidth="1"
            strokeDasharray={k === 1 ? '0' : '2 5'}
          />
        ))}

        {/* 分类扇区分割线 */}
        {categories.map((cat, i) => {
          const a = (i * 2 * Math.PI) / categories.length - Math.PI / 2
          return (
            <line
              key={cat}
              x1={c}
              y1={c}
              x2={c + Math.cos(a) * maxR}
              y2={c + Math.sin(a) * maxR}
              stroke="#1c2529"
              strokeWidth="1"
            />
          )
        })}

        {/* 扫描线 */}
        <g
          style={{
            transformOrigin: `${c}px ${c}px`,
            animation: 'sweep 7s linear infinite',
          }}
        >
          <path
            d={`M ${c} ${c} L ${c + maxR} ${c} A ${maxR} ${maxR} 0 0 0 ${
              c + maxR * Math.cos(-0.62)
            } ${c + maxR * Math.sin(-0.62)} Z`}
            fill="url(#sweepGrad)"
          />
          <line
            x1={c}
            y1={c}
            x2={c + maxR}
            y2={c}
            stroke="#5fb3c4"
            strokeWidth="1.2"
            opacity="0.55"
          />
        </g>

        {/* 光点 */}
        {blips.map((b, i) => {
          const active = hover?.id === b.it.id
          const color = b.it.horizon === 'long' ? '#7f9c6b' : '#f0b429'
          return (
            <g
              key={b.it.id}
              onMouseEnter={() => pick(b.it)}
              onMouseLeave={() => pick(null)}
              className="cursor-pointer"
              style={{ animation: `blip-in 0.5s ${0.3 + i * 0.035}s both` }}
            >
              {active && (
                <circle cx={b.x} cy={b.y} r={b.r * 3.2} fill={color} opacity="0.14" />
              )}
              <circle
                cx={b.x}
                cy={b.y}
                r={active ? b.r * 1.7 : b.r}
                fill={color}
                opacity={active ? 1 : 0.82}
                style={{ transition: 'r 0.18s ease' }}
              />
              <circle cx={b.x} cy={b.y} r={b.r * 2.4} fill="transparent" />
            </g>
          )
        })}

        <circle cx={c} cy={c} r="2" fill="#5fb3c4" opacity="0.7" />
      </svg>

      {/* 悬停读数 —— 仪器读数条 */}
      <div className="mt-3 min-h-[52px] border-l-2 border-rule pl-3">
        {hover ? (
          <div>
            <div className="font-mono text-[10px] tracking-widest text-scope uppercase">
              {hover.category} · {hover.tier} 级 · {hover.score.toFixed(1)}
            </div>
            <div className="mt-1 line-clamp-2 text-[13px] leading-snug text-ink-dim">
              {hover.title}
            </div>
          </div>
        ) : (
          <div className="font-mono text-[10px] leading-relaxed tracking-wider text-ink-faint">
            半径 = 信号强度（越靠中心越可信）<br />
            角度 = 内容分类 · 颜色 = 时效
            <span className="ml-2 text-signal">■</span> 短期
            <span className="ml-2 text-moss">■</span> 长期
          </div>
        )}
      </div>
    </div>
  )
}
