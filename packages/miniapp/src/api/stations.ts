/**
 * 站点接口。docs/06 §五
 * 类型全部来自 codegen，不手写。
 */
import type {
  CreateStationRequest, StationListResponse, StationSummary, StationType,
  UpdateStationRequest,
} from '@enersight/core/types'
import { api } from './index'

export const stationsApi = {
  list: (type?: StationType) =>
    api.get<StationListResponse>('/v1/stations', type ? { type } : undefined),

  create: (body: CreateStationRequest) =>
    api.post<StationSummary>('/v1/stations', body),

  /** 从公开电站目录添加：服务端复制名称/类型/坐标/容量，重复添加返回已有的 */
  addFromCatalog: (catalogId: string) =>
    api.post<StationSummary>('/v1/stations', { catalog_id: catalogId }),

  update: (id: string, body: UpdateStationRequest) =>
    api.patch<StationSummary>(`/v1/stations/${id}`, body),

  remove: (id: string) => api.delete(`/v1/stations/${id}`),
}
