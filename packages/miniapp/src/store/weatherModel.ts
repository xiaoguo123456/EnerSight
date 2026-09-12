import Taro from '@tarojs/taro'
import { create } from 'zustand'

// 自动选择在境内实测就是 ECMWF IFS 9 km（docs/17 §二），请求仍传 best_match 以保留上游回退，
// 展示直接标明模型；原先单独的 ECMWF 选项与它是同一份数据，已合并。
export const WEATHER_MODELS = [
  { id: 'best_match', label: 'ECMWF IFS 9 km · 自动', description: '欧洲中期天气预报中心，境内默认' },
  { id: 'gfs_global', label: 'GFS', description: '美国 NOAA 全球预报' },
  { id: 'icon_global', label: 'ICON', description: '德国气象局全球预报' },
] as const
export type WeatherModel = typeof WEATHER_MODELS[number]['id']
const KEY = 'enersight_weather_model'
function restore(): WeatherModel {
  try { const saved = Taro.getStorageSync(KEY); if (WEATHER_MODELS.some(m => m.id === saved)) return saved } catch { /* 默认自动选择 */ }
  return 'best_match'
}
export const useWeatherModel = create<{ model: WeatherModel; setModel: (model: WeatherModel) => void }>((set) => ({
  model: restore(),
  setModel: (model) => { Taro.setStorageSync(KEY, model); set({ model }) },
}))
export const weatherModelLabel = (id: WeatherModel) => WEATHER_MODELS.find(m => m.id === id)!.label
