/**
 * 地图状态。激活图层持久化，视窗不持久化 —— 见 docs/05 §7.2
 */
import { create } from 'zustand'
import Taro from '@tarojs/taro'
import type { LayerType } from '@enersight/core/types'

const LAYER_KEY = 'enersight_active_layer'

interface MapState {
  activeLayer: LayerType
  setActiveLayer: (l: LayerType) => void
  restore: () => void
}

export const useMapStore = create<MapState>((set) => ({
  activeLayer: 'cloud',
  setActiveLayer: (l) => {
    Taro.setStorageSync(LAYER_KEY, l)
    set({ activeLayer: l })
  },
  restore: () => {
    try {
      const l = Taro.getStorageSync(LAYER_KEY) as LayerType | ''
      if (l) set({ activeLayer: l })
    } catch {
      // 保持默认图层
    }
  },
}))
