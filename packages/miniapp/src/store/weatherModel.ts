import Taro from '@tarojs/taro'
import { create } from 'zustand'

export const WEATHER_MODELS = [
  { id: 'best_match', label: '自动选择', description: 'Open-Meteo 按位置自动选择' },
  { id: 'ecmwf_ifs', label: 'ECMWF IFS', description: '欧洲中期天气预报中心' },
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
