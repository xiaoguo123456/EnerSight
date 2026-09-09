/**
 * 接口类型。
 *
 * `generated.ts` 是 codegen 产物，**禁止手工修改**：
 *   packages/server/app/schemas/ 的 Pydantic 模型
 *     → openapi.json
 *     → openapi-typescript
 * 生成命令 `make codegen`，已纳入 CI。契约见 docs/06-api-contract.md。
 *
 * 本文件只做便捷别名，把生成类型里冗长的路径映射成短名字。
 */

import type { components } from './generated'

export type { components, paths } from './generated'

type S = components['schemas']

// ── 已由接口引用，取自生成产物 ──
export type Coord = S['Coord']
export type StationType = S['StationSummary']['type']
export type StationStatus = S['StationSummary']['status']
export type AlertLevel = S['AlertLevel']
export type IndexLevel = S['IndexLevel']
export type MetricWithDelta = S['MetricWithDelta']
export type EnergyIndex = S['EnergyIndex']
export type IndexAttribution = S['IndexAttribution']
export type CurrentWeather = S['CurrentWeather']
export type TrendSeries = S['TrendSeries']
export type TrendPoint = S['TrendPoint']
export type TrendMetric = S['TrendMetric']
export type AlertSummary = S['AlertSummary']
export type HomeResponse = S['HomeResponse']
export type StationDetailResponse = S['StationDetailResponse']
export type MapOverviewResponse = S['MapOverviewResponse']
export type AlertListResponse = S['AlertListResponse']
export type CurrentAlertResponse = S['CurrentAlertResponse']
export type CloudMotion = S['CloudMotion']
export type AIReportResponse = S['AIReportResponse']
export type ReportSummary = S['ReportSummary']
export type ReportPeriodOut = S['ReportPeriodOut']
export type GeoPlace = S['GeoPlace']
export type GeoSearchResponse = S['GeoSearchResponse']
export type GeoReverseResponse = S['GeoReverseResponse']
export type GeoPlaceType = GeoPlace['type']
export type CatalogPlant = S['CatalogPlantOut']
export type CatalogSearchResponse = S['CatalogSearchResponse']

export type LayerType = S['LayerType']
export type LayerResponse = S['LayerResponse']
export type LayerImage = S['LayerImage']
export type Legend = S['Legend']
export type SatelliteCloudResponse = S['SatelliteCloudResponse']

export type StationSummary = S['StationSummary']
export type StationMetrics = S['StationMetrics']
export type StationListResponse = S['StationListResponse']
export type CreateStationRequest = S['CreateStationRequest']
export type UpdateStationRequest = S['UpdateStationRequest']
export type LoginResponse = S['LoginResponse']

export interface ApiEnvelope<T> {
  data: T
  meta: { coord: Coord; server_time: string }
}

export interface ApiErrorBody {
  error: { code: string; message: string }
}

/** 所有用户共享的分页电站目录。 */
export type PublicStationListResponse = S['PublicStationListResponse']

export type WindVector = S['WindVector']
export type SatelliteHistoryResponse = S['SatelliteHistoryResponse']
export type GenerationPrediction = S['GenerationPrediction']
export type FleetPrediction = S['FleetPrediction']
