# 06 API 接口契约 · API Contract

> EnerSight AI 新能源气象遥感分析平台 · 文档包 V1.0
>
> 小程序 ↔ BFF 的接口定义。对应 `packages/core/api/` 与 `packages/server/app/routers/`。
> 字段语义见 [04 数据说明](./04-data-specification.md)，
> 计算口径见 [07 指标计算规则](./07-metrics.md)。


## 一、设计原则


### 按页面聚合，不按资源拆分

这是 BFF，不是 REST 资源 API。

```
✗  GET /stations/1 + GET /weather + GET /index + GET /trends + GET /alerts
   小程序并发请求数有限，5 个请求换 1 屏，首屏必然慢

✓  GET /v1/home?station_id=1
   一个请求返回首页需要的全部数据
```

站点的增删改保持资源风格，读取一律按页面聚合。


### 一屏一接口

| 页面 | 接口 |
| --- | --- |
| 首页 | `GET /v1/home` |
| 地图 | `GET /v1/map/overview` + `GET /v1/map/layers/{layer}` |
| 预警中心 | `GET /v1/alerts` |
| 我的站点 | `GET /v1/stations` |
| 站点详情 | `GET /v1/stations/{id}/detail` |
| AI 分析报告 | `GET /v1/reports/{station_id}` |
| 卫星云图（快捷入口） | `GET /v1/satellite/cloud` |


---

## 二、通用约定


### 2.1 基础

| 项 | 约定 |
| --- | --- |
| Base URL | `https://api.enersight.example.com` |
| 版本 | 路径版本 `/v1/`，破坏性变更升 `/v2/` |
| 协议 | HTTPS（小程序强制），域名需 ICP 备案 |
| 编码 | UTF-8，`Content-Type: application/json` |
| 方法 | GET 读，POST 建，PATCH 改，DELETE 删 |


### 2.2 坐标系（重要）

```
请求参数 coord: "wgs84" | "gcj02"     默认 wgs84

响应中所有经纬度均按请求的坐标系返回，并在 meta 中回显
```

小程序端固定传 `coord=gcj02`，因为要喂给腾讯底图的 `<map>` 组件。

**转换由服务端完成，客户端不做坐标转换。** 理由：
一份实现胜过每端各写一遍；GCJ-02 偏移是非线性的，重复实现必然出现不一致。

服务端内部存储与计算一律 WGS84，仅在响应序列化时转换。
详见 [05 §6.6](./05-architecture.md)。

> ⚠️ **GCJ-02 转换需自行实现。** `pyproj` 只做标准投影变换，
> 不含 GCJ-02 —— 它是中国特有的非线性加密偏移，不是一个投影。
> 需单独实现（约数十行）并覆盖单元测试，不要假设地理库自带。


### 2.3 单位

**API 一律返回基础单位的裸数值，不带单位字符串，不做进位。**

| 量 | 基础单位 | 展示单位 |
| --- | --- | --- |
| 功率 | kW | kW / MW |
| 电量 | kWh | kWh / GWh |
| 辐射 | W/m² | W/m² |
| 温度 | ℃ | ℃ |
| 风速 | m/s | m/s |
| 距离 | km | km |
| 减排量 | kg | 吨 |
| 金额 | 元 | 元 |

`320` → 前端显示 `320 kW`；`1800` → 前端显示 `1.8 MW`。
进位、千分位、小数位由 `core/format` 按 [04 §十](./04-data-specification.md) 处理。

理由：进位规则变了只改一处；且 App 端复用同一套格式化。


### 2.4 时间

ISO 8601 带时区偏移：

```
"2026-09-07T14:00:00+08:00"
```

全部按站点当地时区。不使用时间戳数字，不使用不带时区的字符串。


### 2.5 空值

**数据缺失一律返回 `null`，不省略字段，不用 0 代替。**

前端据此执行 [07 §六](./07-metrics.md) 的降级规则：
`delta` 为 `null` 时隐藏环比标签，`score` 为 `null` 时显示「数据获取中」。

用 0 代替缺失会导致「环比 0%」「指数 0 分」这类错误展示。


### 2.6 响应包装

成功：

