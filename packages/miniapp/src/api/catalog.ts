/** 公开电站目录。docs/06 §5.5 */
import type { CatalogSearchResponse, StationType } from '@enersight/core/types'
import { api } from './index'

export interface CatalogQuery {
  keyword?: string
  /** "lat,lng"，按客户端坐标系 */
  near?: string
  /** "west,south,east,north"，按客户端坐标系 */
  bbox?: string
  type?: StationType
  limit?: number
}

export const catalogApi = {
  search: (q: CatalogQuery) =>
    api.get<CatalogSearchResponse>('/v1/stations/catalog', q as Record<string, string | number | undefined>),
}
