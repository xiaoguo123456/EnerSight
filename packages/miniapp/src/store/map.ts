/**
 * 地图状态。激活图层持久化，视窗不持久化 —— 见 docs/05 §7.2
 */
import { create } from 'zustand'
import Taro from '@tarojs/taro'
import type { LayerType } from '@enersight/core/types'

const LAYER_KEY = 'enersight_active_layer'
let nextFocusId = 0

interface MapState {
  activeLayer: LayerType
  cloudMode: 'auto' | 'satellite'
  provinceFocus: { id: number; names: string[] } | null
  setActiveLayer: (l: LayerType) => void
  setCloudMode: (mode: 'auto' | 'satellite') => void
  setProvinceFocus: (names: string[]) => void
  consumeProvinceFocus: (id: number) => void
  restore: () => void
}

export const useMapStore = create<MapState>((set) => ({
  activeLayer: 'cloud',
  cloudMode: 'auto',
  provinceFocus: null,
  setActiveLayer: (l) => {
    Taro.setStorageSync(LAYER_KEY, l)
    set({ activeLayer: l })
  },
  setCloudMode: (cloudMode) => set({ cloudMode }),
  setProvinceFocus: (names) => set({ provinceFocus: names.length ? { id: ++nextFocusId, names } : null }),
  consumeProvinceFocus: (id) => set(state => ({ provinceFocus: state.provinceFocus?.id === id ? null : state.provinceFocus })),
  restore: () => {
    try {
      const l = Taro.getStorageSync(LAYER_KEY) as LayerType | ''
      if (l) set({ activeLayer: l })
    } catch {
      // 保持默认图层
    }
  },
}))