```json
{
  "data": { },
  "meta": { "coord": "gcj02", "server_time": "2026-09-07T14:00:00+08:00" }
}
```

失败：

```json
{
  "error": {
    "code": "STATION_NOT_FOUND",
    "message": "站点不存在"
  }
}
```

`message` 可直接展示给用户，服务端负责中文化。


### 2.7 缓存

服务端按 [05 §五](./05-architecture.md) 的 TTL 设置响应头：

```
Cache-Control: public, max-age=600
ETag: "..."
```

图层图片走 CDN，长缓存 + URL 带版本号，不依赖协商缓存。


---

## 三、接口清单


| # | 方法 | 路径 | 说明 | 章节 |
| --- | --- | --- | --- | --- |
| 1 | POST | `/v1/auth/login` | 微信登录 | §4.1 |
| 2 | GET | `/v1/home` | 首页聚合 | §六 |
| 3 | GET | `/v1/stations` | 站点列表 | §5.1 |
| 4 | POST | `/v1/stations` | 新增站点 | §5.2 |
| 5 | GET | `/v1/stations/{id}/detail` | 站点详情聚合 | §5.3 |
| 6 | PATCH | `/v1/stations/{id}` | 编辑站点 | §5.4 |
| 7 | DELETE | `/v1/stations/{id}` | 删除站点 | §5.4 |
| 8 | GET | `/v1/map/overview` | 地图底部面板 | §7.1 |
| 9 | GET | `/v1/map/layers/{layer}` | 图层图片与图例 | §7.2 |
| 10 | GET | `/v1/satellite/cloud` | 卫星云图 | §7.3 |
| 11 | GET | `/v1/trends` | 趋势数据 | §八 |
| 12 | GET | `/v1/alerts` | 预警列表 | §9.1 |
| 13 | GET | `/v1/alerts/current` | 当前预警 + 云团外推 | §9.2 |
| 14 | GET | `/v1/reports/{station_id}` | AI 分析报告 | §十 |
| 15 | GET | `/v1/geo/search` | 地点搜索 | §十一 |
| 16 | GET | `/v1/geo/reverse` | 逆地理编码 | §十一 |


---

## 四、认证


### 4.1 登录

```
POST /v1/auth/login
```

```json
{ "code": "wx.login 返回的 code" }
```

响应：

```json
{
  "data": {
    "token": "eyJhbGciOi...",
    "expires_in": 604800
  }
}
```

服务端用 `code` 调微信 `code2session` 换 `openid`，签发 JWT。
**`openid` 与 `session_key` 不下发到前端。**


### 4.2 鉴权

除登录外全部接口需带：

```
Authorization: Bearer <token>
```

token 过期返回 `401` + `code: "TOKEN_EXPIRED"`，
`core/api` 拦截后自动重新登录并重试一次。


---

## 五、站点


### 5.1 站点列表

```
GET /v1/stations?type=solar|wind&coord=gcj02
```

```ts
interface StationListResponse {
  stations: StationSummary[]
  counts: { all: number; solar: number; wind: number }   // 筛选 Tab 的计数
}

interface StationSummary {
  id: string
  name: string
  type: "solar" | "wind"
  status: "normal" | "standby" | "fault"
  capacity: number              // kW
  latitude: number
  longitude: number
  address: string | null
  image: string | null
  metrics: StationMetrics
}

interface StationMetrics {
  daily_generation: number | null    // kWh 今日发电
  current_power: number | null       // kW  实时功率
  total_generation: number | null    // kWh 累计发电
  co2_reduction: number | null       // kg  减排量
}
```

`counts` 单独返回，避免前端在分页数据上算总数。

> 储能类型 V1 不实现，`type` 枚举只有 `solar` 与 `wind`。


### 5.2 新增站点

```
POST /v1/stations
```

```ts
interface CreateStationRequest {
  name: string
  type: "solar" | "wind"
  latitude: number
  longitude: number
  capacity: number           // kW
  coord?: "wgs84" | "gcj02"  // 入参坐标系，默认 wgs84

  // 出力模型参数，选填，不填用默认值（见 07 §2.3）
  tilt?: number              // 光伏倾角（°），默认 |latitude|
  azimuth?: number           // 光伏方位角（°），默认 180
  hub_height?: number        // 风机轮毂高度（m），默认按容量估算
}
```

