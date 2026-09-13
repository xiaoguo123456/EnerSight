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

test('97 点趋势使用实际时间，15 分钟提示与横轴不会当作小时', () => {
  const points = Array.from({ length: 97 }, (_, i) => ({
    time: `2026-09-${i === 96 ? '14' : '13'}T${String(Math.floor(i / 4) % 24).padStart(2, '0')}:${String(i % 4 * 15).padStart(2, '0')}:00+08:00`,
    value: i === 27 ? 99 : 20,
  }))
  const data = exported.fromTrendSeries({ points, unit: '℃', y_max: null, resolution_minutes: 15 })
  const labels = []
  const ctx = new Proxy({
    fillText: label => labels.push(label),
    measureText: label => ({ width: label.length * 6 }),
    createLinearGradient: () => ({ addColorStop() {} }),
  }, { get: (target, key) => target[key] ?? (() => {}) })
  exported.draw(ctx, { w: 320, h: 180 }, data, 27, {})
  assert.equal(data.stepMinutes, 15)
  assert.ok(labels.includes('06:45'))
  assert.ok(labels.includes('04:00') && labels.includes('20:00'))
  assert.ok(labels.filter(v => /^\d\d:\d\d$/.test(v)).length <= 8)
})

test('历史小时曲线保留步长，缺测不会被转换成零', () => {
  const data = exported.fromTrendSeries({ points: [{ value: 10 }, { value: null }], unit: 'MW', y_max: null })
  assert.equal(data.stepMinutes, 60)
  assert.equal(data.values[1], null)
})
