/** 地理服务：搜索城市 / 坐标 / 站点 / 公开电站。docs/06 §十一 */
import type { GeoSearchResponse, ProvinceBoundsResponse } from '@enersight/core/types'
import { api } from './index'

export const geoApi = {
  search: (keyword: string) => api.get<GeoSearchResponse>('/v1/geo/search', { keyword }),
  provinceBounds: (names: string[]) => api.get<ProvinceBoundsResponse>('/v1/geo/province-bounds', { provinces: names.join(',') }),
}