服务端行为：

- 按经纬度逆地理编码填充 `address`
- 按 `type` 分配默认封面图
- 校验：`capacity > 0`，经纬度在合法范围

响应返回完整的 `StationSummary`。


### 5.3 站点详情

```
GET /v1/stations/{id}/detail?coord=gcj02
```

```ts
interface StationDetailResponse {
  station: StationSummary
  weather: CurrentWeather
  index: EnergyIndex
  trends: TrendSeries          // 默认辐射，24 小时
  updated_at: string           // 「数据更新时间」
}
```


### 5.4 编辑 / 删除

```
PATCH  /v1/stations/{id}     body 为 CreateStationRequest 的任意子集
DELETE /v1/stations/{id}     204 No Content
```


---

## 六、首页


```
GET /v1/home?station_id={id}&coord=gcj02
```

`station_id` 省略时用用户的默认站点；用户无站点时返回 `has_station: false`，
前端引导去创建。

```ts
interface HomeResponse {
  has_station: boolean
  station: StationSummary | null
  index: EnergyIndex | null
  weather: CurrentWeather | null
  trends: TrendSeries | null          // 24 小时，默认辐射
  alert: AlertSummary | null          // 今日最高等级一条，无预警为 null
}
```

一个请求覆盖首页全部模块。快捷入口是静态配置，不走接口。


---

## 七、地图


### 7.1 底部面板

```
GET /v1/map/overview?station_id={id}&coord=gcj02
```

```ts
interface MapOverviewResponse {
  station: StationSummary
  index: EnergyIndex
  weather: CurrentWeather
  ai_hint: string | null      // AI 提示条：「未来2小时整体适宜发电，14:30后云量逐步增加」
}
```


### 7.2 图层

```
GET /v1/map/layers/{layer}?bbox={w},{s},{e},{n}&zoom={z}&coord=gcj02
```

`layer`：`cloud` | `wind` | `temperature` | `radiation`
（`station` 图层走 `/v1/stations`，不在此接口）

```ts
interface LayerResponse {
  layer: LayerType
  observed_at: string          // 数据观测时间，非请求时间
  unit: string | null          // "W/m²"，云图为 null
  legend: Legend
  frames: LayerFrame[]         // 静态图层长度为 1，风场为多帧
  frame_interval_ms: number | null   // 风场轮播间隔，静态图层为 null
}

interface LayerFrame {
  images: LayerImage[]         // 覆盖请求 bbox 所需的量化块
}

interface LayerImage {
  url: string                  // CDN 地址，带版本号
  bounds: {                    // 已按 coord 转换
    sw: { latitude: number; longitude: number }
    ne: { latitude: number; longitude: number }
  }
}

interface Legend {
  title: string                // "辐射强度（W/m²）"
  type: "gradient" | "scale"
  stops: number[] | null       // [0,200,400,600,800,1000]
  labels: [string, string] | null   // 云图用 ["低","高"]
  colors: string[]             // 色带取色点
}
```

关键约定：

- `bbox` 由服务端**对齐到量化块边界**后再返回，客户端按返回的 `bounds` 贴图，
  不要用自己请求的 `bbox`（见 [05 §6.4](./05-architecture.md)）
- `zoom` 超过 8 时服务端返回 zoom 8 的图，客户端拉伸，不报错
- 图例随图层变化，客户端不硬编码色阶
- `observed_at` 必须展示，它不等于当前时间


### 7.3 卫星云图（预警页）

```
GET /v1/satellite/cloud?station_id={id}&coord=gcj02
```

```ts
interface SatelliteCloudResponse {
  band: "visible" | "infrared" | "vapor"   // 服务端按昼夜自动选择
  observed_at: string
  image: LayerImage
  legend: Legend
  station_marker: { latitude: number; longitude: number }
}
```


---

## 八、趋势


```
GET /v1/trends?station_id={id}&metric={m}&range={r}
```

| 参数 | 取值 |
| --- | --- |
| `metric` | `radiation` \| `wind_speed` \| `cloud_cover` |
| `range` | `24h` \| `7d` |

