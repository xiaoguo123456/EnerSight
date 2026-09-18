/** 同一组量化网格共享请求；冷却期不反复轰击上游。 */
import type { LayerResponse, LayerType } from '@enersight/core/types'
import { servedModel, useWeatherModel } from '@/store/weatherModel'
import { imageryApi as api } from './index'
export interface BBox { west: number; south: number; east: number; north: number }
const cache = new Map<string, { until: number; value: Promise<LayerResponse> }>()
let cooldown = 0
export const layersApi = {
  get: (layer: LayerType, bbox: BBox, zoom: number) => {
    // 图层不认三模式，按服务端实际会用的模型请求与缓存，免得同一份图按两个键各存一份
    const model = layer === 'cloud' ? servedModel(useWeatherModel.getState().model) : 'ecmwf_ifs'
    // 返回的数值标注与风矢量绑定视野；图片本身由后端量化缓存。
    const key = [model, layer, zoom, bbox.west, bbox.south, bbox.east, bbox.north].join(':')
    const hit = cache.get(key)
    if (hit && hit.until > Date.now()) return hit.value
    if (Date.now() < cooldown) return Promise.reject(new Error('气象服务冷却中，请约一分钟后重试'))
    const value = api.get<LayerResponse>(`/v1/map/layers/${layer}`, {
      bbox: `${bbox.west},${bbox.south},${bbox.east},${bbox.north}`, zoom, weather_model: model,
    }).catch((e) => { cache.delete(key); if (e.status === 429 || /限流|冷却/.test(e.message)) cooldown = Date.now() + 60_000; throw e })
    if (cache.size > 32) cache.delete(cache.keys().next().value!)
    cache.set(key, { until: Date.now() + 120_000, value })
    return value
  },
}
