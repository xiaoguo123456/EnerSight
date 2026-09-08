/** 地图图层。docs/06 §7.2 */
import type { LayerResponse, LayerType } from '@enersight/core/types'
import { api } from './index'

export interface BBox { west: number; south: number; east: number; north: number }

export const layersApi = {
  get: (layer: LayerType, bbox: BBox, zoom: number) =>
    api.get<LayerResponse>(`/v1/map/layers/${layer}`, {
      bbox: `${bbox.west},${bbox.south},${bbox.east},${bbox.north}`,
      zoom,
    }),
}