```ts
interface TrendSeries {
  metric: "radiation" | "wind_speed" | "cloud_cover"
  unit: string
  range: "24h" | "7d"
  y_max: number | null         // 固定纵轴上限；null 表示自适应
  points: TrendPoint[]
}

interface TrendPoint {
  time: string
  value: number | null         // 缺测为 null，前端断线不补 0
}
```

`y_max`：辐射固定 1000，云量固定 100，风速为 `null` 自适应。
由服务端下发而非前端硬编码，便于调整。


---

## 九、预警


### 9.1 预警列表

```
GET /v1/alerts?station_id={id}&level={l}&cursor={c}&limit=20
```

`level`：`all` | `minor` | `moderate` | `severe`

```ts
interface AlertListResponse {
  alerts: AlertSummary[]
  next_cursor: string | null
}

interface AlertSummary {
  id: string
  level: "minor" | "moderate" | "severe" | "cleared"
  title: string
  description: string
  published_at: string
  station_id: string
  source: "satellite" | "forecast"
}
```

`cleared`（解除预警）不出现在 `minor/moderate/severe` 筛选结果里，
仅在 `all` 中出现。


### 9.2 当前预警

```
GET /v1/alerts/current?station_id={id}&coord=gcj02
```

```ts
interface CurrentAlertResponse {
  alert: AlertSummary | null
  cloud_motion: CloudMotion | null      // 仅卫星短临预警有
  satellite: SatelliteCloudResponse | null
}

interface CloudMotion {
  distance_km: number
  direction: string            // "东北"
  direction_detail: string     // "向东/东北方向移动"
  impact_in_minutes: number
  impact_start_time: string
  reference_station: string    // "距苏州光伏站"
}
```

无预警时 `alert` 为 `null`，`satellite` 仍返回（预警页始终展示云图）。


---

## 十、AI 分析报告


```
GET /v1/reports/{station_id}?date=2026-09-07
```

`date` 省略取当日。

```ts
interface AIReportResponse {
  station: StationSummary
  report_date: string
  generated_at: string
  is_fallback: boolean         // true 表示走了规则模板降级

  verdict_title: string
  verdict_detail: string
  periods: ReportPeriod[]      // 固定 3 段
  risk_title: string | null
  risk_detail: string | null
  suggestions: string[]
  summary: ReportSummary
}

interface ReportPeriod {
  period: "morning" | "afternoon" | "evening"
  time_range: string           // "06:00 – 12:00"
  weather_summary: string
  generation_impact: string
  level: "good" | "warning" | "risk"
}

interface ReportSummary {
  generation: MetricWithDelta          // kWh
  equivalent_hours: MetricWithDelta    // h
  co2_reduction: MetricWithDelta       // kg
  estimated_revenue: MetricWithDelta   // 元
}
```

`is_fallback` 供前端埋点统计降级率，**不用于改变展示**
（[08 §六](./08-ai-design.md)：降级不向用户暴露技术细节）。

报告由定时任务预生成，此接口只读缓存，不触发模型调用。
当日报告尚未生成时返回 `202` + `code: "REPORT_GENERATING"`。


---

## 十一、地理服务


代理腾讯位置服务，避免在前端暴露 key。

```
GET /v1/geo/search?keyword={k}&coord=gcj02
GET /v1/geo/reverse?latitude={lat}&longitude={lng}&coord=wgs84
```

```ts
interface GeoSearchResponse {
  results: GeoPlace[]
}

interface GeoPlace {
  name: string
  address: string
  latitude: number
  longitude: number
  type: "city" | "poi" | "station"    // station 为用户自己的站点
}

interface GeoReverseResponse {
  address: string              // "江苏省苏州市吴中区"
  province: string
  city: string
  district: string
}
```

地图页搜索框「搜索城市 / 坐标 / 站点」：
服务端合并三类结果 —— 用户站点匹配、城市 POI、以及直接解析的经纬度输入。


---

## 十二、公共类型


