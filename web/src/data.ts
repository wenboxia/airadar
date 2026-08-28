import { useEffect, useState } from 'react'
import type { Feed, Stats } from './types'

/** 静态 JSON 读取。失败不白屏——返回 error 由 UI 显式提示（兜底原则也适用于前端）。 */
export function useJson<T>(path: string) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    fetch(`${import.meta.env.BASE_URL}data/${path}`)
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status}`)
        return r.json()
      })
      .then((j) => alive && setData(j))
      .catch((e) => alive && setError(String(e.message ?? e)))
    return () => {
      alive = false
    }
  }, [path])

  return { data, error }
}

export const useLatest = () => useJson<Feed>('latest.json')
export const useWeek = () => useJson<Feed>('week.json')
export const usePending = () => useJson<Feed>('pending.json')
export const useStats = () => useJson<Stats>('stats.json')

export function relTime(iso: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  const h = (Date.now() - d.getTime()) / 36e5
  if (h < 1) return '刚刚'
  if (h < 24) return `${Math.floor(h)} 小时前`
  const days = Math.floor(h / 24)
  if (days < 30) return `${days} 天前`
  return d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}
