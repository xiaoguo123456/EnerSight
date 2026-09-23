// 执行真实视野 hook，模拟重新渲染和原生地图异步回调。
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const root = path.resolve(__dirname, '..')
function compile(file, dependencies) {
  const exports = {}
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText
  vm.runInNewContext(code, { exports, require: name => dependencies[name], setTimeout, clearTimeout })
  return exports
}
const map = compile(path.resolve(root, '../core/src/map/index.ts'), {})
const stationA = { id: 'a', latitude: 39, longitude: 115 }
const stationB = { id: 'b', latitude: 30, longitude: 120 }
const away = { latitude: 40, longitude: 116, scale: 11 }
function setup() {
  const slots = [], pending = []
  let cursor = 0, dirty = false, station = stationA, selected = 'a', native = away
  const ctx = {
    getCenterLocation: ({ success }) => success(native),
    getScale: ({ success }) => success({ scale: native.scale }),
  }
  const memo = (value, deps) => {
    const index = cursor++
    const previous = slots[index]
    if (!previous || deps.some((d, i) => !Object.is(d, previous.deps[i]))) {
      slots[index] = { value, deps }
      return { value, changed: true }
    }
    return { value: previous.value, changed: false }
  }
  const react = {
    useState: initial => {
      const index = cursor++
      if (!(index in slots)) slots[index] = initial
      return [slots[index], value => {
        const next = typeof value === 'function' ? value(slots[index]) : value
        if (!Object.is(next, slots[index])) { slots[index] = next; dirty = true }
      }]
    },
    useRef: initial => {
      const index = cursor++
      if (!(index in slots)) slots[index] = { current: initial }
      return slots[index]
    },
    useCallback: (fn, deps) => memo(fn, deps).value,
    useEffect: (fn, deps) => { if (memo(fn, deps).changed) pending.push(fn) },
  }
  const useViewport = compile(path.join(root, 'src/hooks/useMapViewport.ts'), {
    '@tarojs/taro': { default: {
      createMapContext: () => ctx,
      getWindowInfo: () => ({ windowWidth: 375, windowHeight: 640 }),
      createSelectorQuery: () => ({ select: () => ({ boundingClientRect: callback => ({ exec: () => callback({ width: 375, height: 520 }) }) }) }),
    } },
    react,
    '@enersight/core/map': map,
  }).useMapViewport
  function render() {
    let hook
    do {
      dirty = false; cursor = 0
      hook = useViewport('test', station, selected)
      pending.splice(0).forEach(fn => fn())
    } while (dirty)
    return hook
  }
  return {
    render, ctx,
    station: (next, id = next?.id) => { station = next; selected = id; return render() },
    native: next => { native = next },
  }
}
const camera = hook => JSON.parse(JSON.stringify(hook.camera))
const end = (centerLocation = away) => ({ type: 'regionchange', detail: { type: 'end', causedBy: 'drag', centerLocation, scale: centerLocation.scale } })

test('首次进入居中，拖动缩放后刷新同站数据、短暂加载都保留视野', async () => {
  const h = setup()
  assert.deepEqual(camera(h.render()), { latitude: 39, longitude: 115, scale: 9 })
  await h.render().regionChanged(end())
  assert.deepEqual(camera(h.render()), away)
  assert.deepEqual(camera(h.station({ ...stationA })), away)
  assert.deepEqual(camera(h.station(undefined, 'a')), away)
  assert.deepEqual(camera(h.station(stationA)), away)
  assert.deepEqual(camera(h.station(stationB)), { latitude: 30, longitude: 120, scale: 11 })
})

test('切站期间旧响应不触发定位，明确点击定位仍能回到场站', async () => {
  const h = setup()
  await h.render().regionChanged(end())
  assert.deepEqual(camera(h.station(stationA, 'b')), away)
  h.station(stationB).moveTo(stationA)
  assert.deepEqual(camera(h.render()), { latitude: 39, longitude: 115, scale: 11 })
})

test('模拟器缺少事件坐标时读取实际视野，按钮缩放保持当前中心', async () => {
  const h = setup()
  await h.render().regionChanged({ type: 'end', causedBy: 'drag' })
  assert.deepEqual(camera(h.render()), away)
  h.native({ latitude: 42, longitude: 118, scale: 12 })
  await h.render().zoom(1)
  assert.deepEqual(camera(h.render()), { latitude: 42, longitude: 118, scale: 13 })
})

test('旧手势异步回调不能覆盖搜索定位或新一轮手势', async () => {
  const h = setup()
  let complete
  h.ctx.getCenterLocation = ({ success }) => { complete = success }
  const old = h.render().regionChanged({ type: 'end', causedBy: 'drag' })
  h.render().moveTo(stationB, 8)
  complete(away); await old
  assert.deepEqual(camera(h.render()), { latitude: 30, longitude: 120, scale: 8 })
  const previous = h.render().regionChanged({ type: 'end', causedBy: 'drag' })
  await h.render().regionChanged({ type: 'begin', causedBy: 'drag' })
  complete(away); await previous
  assert.deepEqual(camera(h.render()), { latitude: 30, longitude: 120, scale: 8 })
  await h.render().regionChanged(end())
  assert.deepEqual(camera(h.render()), away)
})

test('程序更新不产生定位循环，读取失败不改变视野', async () => {
  const h = setup()
  await h.render().regionChanged(end())
  await h.render().regionChanged({ detail: { type: 'end', causedBy: 'update', centerLocation: stationA, scale: 9 } })
  h.ctx.getCenterLocation = ({ fail }) => fail(new Error('地图暂未就绪'))
  await h.render().zoom(1)
  await h.render().regionChanged({ type: 'end', causedBy: 'drag' })
  assert.deepEqual(camera(h.render()), away)
})

test('省域适配直接更新受控地图视野，刷新同站数据不会跳回场站', async () => {
  const h = setup()
  const bounds = { sw: { latitude: 35, longitude: 97 }, ne: { latitude: 53, longitude: 126 } }
  await h.render().fitBounds(bounds, [90, 24, 92, 24])
  const fitted = JSON.parse(JSON.stringify(map.fitMapBounds(bounds, { width: 375, height: 520 }, [90, 24, 92, 24])))
  assert.deepEqual(camera(h.render()), fitted)
  assert.deepEqual(camera(h.station({ ...stationA })), fitted)
})
