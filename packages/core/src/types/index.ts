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
 * 服务端路由尚未实现，别名暂时手写；对应 schema 落地后改为从 generated 取。
 */

export type { components, paths } from './generated'

export type Coord = 'wgs84' | 'gcj02'
export type StationType = 'solar' | 'wind'
export type StationStatus = 'normal' | 'standby' | 'fault'
export type LayerType = 'cloud' | 'wind' | 'temperature' | 'radiation'
export type AlertLevel = 'minor' | 'moderate' | 'severe' | 'cleared'
export type IndexLevel = 'excellent' | 'good' | 'fair' | 'poor'

/** 值 + 环比。delta_percent 为 null 时前端隐藏环比标签。docs/06 §2.5 */
export interface MetricWithDelta {
  value: number | null
  delta_percent: number | null
}

export interface ApiEnvelope<T> {
  data: T
  meta: { coord: Coord; server_time: string }
}

export interface ApiErrorBody {
  error: { code: string; message: string }
}
