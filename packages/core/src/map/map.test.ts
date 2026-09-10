import { describe, it, expect } from 'vitest'
import { projectLayerImage, interpolateWind } from './index'
const region = { southwest: { latitude: 30, longitude: 110 }, northeast: { latitude: 34, longitude: 114 } }
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
