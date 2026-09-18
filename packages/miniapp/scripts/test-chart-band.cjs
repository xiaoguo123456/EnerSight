// 三模式区间带的绘制。docs/19 §一
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const exported = {}
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/components/TrendChart/draw.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText, { exports: exported, require: () => ({ thousands: value => String(value) }) })

/** 记录每条填充路径的点，用来判断带的形状；其余调用一律吞掉。 */
function tracingCtx() {
  const paths = []
  let cur = null
  const target = {
    beginPath: () => { cur = [] },
    moveTo: (x, y) => { if (cur) cur.push([x, y]) },
    lineTo: (x, y) => { if (cur) cur.push([x, y]) },
    fill: () => { if (cur && cur.length) paths.push({ points: cur, style: target.fillStyle }) },
    measureText: label => ({ width: label.length * 6 }),
    createLinearGradient: () => ({ addColorStop() {} }),
    fillStyle: '',
  }
  const ctx = new Proxy(target, {
    get: (t, key) => (key in t ? t[key] : () => {}),
    set: (t, key, value) => { t[key] = value; return true },
  })
  return { ctx, paths }
}

test('区间带按上沿去、下沿回，闭合成一条带', () => {
  const { ctx, paths } = tracingCtx()
  exported.draw(ctx, { w: 300, h: 180 }, {
    values: [5, 10, 6],
    band: { low: [3, 8, 4], high: [7, 12, 9] },
    unit: 'MW', yMax: null,
  }, 1, { band: 'BAND' })
  const band = paths.find(p => p.style === 'BAND')
  assert.ok(band, '带应该被填充一次')
  assert.equal(band.points.length, 6) // 3 点去 + 3 点回
  const ys = band.points.map(p => p[1])
  // 画布 y 向下：上沿的 y 一定小于同一横坐标处下沿的 y
  assert.ok(ys[0] < ys[5] && ys[1] < ys[4] && ys[2] < ys[3])
})

test('带在缺口处断开，不跨缺测连成一片', () => {
  const { ctx, paths } = tracingCtx()
  exported.draw(ctx, { w: 300, h: 180 }, {
    values: [5, null, 6, 7],
    band: { low: [3, null, 4, 5], high: [7, null, 9, 10] },
    unit: 'MW', yMax: null,
  }, 0, { band: 'BAND' })
  const bands = paths.filter(p => p.style === 'BAND')
  // 只有下标 2、3 连得起来；下标 0 是孤点，不成面
  assert.equal(bands.length, 1)
  assert.equal(bands[0].points.length, 4)
})

test('纵轴把带的上沿算进去，带不会画到画布外', () => {
  const labels = []
  const ctx = new Proxy({
    fillText: label => labels.push(label),
    measureText: label => ({ width: label.length * 6 }),
    createLinearGradient: () => ({ addColorStop() {} }),
  }, { get: (target, key) => target[key] ?? (() => {}) })
  exported.draw(ctx, { w: 300, h: 180 }, {
    values: [10, 20],
    band: { low: [8, 15], high: [40, 90] },
    unit: 'MW', yMax: null,
  }, 0, {})
  // 只按主曲线取整，上限会停在 20，带的 90 就画到画布外了
  const ticks = labels.map(Number).filter(Number.isFinite)
  assert.ok(Math.max(...ticks) >= 90, `纵轴刻度应覆盖带的上沿，实际 ${labels.join(',')}`)
})

test('没有带时绘制行为不变：画面积，不画带', () => {
  const { ctx, paths } = tracingCtx()
  exported.draw(ctx, { w: 300, h: 180 }, { values: [5, 10, 6], unit: 'MW', yMax: null }, 1, { band: 'BAND' })
  assert.equal(paths.filter(p => p.style === 'BAND').length, 0)
  // 面积用的是渐变对象；气泡框等其他填充是字符串色值
  assert.ok(paths.some(p => typeof p.style === 'object'), '主曲线面积应照常填充')
})

test('有带时不再画主曲线面积，带是唯一的填充', () => {
  // 两层浅蓝叠在一起时带的下沿看不出来，区间就失去意义
  const { ctx, paths } = tracingCtx()
  exported.draw(ctx, { w: 300, h: 180 }, {
    values: [5, 10, 6],
    band: { low: [3, 8, 4], high: [7, 12, 9] },
    unit: 'MW', yMax: null,
  }, 1, { band: 'BAND' })
  assert.equal(paths.filter(p => typeof p.style === 'object').length, 0, '不应再画渐变面积')
  assert.equal(paths.filter(p => p.style === 'BAND').length, 1)
})
