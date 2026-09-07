/**
 * 页面截图自检。
 *
 * 用法：node scripts/screenshot.mjs <url> <out.png> [viewportHeight]
 * 或经 Makefile：make shot PAGE=home
 *
 * 为什么不用 fullPage：Taro 的 .taro_page 是内层滚动容器，
 * fullPage 只截 body，溢出部分截不到 —— 会误判成「页面被裁剪」。
 * 因此改用足够高的视口让内容完全展开。
 *
 * 需要 playwright：pnpm --filter @enersight/miniapp add -D playwright
 */
import { chromium } from 'playwright'

const url = process.argv[2]
const out = process.argv[3]
// Taro 的 taro_page 是内层滚动容器，fullPage 截不到溢出部分。
// 用足够高的视口让内容完全展开。
const h = Number(process.argv[4] ?? 1400)

const browser = await chromium.launch()
const ctx = await browser.newContext({
  viewport: { width: 390, height: h },
  deviceScaleFactor: 2,
  isMobile: true,
  hasTouch: true,
  userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
})
const page = await ctx.newPage()
const errors = []
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
page.on('pageerror', (e) => errors.push(String(e)))

await page.goto(url, { waitUntil: 'networkidle' })
await page.waitForTimeout(800)
await page.screenshot({ path: out })

const info = await page.evaluate(() => {
  const p = document.querySelector('.taro_page')
  return { rootFs: getComputedStyle(document.documentElement).fontSize,
           contentH: p ? p.scrollHeight : -1 }
})
console.log(`html.font-size=${info.rootFs}  内容高度=${info.contentH}px`)
if (errors.length) console.log('CONSOLE ERRORS:\n' + errors.slice(0, 5).join('\n'))
await browser.close()
