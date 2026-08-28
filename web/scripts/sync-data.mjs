// 把 pipeline 产出的 data/feed/*.json 同步到前端 public/data/。
// 前端是纯静态站（零服务器架构，见 docs/decisions.md D3），数据随构建打包。
import { cp, mkdir, readdir } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const src = join(here, '..', '..', 'data', 'feed')
const dest = join(here, '..', 'public', 'data')

if (!existsSync(src)) {
  console.warn(`[sync-data] 找不到 ${src}，跳过（先跑一次 pipeline）`)
  process.exit(0)
}
await mkdir(dest, { recursive: true })
await cp(src, dest, { recursive: true })
console.log(`[sync-data] 已同步 ${(await readdir(dest)).join(', ')}`)
