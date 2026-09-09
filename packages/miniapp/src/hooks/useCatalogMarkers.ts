import Taro from '@tarojs/taro'
import { useCallback, useRef, useState } from 'react'
import type { CatalogPlant } from '@enersight/core/types'
import { catalogApi } from '@/api/catalog'

// 文件路径而不是 import：小程序 marker 的 iconPath 不认 base64，
// 而 vite 会把小于 4 KB 的图内联。文件由 config/index.ts 的 copy 规则拷进产物。
const ICON = { solar: '/assets/markers/plant-solar.png', wind: '/assets/markers/plant-wind.png' } as const

/** 用户站点占 1..999；公开电站 marker 从 1000 起，避免 id 冲突 */
const BASE_ID = 1000
/** 缩放级别低于此不拉：省级视野下几百个点没有信息量，只会挤成一团 */
const MIN_SCALE = 7
const LIMIT = 100

export interface PlantMarker {
  id: number
  latitude: number
  longitude: number
  iconPath: string
  width: number
  height: number
  anchor: { x: number; y: number }
}

/**
 * 视野内的公开电站 marker。由地图页在 regionchange(end, 用户手势) 时调用 refresh。
 * 数据与 useMapLayer 一样来自服务端 bbox 查询，客户端不缓存全量目录。
 */
export function useCatalogMarkers(mapId: string, enabled: boolean, scale = 9) {
  const [plants, setPlants] = useState<CatalogPlant[]>([])
  const seq = useRef(0)

  const refresh = useCallback(async () => {
    if (!enabled) { setPlants([]); return }
    const ctx = Taro.createMapContext(mapId)
    const mine = ++seq.current
    try {
      const scale = await new Promise<number>((resolve) =>
        ctx.getScale({ success: (r) => resolve(r.scale), fail: () => resolve(MIN_SCALE) }))
      if (scale < MIN_SCALE) { setPlants([]); return }
      const region: any = await new Promise((resolve, reject) => ctx.getRegion({ success: resolve, fail: reject }))
      const bbox = [
        region.southwest.longitude, region.southwest.latitude,
        region.northeast.longitude, region.northeast.latitude,
      ].map((v: number) => v.toFixed(4)).join(',')
      const res = await catalogApi.search({ bbox, limit: LIMIT })
      if (mine === seq.current) setPlants(res.plants)
    } catch {
      // 拉不到就保持上一批，不打断地图
    }
  }, [mapId, enabled])

  // 小比例尺按经纬网格合并标记；放大后恢复单站，避免密集区域互相遮挡。
  const groups = new Map<string, CatalogPlant[]>()
  const cell = 360 / 2 ** scale * 0.15
  for (const plant of plants) {
    const key = scale >= 14 ? plant.id : `${Math.floor(plant.longitude / cell)},${Math.floor(plant.latitude / cell)}`
    groups.set(key, [...(groups.get(key) ?? []), plant])
  }
  const entries = [...groups.values()]
  const markers = entries.map((group, i) => {
    const p = group[0]!
    const clustered = group.length > 1
    return {
      id: BASE_ID + i,
      latitude: group.reduce((n, item) => n + item.latitude, 0) / group.length,
      longitude: group.reduce((n, item) => n + item.longitude, 0) / group.length,
      iconPath: ICON[p.type], width: clustered ? 30 : 22, height: clustered ? 30 : 22,
      anchor: { x: 0.5, y: 0.5 },
      ...(clustered ? { callout: { content: `${group.length} 座`, display: 'ALWAYS', color: '#334155', bgColor: '#ffffff', padding: 5, borderRadius: 6, fontSize: 12 } } : {}),
    }
  })
  const byMarkerId = (id: number) => {
    const group = entries[id - BASE_ID]
    return group?.length === 1 ? group[0] : undefined
  }
  const clusterByMarkerId = (id: number) => entries[id - BASE_ID]?.length! > 1 ? markers[id - BASE_ID] : undefined
  return { plants, markers, byMarkerId, clusterByMarkerId, refresh }
}
