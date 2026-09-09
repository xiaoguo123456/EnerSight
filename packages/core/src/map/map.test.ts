import { describe, it, expect } from 'vitest'
import { projectLayerImage } from './index'
const region = { southwest: { latitude: 30, longitude: 110 }, northeast: { latitude: 34, longitude: 114 } }
describe('图层预览投影', () => {
  it('同一范围准确覆盖视野', () => expect(projectLayerImage(region, { sw: region.southwest, ne: region.northeast })).toEqual({ left: '0%', top: '0%', width: '100%', height: '100%' }))
  it('东侧半幅保持准确宽度', () => expect(projectLayerImage(region, { sw: { latitude: 30, longitude: 112 }, ne: region.northeast }).left).toBe('50%'))
  it('无效视野不能产生无穷坐标', () => expect(() => projectLayerImage({ southwest: region.southwest, northeast: region.southwest }, { sw: region.southwest, ne: region.northeast })).toThrow())
})
