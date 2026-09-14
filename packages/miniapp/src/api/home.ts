/**
 * 首页聚合与趋势。docs/06 §六、§八
 */
import type {
  HomeResponse, MapOverviewResponse, StationDetailResponse, TrendMetric, TrendSeries,
} from '@enersight/core/types'
import { ApiError } from '@enersight/core/api'
import { api } from './index'

export const homeApi = {
  get: (stationId?: string) =>
    api.get<HomeResponse>('/v1/home', stationId ? { station_id: stationId } : undefined),

  /** dayOffset：今日起第几天（0–6），与七天预测所选日期对应；今日不带参数 */
  trends: (stationId: string, metric: TrendMetric, dayOffset = 0) =>
    api.get<TrendSeries>('/v1/trends', { station_id: stationId, metric, day_offset: dayOffset || undefined }),

  detail: (stationId: string) =>
    api.get<StationDetailResponse>(`/v1/stations/${stationId}/detail`),

  /** 未指定站点：用 home 的默认站点逻辑，再取详情 */
  detailDefault: async () => {
    const h = await api.get<HomeResponse>('/v1/home')
    if (!h.station) throw new ApiError('STATION_NOT_FOUND', '还没有站点', 404)
    return api.get<StationDetailResponse>(`/v1/stations/${h.station.id}/detail`)
  },

  mapOverview: (stationId?: string) =>
    api.get<MapOverviewResponse>('/v1/map/overview', stationId ? { station_id: stationId } : undefined),
}
