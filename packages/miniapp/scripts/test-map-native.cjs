// 执行真实 hook 的异步流程，模拟真机覆盖层回调；不依赖开发者工具的预览分支。
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const root = path.resolve(__dirname, '..')
function compile(file, dependencies, globals = {}) {
  const exports = {}
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
  vm.runInNewContext(code, { exports, require: name => dependencies[name], ...globals })
  return exports
}
const map = compile(path.resolve(root, '../core/src/map/index.ts'), {})
function setup() {
  let region = { southwest: { latitude: 30, longitude: 110 }, northeast: { latitude: 34, longitude: 114 } }
  const additions = [], removals = [], timers = new Map()
  let calls = 0, timerId = 0
  const ctx = {
    getRegion: ({ success }) => success(region),
    addGroundOverlay: options => { additions.push(options) },
    removeGroundOverlay: ({ id }) => { removals.push(id) },
  }
  const response = { frames: [{ images: [0, 1].map(i => ({ url: `tile${i}`, bounds: { sw: region.southwest, ne: region.northeast } })) }], legend: {}, observed_at: '2026-09-10T04:00:00Z' }
  const hook = compile(path.join(root, 'src/hooks/useMapLayer.ts'), {
    '@tarojs/taro': { default: { createMapContext: () => ctx, getDeviceInfo: () => ({ platform: 'ios' }) } },
    react: { useState: init => [typeof init === 'function' ? init() : init, () => {}], useRef: value => ({ current: value }), useCallback: f => f, useEffect: () => {} },
    '@/store/weatherModel': { useWeatherModel: select => select({ model: 'best_match' }) },
    '@/api/debug': { clientLog: () => {} },
    '@/api/layers': { layersApi: { get: async () => { calls++; return response } } },
    '@enersight/core/map': map,
  }, {
    setTimeout: (fn, delay) => { timers.set(++timerId, { fn, delay }); return timerId },
    clearTimeout: id => timers.delete(id),
  }).useMapLayer('test', 'temperature', true)
  const flush = () => new Promise(resolve => setImmediate(resolve))
  const load = async () => {
    for (const [id, t] of [...timers]) if (t.delay === 600) { timers.delete(id); t.fn() }
    await flush()
  }
  return { hook, additions, removals, load, flush, calls: () => calls, move: () => { region = { ...region, northeast: { latitude: 35, longitude: 115 } } } }
}
test('真机静止不重载，新批全部完成才移除旧图', async () => {
  const h = setup()
  h.hook.refresh(); await h.load()
  h.additions.forEach(a => a.success()); await h.flush()
  const old = h.additions.map(a => a.id)
  await h.hook.viewportChanged(); await h.load()
  assert.equal(h.calls(), 1)
  h.move(); await h.hook.viewportChanged(); await h.load()
  assert.deepEqual(h.removals, [])
  h.additions[2].success(); await h.flush()
  assert.deepEqual(h.removals, [])
  h.additions[3].success(); await h.flush()
  assert.deepEqual(h.removals, old)
})
test('真机新批失败保留旧图，离开时清理全部', async () => {
  const h = setup()
  h.hook.refresh(); await h.load()
  h.additions.forEach(a => a.success()); await h.flush()
  const old = h.additions.map(a => a.id)
  h.move(); await h.hook.viewportChanged(); await h.load()
  h.additions[2].fail(new Error('模拟图片失败')); await h.flush()
  assert.ok(old.every(id => !h.removals.includes(id)))
  h.hook.invalidate()
  assert.ok(old.every(id => h.removals.includes(id)))
})
