/**
 * 首页聚合与趋势。docs/06 §六、§八
 */
import type { HomeResponse, TrendMetric, TrendSeries } from '@enersight/core/types'
import { api } from './index'

export const homeApi = {
  get: (stationId?: string) =>
    api.get<HomeResponse>('/v1/home', stationId ? { station_id: stationId } : undefined),

  trends: (stationId: string, metric: TrendMetric) =>
    api.get<TrendSeries>('/v1/trends', { station_id: stationId, metric }),
}
