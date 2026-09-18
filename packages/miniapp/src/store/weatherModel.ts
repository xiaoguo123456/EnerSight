import Taro from '@tarojs/taro'
import { create } from 'zustand'

// 自动选择在境内实测就是 ECMWF IFS 9 km（docs/17 §二），请求仍传 best_match 以保留上游回退，
// 展示直接标明模型；原先单独的 ECMWF 选项与它是同一份数据，已合并。
//
// 「三模式」是默认（docs/19 §一）：只有 7 天预测接口认它，服务端对其余接口自动退回自动选择，
// 并在响应头里说明。所以这里照常把它带给所有接口，不在前端分叉。
export const WEATHER_MODELS = [
  { id: 'ensemble', label: '三模式', description: 'ECMWF、ICON、GFS 一起算，预测卡给出分歧区间' },
  { id: 'best_match', label: '自动选择', description: '按服务端确认的气象模型显示来源' },
  { id: 'gfs_global', label: 'GFS', description: '美国 NOAA 全球预报' },
  { id: 'icon_global', label: 'ICON', description: '德国气象局全球预报' },
] as const
export type WeatherModel = typeof WEATHER_MODELS[number]['id']
const KEY = 'enersight_weather_model'
function restore(): WeatherModel {
  try { const saved = Taro.getStorageSync(KEY); if (WEATHER_MODELS.some(m => m.id === saved)) return saved } catch { /* 默认三模式 */ }
  return 'ensemble'
}
export const useWeatherModel = create<{ model: WeatherModel; setModel: (model: WeatherModel) => void }>((set) => ({
  model: restore(),
  setModel: (model) => { Taro.setStorageSync(KEY, model); set({ model }) },
}))
export const weatherModelLabel = (id: WeatherModel) => WEATHER_MODELS.find(m => m.id === id)!.label

/**
 * 服务端对这个选择实际会用哪个模型回答。
 *
 * 三模式只有 7 天预测接口认，其余接口一律退回自动选择。页面按「响应模型 == 当前选择」丢弃
 * 切换前的旧响应时要跟这个比 —— 直接拿 'ensemble' 比，首页与全目录的响应永远对不上，
 * 页面会一直停在加载。docs/19 §一
 */
export const servedModel = (model: WeatherModel): WeatherModel => model === 'ensemble' ? 'best_match' : model
