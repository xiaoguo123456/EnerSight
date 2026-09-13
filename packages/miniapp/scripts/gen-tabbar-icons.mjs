import { chromium } from 'playwright'
import { mkdirSync, writeFileSync } from 'node:fs'

const P = {
  home: '<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
  map: '<path d="M14.106 5.553a2 2 0 0 0 1.788 0l3.659-1.83A1 1 0 0 1 21 4.619v12.764a1 1 0 0 1-.553.894l-4.553 2.277a2 2 0 0 1-1.788 0l-4.212-2.106a2 2 0 0 0-1.788 0l-3.659 1.83A1 1 0 0 1 3 19.381V6.618a1 1 0 0 1 .553-.894l4.553-2.277a2 2 0 0 1 1.788 0z"/><path d="M15 5.764v15M9 3.236v15"/>',
  bell: '<path d="M10.268 21a2 2 0 0 0 3.464 0"/><path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326"/>',
  chart: '<path d="M3 21V10l6 3V7l6 3V3h6v18H3Z"/><path d="M7 17h1M12 17h1M17 17h1"/>',
  // 「我的」使用人像，与个人页面身份图标一致。
  user: '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
}

const OUT = process.argv[2]
mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 81, height: 81 }, deviceScaleFactor: 1 })

for (const [name, d] of Object.entries(P)) {
  for (const [state, color] of [['', '#64748B'], ['-active', '#1677FF']]) {
    // 两态保持相同线宽与留白，仅用颜色区分选择。
    const fill = 'fill="none"'
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="81" height="81" viewBox="-2 -2 28 28" ${fill} stroke="${color}" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`
    await page.setContent(
      `<body style="margin:0;background:transparent">${svg}</body>`)
    const buf = await page.screenshot({ omitBackground: true })
    writeFileSync(`${OUT}/${name}${state}.png`, buf)
  }
}
await browser.close()
console.log('生成 10 个 tabBar 图标 →', OUT)
