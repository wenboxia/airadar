export type Tier = 'S' | 'A' | 'B' | 'C' | 'D' | 'X'
export type Horizon = 'short' | 'long'
export type Status = 'published' | 'review' | 'discarded' | 'new'

export interface Item {
  id: string
  url: string
  title: string
  source: string
  tier: Tier
  published_at: string
  summary_short: string
  summary_long: string
  key_points: string[]
  topics: string[]
  category: string
  horizon: Horizon
  score: number
  status: Status
  extra: {
    content_source?: 'direct' | 'reader' | 'rss'
    summary_mode?: 'full' | 'brief'
    hn_points?: number
    stars?: number
    authors?: string[]
  }
}

export interface Feed {
  generated_at: string
  date?: string
  run_id?: string
  top?: Item[]
  items: Item[]
}

export interface RunStat {
  run_id: string
  started_at: string
  stats: {
    fetch?: { total: number; per_source: Record<string, number | string> }
    dedupe?: Record<string, number>
    triage?: Record<string, number>
    summarize?: Record<string, number>
    classify?: Record<string, number>
    budget?: { tokens_used: number; calls_made: number }
    timing?: Record<string, number>
    calls_by_provider?: Record<string, number>
  }
}

export interface Stats {
  generated_at: string
  totals: Record<string, number>
  /** 由 pipeline 从 sources.yaml 导出——展示层不硬编码信源，避免与实现脱节 */
  sources?: { total: number; by_tier: Record<string, string[]> }
  runs: RunStat[]
}

export const TIER_META: Record<Tier, { label: string; desc: string }> = {
  S: { label: 'S', desc: '官方一手' },
  A: { label: 'A', desc: '公认专家' },
  B: { label: 'B', desc: '垂直媒体' },
  C: { label: 'C', desc: '跨界视角' },
  D: { label: 'D', desc: '待观察' },
  X: { label: 'X', desc: '已屏蔽' },
}
