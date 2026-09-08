/** 预警。docs/06 §九 */
import type { AlertListResponse, CurrentAlertResponse } from '@enersight/core/types'
import { api } from './index'

export const alertsApi = {
  current: (stationId?: string) =>
    api.get<CurrentAlertResponse>('/v1/alerts/current', stationId ? { station_id: stationId } : undefined),

  list: (stationId?: string, level: string = 'all') =>
    api.get<AlertListResponse>('/v1/alerts', { station_id: stationId, level }),
}
