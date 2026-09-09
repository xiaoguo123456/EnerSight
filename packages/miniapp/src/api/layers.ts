/** 同一组量化网格共享请求；冷却期不反复轰击上游。 */
import type { LayerResponse, LayerType } from '@enersight/core/types'
import { useWeatherModel } from '@/store/weatherModel'
import { imageryApi as api } from './index'
export interface BBox { west: number; south: number; east: number; north: number }
const cache = new Map<string, { until: number; value: Promise<LayerResponse> }>()
let cooldown = 0
export const layersApi = {
  get: (layer: LayerType, bbox: BBox, zoom: number) => {
    const model = useWeatherModel.getState().model
    const span = Math.max(4, Math.ceil(Math.max(bbox.east - bbox.west, bbox.north - bbox.south) / 4) * 4)
    const key = [model, layer, span, Math.floor(bbox.west / span), Math.floor(bbox.south / span), Math.ceil(bbox.east / span), Math.ceil(bbox.north / span)].join(':')
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
