/**
 * 站点状态。持久化到 Storage —— 见 docs/05 §7.2
 */
import { create } from 'zustand'
import Taro from '@tarojs/taro'
import type { StationSummary } from '@enersight/core/types'

const CURRENT_KEY = 'enersight_current_public_station'
/** 公开目录 ID 带来源前缀；自建站点是 12 位 hex。docs/17 §一 */
const ID_PATTERN = /^(gem|wri):|^[0-9a-f]{12}$/
const OWN_ID = /^[0-9a-f]{12}$/

interface StationState {
  currentId: string | null
  recent: StationSummary[]
  favorites: StationSummary[]
  toggleFavorite: (station: StationSummary) => void
  remember: (station: StationSummary) => void
  clearRecent: () => void
  setCurrent: (id: string) => void
  restore: () => void
  /** 退出登录或删除数据后，移除本机记住的自建电站 */
  forgetOwn: () => void
}

export const useStationStore = create<StationState>((set) => ({
  currentId: null,
  recent: [],
  favorites: [],
  toggleFavorite: (station) => set((state) => {
    const favorites = state.favorites.some((s) => s.id === station.id)
      ? state.favorites.filter((s) => s.id !== station.id)
      : [station, ...state.favorites].slice(0, 20)
    try { Taro.setStorageSync('enersight_favorite_stations', favorites) } catch { /* 存储不可用时保留当前会话 */ }
    return { favorites }
  }),
  remember: (station) => set((state) => {
    const recent = [station, ...state.recent.filter((s) => s.id !== station.id)].slice(0, 6)
    try { Taro.setStorageSync('enersight_recent_stations', recent) } catch { /* 本机存储不可用不影响查看 */ }
    return { recent }
  }),
  clearRecent: () => {
    try { Taro.removeStorageSync('enersight_recent_stations') } catch { /* 保持内存可用 */ }
    set({ recent: [] })
  },
  setCurrent: (id) => {
    try { Taro.setStorageSync(CURRENT_KEY, id) } catch { /* 不阻断站点切换 */ }
    set({ currentId: id })
  },
  forgetOwn: () => set((state) => {
    const keep = (s: StationSummary) => !s.is_own && !OWN_ID.test(s.id)
    const favorites = state.favorites.filter(keep)
    const recent = state.recent.filter(keep)
    const currentId = state.currentId && OWN_ID.test(state.currentId) ? null : state.currentId
    try {
      Taro.setStorageSync('enersight_favorite_stations', favorites)
      Taro.setStorageSync('enersight_recent_stations', recent)
      if (!currentId) Taro.removeStorageSync(CURRENT_KEY)
    } catch { /* 存储不可用时仅清内存 */ }
    return { favorites, recent, currentId }
  }),
  restore: () => {
    try {
      const favorites = Taro.getStorageSync('enersight_favorite_stations')
      if (Array.isArray(favorites)) set({ favorites: favorites.filter((s) => s && typeof s.id === 'string' && typeof s.name === 'string' && ID_PATTERN.test(s.id)).slice(0, 20) })
      const recent = Taro.getStorageSync('enersight_recent_stations')
      if (Array.isArray(recent)) set({ recent: recent.filter((s) =>
        s && typeof s.id === 'string' && typeof s.name === 'string' && ID_PATTERN.test(s.id)
      ).slice(0, 6) })
      const id = Taro.getStorageSync(CURRENT_KEY)
      if (typeof id === 'string' && ID_PATTERN.test(id)) set({ currentId: id })
    } catch {
      // Storage 不可用时保持默认值
    }
  },
}))
