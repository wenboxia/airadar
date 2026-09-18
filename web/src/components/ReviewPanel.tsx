import type { ReviewFeed, ReviewItem, Tier } from '../types'

const TIER_STYLE: Record<Tier, string> = {
  S: 'text-signal border-signal/45',
  A: 'text-scope border-scope/45',
  B: 'text-ink-dim border-rule-bright',
  C: 'text-ink-faint border-rule',
  D: 'text-ink-faint border-rule',
  X: 'text-alert border-alert/40',
}

/** 下一个北京时间周一。审批单在那天北京时间 05:00 的定时运行里开出来 */
function nextMondayCN(): string {
  const cn = new Date(Date.now() + 8 * 3600_000) // 按北京时间取星期
  const dow = cn.getUTCDay() // 0=周日
  const add = dow === 1 ? 7 : (8 - dow) % 7 || 7
  const d = new Date(cn.getTime() + add * 86400_000)
  return `${d.getUTCMonth() + 1} 月 ${d.getUTCDate()} 日`
}

const LANES: { key: 'queue' | 'audit' | 'recall'; title: string; verb: string; hint: string }[] = [
  {
    key: 'queue',
    title: '一 · 待审候选',
    verb: '勾上 = 收录',
    hint: '系统当初拿不准、交给你定的内容，按最新一版打分标准从高到低排。',
  },
  {
    key: 'audit',
    title: '二 · 自动发布抽查',
    verb: '勾上 = 撤下',
    hint: '系统自己发的、没经过你，均匀随机抽几条给你复核。这一块的勾选含义和另外两块相反。',
  },
  {
    key: 'recall',
    title: '三 · 旧池捞回',
    verb: '勾上 = 收录，留空 = 永久出队',
    hint: '按时效或新标准已经出队的条目，分最高的给一次第二机会。',
  },
]

function Row({ it }: { it: ReviewItem }) {
  return (
    <div className="border-b border-rule py-3.5">
      <div className="mb-1 flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className={`border px-1.5 font-mono text-[10px] ${TIER_STYLE[it.tier]}`}>
          {it.tier}
        </span>
        <span className="font-mono text-[11px] text-ink-faint">{it.source}</span>
        <span className="font-mono text-[11px] text-ink-faint">{it.current_score.toFixed(1)} 分</span>
        {it.reason && (
          <span className="font-mono text-[10px] text-ink-faint">
            {it.reason === 'stale' ? '时效已过出队' : it.reason === 'retired' ? '信源已停抓' : '新标准不达标出队'}
          </span>
        )}
      </div>
      <a
        href={it.url}
        target="_blank"
        rel="noreferrer"
        className="text-[15px] leading-snug text-ink hover:text-signal"
      >
        {it.title}
      </a>
      {it.summary_short && (
        <p className="mt-1 text-[13px] leading-relaxed text-ink-dim">{it.summary_short}</p>
      )}
    </div>
  )
}

/** 「待审」窗口：只读展示当前这张审批单。
 *  以前这里读的是"今天这次运行新送审的条目"，跟人周一真正要审的不是一回事，却叫同一个名字。
 *  现在网站和 GitHub issue 读的是同一份数据（review.json），两边永远一致；
 *  没有开着的单子时只说下一张什么时候开，不放预览——预览到周一前还会变，放出来只会误导。 */
export function ReviewPanel({ data, error }: { data: ReviewFeed | null; error: string | null }) {
  const open = data?.status === 'open'
  const total = open ? LANES.reduce((n, l) => n + data.lanes[l.key].length, 0) : 0

  return (
    <main className="mx-auto max-w-4xl px-6 py-8 lg:px-10">
      <div className="mb-5">
        <h2 className="text-[30px] leading-none text-ink" style={{ fontFamily: 'var(--font-display)' }}>
          {open ? `本周审批单 · #${data.issue}` : '待人工审批'}
        </h2>
        <p className="mt-2 font-mono text-[11px] tracking-wide text-ink-faint">
          这里只能看。勾选和提交在 GitHub 上完成，勾完记得点 Close issue。
        </p>
      </div>

      {open ? (
        <>
          <a
            href={data.url}
            target="_blank"
            rel="noreferrer"
            className="mb-6 inline-block border border-signal/60 px-4 py-2 font-mono text-[12px] text-signal hover:bg-signal/10"
          >
            去 GitHub 审批（{total} 条）→
          </a>

          {LANES.map((l) =>
            data.lanes[l.key].length ? (
              <section key={l.key} className="mb-8">
                <div className="mb-1 flex items-baseline gap-3">
                  <h3 className="text-[17px] text-ink">{l.title}</h3>
                  <span
                    className={`font-mono text-[11px] ${l.key === 'audit' ? 'text-alert' : 'text-signal'}`}
                  >
                    {l.verb}
                  </span>
                </div>
                <p className="mb-2 text-[12.5px] text-ink-dim">{l.hint}</p>
                <div className="border-t border-rule">
                  {data.lanes[l.key].map((it) => (
                    <Row key={it.id} it={it} />
                  ))}
                </div>
              </section>
            ) : null,
          )}

          <p className="border-l-2 border-rule pl-3 text-[12.5px] leading-relaxed text-ink-dim">
            待审队列共 {data.queue_total} 条，其中 {data.shelved} 条已出队（时效已过、新标准不达标，或信源已停抓）——
            前两类会轮流出现在「旧池捞回」里，停抓信源的不再打扰你。容量以外的条目不会假装还会被审。
          </p>
        </>
      ) : (
        <div className="border border-rule px-5 py-8 text-center">
          <p className="text-[15px] text-ink">
            {data?.status === 'collected' ? '本周的审批单已经审完' : '目前没有开着的审批单'}
          </p>
          <p className="mt-2 font-mono text-[12px] text-ink-faint">
            下一张 {nextMondayCN()}（周一）北京时间 05:00 开
          </p>
          {error && !data && (
            <p className="mt-3 font-mono text-[10px] text-ink-faint">（还没有开过三块清单的审批单）</p>
          )}
        </div>
      )}
    </main>
  )
}
