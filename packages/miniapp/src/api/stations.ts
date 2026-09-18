/**
 * 站点接口。docs/06 §五
 * 类型全部来自 codegen，不手写。
 */
import type {
  CreateStationRequest, ForecastEvolution, PublicStationListResponse, StationListResponse,
  StationOutlook, StationSummary, StationType, UpdateStationRequest,
} from '@enersight/core/types'
import { useWeatherModel } from '@/store/weatherModel'
import { api } from './index'

export const stationsApi = {
  list: (params?: { type?: StationType; keyword?: string; limit?: number; offset?: number; province?: string; sort?: 'capacity' | 'name' }) =>
    api.get<PublicStationListResponse>('/v1/stations/public', params),

  /** 本账号自建站点。docs/17 §一 */
  mine: (type?: StationType) =>
    api.get<StationListResponse>('/v1/stations', type ? { type } : undefined),

  /**
   * 未来 7 天逐日预测，首页懒加载。docs/17 §二、docs/19 §一
   *
   * 唯一认三模式的接口，所以当前选择（可能是 ensemble）用查询参数显式带上 —— 查询参数优先于请求头，
   * 而请求头为了兼容后端未部署的窗口只带真实模型名。
   */
  outlook: (stationId: string, days = 7) =>
    api.get<StationOutlook>('/v1/predictions/station', {
      station_id: stationId,
      days,
      weather_model: useWeatherModel.getState().model,
    }),

  /** 同一目标日历次起报的变化。只读留档，服务端不重算。docs/19 §二 */
  history: (stationId: string, date: string) =>
    api.get<ForecastEvolution>('/v1/predictions/station/history', {
      station_id: stationId,
      date,
    }),

  create: (body: CreateStationRequest) =>
    api.post<StationSummary>('/v1/stations', body),

  /** 从公开电站目录添加：服务端复制名称/类型/坐标/容量，重复添加返回已有的 */
  addFromCatalog: (catalogId: string) =>
    api.post<StationSummary>('/v1/stations', { catalog_id: catalogId }),

  update: (id: string, body: UpdateStationRequest) =>
    api.patch<StationSummary>(`/v1/stations/${id}`, body),

  remove: (id: string) => api.delete(`/v1/stations/${id}`),
}
