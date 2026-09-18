/**
 * 实测电量与订正。只对我的电站，需登录。docs/19 §三
 * 类型全部来自 codegen，不手写。
 */
import type { MeasuredSummary, RecordMeasuredRequest } from '@enersight/core/types'
import { api } from './index'

export const measuredApi = {
  get: (stationId: string) =>
    api.get<MeasuredSummary>(`/v1/stations/${encodeURIComponent(stationId)}/measured`),

  /** 记完服务端立即回算模型同期电量并重新拟合，返回最新的记录与订正状态。 */
  record: (stationId: string, body: RecordMeasuredRequest) =>
    api.post<MeasuredSummary>(`/v1/stations/${encodeURIComponent(stationId)}/measured`, body),

  remove: (stationId: string, entryId: number) =>
    api.delete<MeasuredSummary>(`/v1/stations/${encodeURIComponent(stationId)}/measured/${entryId}`),
}
