/**
 * 站点状态。持久化到 Storage —— 见 docs/05 §7.2
 */
import { create } from 'zustand'
import Taro from '@tarojs/taro'
import type { StationSummary } from '@enersight/core/types'

const CURRENT_KEY = 'enersight_current_public_station'

interface StationState {
  currentId: string | null
  recent: StationSummary[]
  remember: (station: StationSummary) => void
  clearRecent: () => void
  setCurrent: (id: string) => void
  restore: () => void
}

export const useStationStore = create<StationState>((set) => ({
  currentId: null,
  recent: [],
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
  restore: () => {
    try {
      const recent = Taro.getStorageSync('enersight_recent_stations')
      if (Array.isArray(recent)) set({ recent: recent.filter((s) =>
        s && typeof s.id === 'string' && typeof s.name === 'string' && /^(gem|wri):/.test(s.id)
      ).slice(0, 6) })
      const id = Taro.getStorageSync(CURRENT_KEY)
      if (typeof id === 'string' && /^(gem|wri):/.test(id)) set({ currentId: id })
    } catch {
      // Storage 不可用时保持默认值
    }
  },
}))
