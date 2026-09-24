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
export type ProvinceBoundsResponse = S['ProvinceBoundsResponse']
export type GeoPlaceType = GeoPlace['type']
export type CatalogPlant = S['CatalogPlantOut']
export type CatalogSearchResponse = S['CatalogSearchResponse']

export type LayerType = S['LayerType']
export type LayerResponse = S['LayerResponse']
export type MapCloudHistoryResponse = S['MapCloudHistoryResponse']
export type LayerImage = S['LayerImage']
export type Legend = S['Legend']
export type SatelliteCloudResponse = S['SatelliteCloudResponse']
export type SatelliteIrradiance = S['SatelliteIrradiance']

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
export type FleetDay = S['FleetDay']
/** 全目录同目标日可比签发变化。docs/20 */
export type FleetSignalResponse = S['FleetSignalResponse']
export type FleetRegionSignal = S['FleetRegionSignal']
export type ForecastBasis = S['ForecastBasis']
export type StationOutlook = S['StationOutlook']
export type DailyOutlook = S['DailyOutlook']
/** 三模式区间与预报演变。docs/19 §一、§二 */
export type EnsembleSummary = S['EnsembleSummary']
export type MemberEnergy = S['MemberEnergy']
export type SpreadLevel = S['SpreadLevel']
export type ForecastEvolution = S['ForecastEvolution']
export type Issuance = S['Issuance']
export type ConvergenceLevel = S['ConvergenceLevel']
/** 实测随手记与订正。docs/19 §三 */
export type MeasuredSummary = S['MeasuredSummary']
export type MeasuredEntry = S['MeasuredEntry']
export type CorrectionStatus = S['CorrectionStatus']
export type CorrectionApplied = S['CorrectionApplied']
export type RecordMeasuredRequest = S['RecordMeasuredRequest']
export type CurtailmentRule = S['CurtailmentRule']
export type CurtailmentWindow = S['CurtailmentWindow']
export type PowerCurvePoint = S['PowerCurvePoint']
export type TurbineClass = NonNullable<S['StationSummary']['turbine_class']>
export type Mounting = NonNullable<S['StationSummary']['mounting']>
