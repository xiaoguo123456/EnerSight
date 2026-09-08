// 地图 marker 图标：公开电站（光伏 / 风电）与用户站点。map 的 marker 只认图片文件。
// 用法：node scripts/gen-marker-icons.mjs src/assets/markers
import { chromium } from 'playwright'
import { mkdirSync, writeFileSync } from 'node:fs'

const ICON = {
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
  wind: '<path d="M12.8 19.6A2 2 0 1 0 14 16H2"/><path d="M17.5 8a2.5 2.5 0 1 1 2 4H2"/><path d="M9.8 4.4A2 2 0 1 1 11 8H2"/>',
}
// name → [图标, 底色, 尺寸]
const MARKERS = {
  'plant-solar': ['sun', '#f59e0b', 44],
  'plant-wind': ['wind', '#1677ff', 44],
}

const OUT = process.argv[2]
mkdirSync(OUT, { recursive: true })
const browser = await chromium.launch()
for (const [name, [icon, color, size]] of Object.entries(MARKERS)) {
  const page = await browser.newPage({ viewport: { width: size, height: size }, deviceScaleFactor: 2 })
  // 圆形底 + 白描边 + 白色图标，缩到 22px 显示时仍能辨认
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 44 44">
    <circle cx="22" cy="22" r="19" fill="${color}" stroke="#ffffff" stroke-width="3"/>
    <g transform="translate(10 10) scale(1)" fill="none" stroke="#ffffff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">${ICON[icon]}</g>
  </svg>`
  await page.setContent(`<body style="margin:0;background:transparent">${svg}</body>`)
  writeFileSync(`${OUT}/${name}.png`, await page.screenshot({ omitBackground: true }))
  await page.close()
}
await browser.close()
console.log('生成 marker 图标 →', OUT)