```ts
type LayerType = "cloud" | "wind" | "temperature" | "radiation"

interface EnergyIndex {
  score: number | null                 // 0-100，不可算时为 null
  level: "excellent" | "good" | "fair" | "poor" | null
  summary: string | null               // AI 一句话结论
  estimated: boolean                   // true 表示部分气象因子由气候平均值填补
  attribution: IndexAttribution[]      // 归因，供说明弹窗展示
}

// 归因不是权重，是「该因子使指数偏离理想值多少分」，算出来的
// 见 07 §1.6
interface IndexAttribution {
  factor: "radiation" | "temperature" | "wind"
  delta: number                        // 负数为扣分，正数为加分
  description: string                  // 「云层使辐照降至晴空的 64%」
}

interface CurrentWeather {
  temperature: MetricWithDelta         // ℃
  apparent_temperature: number | null  // ℃ 体感
  humidity: number | null              // %
  wind_speed: MetricWithDelta          // m/s
  wind_direction: number | null        // °
  cloud_cover: MetricWithDelta         // %
  radiation: MetricWithDelta           // W/m²
  weather_text: string | null          // "晴转多云"
  observed_at: string
}

interface MetricWithDelta {
  value: number | null
  delta_percent: number | null         // 较昨日同期，null 时前端隐藏标签
}
```

`MetricWithDelta` 把「值 + 环比」绑成一个类型，
避免出现 `value` 有而 `delta` 字段漏传的情况。


---

## 十三、错误码


| HTTP | code | 说明 |
| --- | --- | --- |
| 400 | `INVALID_PARAM` | 参数校验失败 |
| 400 | `INVALID_COORDINATE` | 经纬度超出合法范围 |
| 401 | `TOKEN_EXPIRED` | token 过期，客户端自动重登重试 |
| 401 | `UNAUTHORIZED` | 未登录 |
| 403 | `STATION_FORBIDDEN` | 站点不属于当前用户 |
| 404 | `STATION_NOT_FOUND` | 站点不存在 |
| 404 | `LAYER_NOT_FOUND` | 图层类型不支持 |
| 202 | `REPORT_GENERATING` | 报告生成中，稍后重试 |
| 429 | `RATE_LIMITED` | 请求过于频繁 |
| 502 | `UPSTREAM_UNAVAILABLE` | 上游数据源不可用 |
| 503 | `DATA_UNAVAILABLE` | 该区域暂无数据 |

`502` 与 `503` 的区别：`502` 是上游故障（可重试），
`503` 是该位置本就没有数据（重试无用，前端应展示空态）。


---

## 十四、前端调用约定


`packages/core/api/` 统一封装，页面不直接发请求。

```ts
// core/api/client.ts
export function createClient(adapter: HttpAdapter, opts: ClientOptions): ApiClient
```

本文档的 TypeScript 类型是**契约描述**。
`core/types/` 由服务端的 Pydantic 模型经 OpenAPI codegen 生成，
不手工维护 —— 本文档与生成结果不一致时，以 OpenAPI schema 为准，
并回头修正本文档。生成流程见 [05 §1.3](./05-architecture.md)。

`core/api` 负责：

| 职责 | 说明 |
| --- | --- |
| 注入 token | 从 adapter 提供的存储读取 |
| 401 自动重登 | 重登后重试一次，失败才抛错 |
| 坐标系参数 | 按端注入固定的 `coord`，页面不传 |
| 错误归一 | 网络错误与业务错误统一为 `ApiError` |
| 超时与重试 | 默认 10s；仅对 `502` 与网络错误重试，最多 2 次 |

`ApiError` 不做重试的情况：`4xx` 全部不重试，`503` 不重试。


平台差异通过 `HttpAdapter` 隔离（见 [05 §四](./05-architecture.md)）：
小程序注入 `Taro.request`，App 注入 `fetch`。


---

## 十五、待定项


| # | 事项 | 影响 |
| --- | --- | --- |
| 1 | 站点是否支持多用户共享 | 影响 `STATION_FORBIDDEN` 的判定与站点归属模型 |
| 2 | 预警是否需要订阅推送（微信订阅消息） | 需增加订阅管理接口 |
| 3 | 「我的」页面需求未定 | 用户配置类接口未设计 |
| 4 | 7 天趋势的采样粒度（逐日还是逐 3 小时） | 影响 `TrendSeries` 的点数 |
