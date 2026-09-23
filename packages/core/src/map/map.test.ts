import { describe, it, expect } from 'vitest'
import { fitMapBounds, projectLayerImage, interpolateWind, mapRegionKey, mapRegionPhase, sameMapRegion } from './index'
const region = { southwest: { latitude: 30, longitude: 110 }, northeast: { latitude: 34, longitude: 114 } }

describe('省域地图视野', () => {
  const viewport = { width: 375, height: 520 }
  const padding: [number, number, number, number] = [90, 24, 92, 24]
  const screen = (point: { latitude: number; longitude: number }, camera: ReturnType<typeof fitMapBounds>) => {
    const y = (lat: number) => (1 - Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)) / Math.PI) / 2
    const pixels = 256 * 2 ** camera.scale
    return {
      x: viewport.width / 2 + (point.longitude - camera.longitude) / 360 * pixels,
      y: viewport.height / 2 + (y(point.latitude) - y(camera.latitude)) * pixels,
    }
  }
  it.each([
    ['内蒙古', { sw: { latitude: 37, longitude: 97 }, ne: { latitude: 53, longitude: 126 } }],
    ['云南', { sw: { latitude: 21.5, longitude: 97.5 }, ne: { latitude: 29.3, longitude: 106.2 } }],
    ['相邻两省', { sw: { latitude: 29, longitude: 114 }, ne: { latitude: 36, longitude: 123 } }],
  ])('%s 的四边均留在可见区域内', (_name, bounds) => {
    const camera = fitMapBounds(bounds, viewport, padding)
    const sw = screen(bounds.sw, camera)
    const ne = screen(bounds.ne, camera)
    expect(sw.x).toBeGreaterThanOrEqual(padding[3])
    expect(ne.x).toBeLessThanOrEqual(viewport.width - padding[1])
    expect(ne.y).toBeGreaterThanOrEqual(padding[0])
    expect(sw.y).toBeLessThanOrEqual(viewport.height - padding[2])
  })
  it('无效省界和画布不能产生无穷视野', () => {
    expect(() => fitMapBounds({ sw: region.southwest, ne: region.southwest }, viewport, padding)).toThrow()
    expect(() => fitMapBounds({ sw: region.southwest, ne: region.northeast }, { width: 10, height: 10 }, padding)).toThrow()
  })
})

describe('图层预览投影', () => {
  it('同一范围准确覆盖视野', () => expect(projectLayerImage(region, { sw: region.southwest, ne: region.northeast })).toEqual({ left: '0%', top: '0%', width: '100%', height: '100%' }))
  it('东侧半幅保持准确宽度', () => expect(projectLayerImage(region, { sw: { latitude: 30, longitude: 112 }, ne: region.northeast }).left).toBe('50%'))
  it('无效视野不能产生无穷坐标', () => expect(() => projectLayerImage({ southwest: region.southwest, northeast: region.southwest }, { sw: region.southwest, ne: region.northeast })).toThrow())
})


describe('密集风场插值', () => {
  it('保持最近四点的加权结果', () => {
    const points = Array.from({ length: 1000 }, (_, i) => ({ x: i % 40, y: Math.floor(i / 40), u: i % 7, v: -(i % 9) }))
    const x = 12.3, y = 8.4
    const selected = points.map(p => ({ ...p, d: (p.x-x)**2+(p.y-y)**2 })).sort((a,b) => a.d-b.d).slice(0,4)
    const weight = selected.reduce((a,p) => a+1/Math.max(p.d,1),0)
    const actual = interpolateWind(points,x,y)
    expect(actual.u).toBeCloseTo(selected.reduce((a,p) => a+p.u/Math.max(p.d,1),0)/weight)
    expect(actual.v).toBeCloseTo(selected.reduce((a,p) => a+p.v/Math.max(p.d,1),0)/weight)
  })
  it('准确命中与空场不产生非有限数', () => {
    expect(interpolateWind([{ x: 1, y: 2, u: -3, v: 4 }],1,2)).toEqual({ u: -3, v: 4 })
    expect(interpolateWind([],0,0)).toEqual({ u: 0, v: 0 })
  })
})

describe('地图视野事件', () => {
  it('兼容模拟器顶层阶段和真机 detail 阶段', () => {
    expect(mapRegionPhase({ type: 'end', detail: {} })).toBe('end')
    expect(mapRegionPhase({ type: 'regionchange', detail: { type: 'end' } })).toBe('end')
    expect(mapRegionPhase({ type: 'begin' })).toBe('begin')
  })
  it('缩放改变视野键，浮点抖动不会触发反复装载', () => {
    expect(mapRegionKey(region)).not.toBe(mapRegionKey({ ...region, northeast: { latitude: 35, longitude: 115 } }))
    expect(mapRegionKey(region)).toBe(mapRegionKey({ ...region, northeast: { latitude: 34 + 1e-9, longitude: 114 } }))
  })
})

describe('真机视野抖动过滤', () => {
  it('贴图引起的微小边界抖动不刷新，实际拖动和缩放仍刷新', () => {
    expect(sameMapRegion(undefined, region)).toBe(false)
    expect(sameMapRegion(region, { southwest: { latitude: 30.0001, longitude: 110.0001 }, northeast: { latitude: 34.0001, longitude: 114.0001 } })).toBe(true)
    expect(sameMapRegion(region, { southwest: { latitude: 30.1, longitude: 110 }, northeast: { latitude: 34.1, longitude: 114 } })).toBe(false)
    expect(sameMapRegion(region, { southwest: { latitude: 31, longitude: 111 }, northeast: { latitude: 33, longitude: 113 } })).toBe(false)
  })
})
