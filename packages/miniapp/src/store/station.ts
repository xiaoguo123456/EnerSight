/**
 * 站点状态。持久化到 Storage —— 见 docs/05 §7.2
 */
import { create } from 'zustand'
import Taro from '@tarojs/taro'

const CURRENT_KEY = 'enersight_current_public_station'

interface StationState {
  currentId: string | null
  setCurrent: (id: string) => void
  restore: () => void
}

export const useStationStore = create<StationState>((set) => ({
  currentId: null,
  setCurrent: (id) => {
    Taro.setStorageSync(CURRENT_KEY, id)
    set({ currentId: id })
  },
  restore: () => {
    try {
      const id = Taro.getStorageSync(CURRENT_KEY)
      if (typeof id === 'string' && /^(gem|wri):/.test(id)) set({ currentId: id })
    } catch {
      // Storage 不可用时保持默认值
    }
  },
}))
