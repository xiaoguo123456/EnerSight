/** AI 分析报告。docs/06 §十 */
import type { AIReportResponse } from '@enersight/core/types'
import { api } from './index'

export const reportsApi = {
  get: (stationId: string, date?: string) =>
    api.get<AIReportResponse>(`/v1/reports/${stationId}`, date ? { date } : undefined),
}
