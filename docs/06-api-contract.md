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


### 2.8 曲线粒度（过渡期）

线上旧版小程序（2026-09-13 起的正式版）按逐小时画图，第 i 个点即 i 点钟。当前曲线为 15 分钟粒度，客户端需声明：

```
X-Resolution-Minutes: 15
```

| 请求 | 曲线 |
| --- | --- |
| 带 `X-Resolution-Minutes: 15` | 按各接口说明返回 15 分钟曲线（96 / 97 点） |
| 不带或其他值 | 响应出口降为逐小时，`resolution_minutes` 为 60（24 / 25 点） |

降为逐小时的规则：
- 功率曲线 `power_kw`、`grid_power_kw` 标注区间起点，取该小时四格平均，缺任一格为 null；电量字段不变。
- 趋势中的辐射是区间均值标在区间末，取整点及之前三格平均，首点只有自身一格；风速、云量等瞬时量取整点值。
- 只换算已算好的同一份结果，不额外请求气象上游。

小程序在 `src/api/index.ts` 的 adapter 里固定携带该头。旧版正式版停用后（网关日志不再出现该版本号的 Referer），删除服务端 `app/curve_resolution.py` 与这个请求头。


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
| 17 | GET | `/v1/stations/catalog` | 公开电站目录 | §5.5 |


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

**开发态**：`ENERSIGHT_DEBUG=true` 且未配置 `WX_APPID` 时，登录不调微信，
直接签发固定开发用户的 token；且不带 token 的请求也按开发用户放行，
省得每次联调先登录。真正的 `code2session` 依赖 AppID/AppSecret，
那是合规那条线的事（09 §二），不让它卡住开发。


### 4.2 鉴权

请求头：

```
Authorization: Bearer <token>
```

按接口分三类，游客模式口径见 09 §4.3：

| 类别 | 接口 | 不带 token 时 |
| --- | --- | --- |
| 公开，不取身份 | `GET /v1/stations/public`、`/v1/stations/catalog`、`/v1/predictions/fleet`、`/v1/predictions/fleet/history`、`/v1/map/layers/{layer}`、`/v1/geo/reverse` | 正常返回 |
| 公开，身份可选 | `GET /v1/home`、`/v1/trends`、`/v1/stations/{id}/detail`、`/v1/predictions/station`、`/v1/map/overview`、`/v1/alerts`、`/v1/alerts/current`、`/v1/satellite/cloud`、`/v1/satellite/cloud/history`、`/v1/reports/{id}`、`/v1/geo/search` | 公开电站正常返回；自建电站返回 `401 LOGIN_REQUIRED`；默认电站只从公开目录选，地址搜索不含自建电站 |
| 需要登录 | `GET` / `POST /v1/stations`、`PATCH` / `DELETE /v1/stations/{id}`、`DELETE /v1/me` | `401 UNAUTHORIZED` |

带了 token 的请求一律校验：过期返回 `401 TOKEN_EXPIRED`，无效返回 `401 UNAUTHORIZED`。
`core/api` 收到这三种 401 时调用各端 `relogin` 并重试一次，并发请求共用一次续登。
小程序只对主动登录过的用户静默续登；游客的 `relogin` 抛 `LOGIN_REQUIRED`，由页面引导到登录页。
游客不带 token 请求公开数据，不自动登录。


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
  is_own: boolean                    // 本账号自建，可编辑删除；目录电站为 false
  curtailment: CurtailmentRule | null // 出力约束，目录电站与未设置为 null（17 §四）
}

interface StationMetrics {
  daily_generation: number | null    // kWh 今日可发电量
  current_power: number | null       // kW  实时功率
  total_generation: number | null    // kWh 累计发电
  co2_reduction: number | null       // kg  减排量
  grid_generation: number | null     // kWh 今日预计上网，计入出力约束；无规则为 null
}

interface CurtailmentRule {
  mode: "ratio" | "schedule"
  ratio_percent: number | null       // ratio：全天出力 × (1 − ratio_percent / 100)
  windows: CurtailmentWindow[]       // schedule：至少一条，最多 12 条
}

interface CurtailmentWindow {
  start_hour: number                 // 0–23
  end_hour: number                   // 0–24，不含；start >= end 表示跨零点
  limit_percent: number              // 时段内出力上限，装机容量的百分比，0 为停机
  weekdays: number[]                 // ISO 1–7，空为每天
}
```

`counts` 单独返回，避免前端在分页数据上算总数。

> 储能类型 V1 不实现，`type` 枚举只有 `solar` 与 `wind`。

**已落地。** `metrics` 四项在指标服务（`/v1/home` 那一步）接入前恒为 `null`，
前端展示「—」。`address` 在逆地理编码接入前为 `null`。


### 5.2 新增站点

```
POST /v1/stations
```

```ts
interface CreateStationRequest {
  catalog_id?: string        // 从公开电站目录复制：其余字段可省，给了则覆盖目录值
  name?: string              // 以下五项在不给 catalog_id 时必填（自建分支，小程序不暴露）
  type?: "solar" | "wind"
  latitude?: number
  longitude?: number
  capacity?: number          // kW
  coord?: "wgs84" | "gcj02"  // 入参坐标系，默认 wgs84

  // 出力模型参数，选填，不填用默认值（见 07 §2.3）
  tilt?: number              // 光伏倾角（°），默认 |latitude|
  azimuth?: number           // 光伏方位角（°），默认 180
  hub_height?: number        // 风机轮毂高度（m），默认按容量估算
  curtailment?: CurtailmentRule | null  // 出力约束，PATCH 传 null 清除、不传保持
  turbine_class?: "generic" | "low_wind" | "medium_wind" | "high_wind" | "custom" | null
  power_curve?: { v: number; p: number }[] | null   // custom 档必填：风速递增、出力 0–100%
  mounting?: "fixed" | "single_axis" | null
  bifacial?: boolean | null
}
```

`StationSummary` 原样回显这四个字段，目录电站一律 null。`turbine_class: "custom"` 而没有
`power_curve`、曲线少于 3 点或风速不递增返回 `400 INVALID_PARAM`。

服务端行为：

- 自建（不给 `catalog_id`）受每用户上限约束，超出返回 `400 STATION_LIMIT`
- 给了 `catalog_id`：名称取中文名、类型 / 坐标 / 容量 / 省市区取目录值；同一用户重复添加
  同一座返回已有站点（幂等，仍是 201）；目录里没有返回 `404 CATALOG_NOT_FOUND`
- 没给 `catalog_id` 且缺必填字段：`400 INVALID_PARAM`

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
DELETE /v1/me                204 No Content，删除本账号全部自建电站
```

删除电站会一并删除它的预警、发电记录、报告与预测留档；`DELETE /v1/me`（「我的 → 删除我的数据」）
对本账号全部自建电站执行同样的删除，不可恢复。表之间没有外键级联，由服务端逐表删除。


### 5.5 公开电站目录

```
GET /v1/stations/catalog?keyword={k}&near={lat,lng}&bbox={w,s,e,n}&type={t}&limit=20&coord=gcj02
```

全体用户共享、只读的场站库，来自公开数据集（[04 §七](./04-data-specification.md)）。
三种查法按 `keyword > near > bbox` 取其一：关键词匹配中文名 / 原名 / 省市区 / 业主，
按容量降序；`near` 按距离升序（200 km 粗筛）；`bbox` 给地图视野内的 marker，按容量降序，
`limit` 上限 200。`near` / `bbox` 入参按 `coord`。

```ts
interface CatalogSearchResponse {
  plants: CatalogPlant[]
  total: number                // 目录内该类型总数，供「共收录 N 座」文案
}

interface CatalogPlant {
  id: string                   // "{source}:{source_id}"，建站时回填到 catalog_id
  name: string                 // 有中文名用中文名，否则数据集原名
  name_en: string | null       // 数据集原名，与 name 相同时为 null
  type: "solar" | "wind"
  capacity: number             // kW
  latitude: number             // 已按 coord 转换
  longitude: number
  address: string | null       // 省市区，未回填为 null
  owner: string | null
  commissioning_year: number | null
  distance_km: number | null   // near 查询时有值
  source: "wri" | "gem"        // 展示出处用，数据授权要求注明
}
```

**已落地。** 从目录「添加」= `POST /v1/stations { catalog_id }`，服务端复制；用户之后只能改
装机容量与模型参数（PATCH）。目录本身不可写：脚本 `scripts/import_catalog.py` 手动导入，
定时任务 `sync_catalog` 按月从 GEM 重新拉取，这次没出现的条目标记 `retired` 并从搜索里隐藏。
`/v1/geo/search` 的结果里也合并了目录命中（`type: "plant"`），地图搜索框一处搜全部。


---

## 六、首页


```
GET /v1/home?station_id={id}&coord=gcj02
```

`station_id` 省略时用用户的默认站点；用户无站点时返回 `has_station: false`，
前端引导去创建。

```ts
interface HomeResponse {
  prediction: GenerationPrediction | null   // 今日；basis 带起报时刻，grid_* 为计入出力约束的口径
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

## 六-b、发电预测（2026-09-12）

```
GET /v1/predictions/station?station_id={id}&days=7&weather_model=best_match
GET /v1/predictions/fleet?weather_model=best_match&provinces=云南省,河北省
```

`provinces` 可选，逗号分隔的目录省份全称。给了就把整份响应按所选省汇总，包括覆盖统计的
**分母**；不给就是全目录。未知省份忽略，全部无效返回 400 `INVALID_PARAM`。
筛选只读快照的逐省明细做加法，不触发新一轮计算，也不写历史留档。口径见
[17 §二](./17-user-stations-and-outlook.md)。

```ts
interface ForecastBasis {
  model: string                     // 请求模型，best_match 即自动选择
  resolved_model: string | null     // 实际落到的模型（元数据 slug），未能确认为 null
  issued_at: string | null          // 模型起报，null = 无法确认，前端只显示 fetched_at
  available_at: string | null
  fetched_at: string
}

interface GenerationPrediction {
  model: string; date: string; timezone: string; generated_at: string
  energy_kwh: number | null         // 可发电量
  power_kw: PowerPoint[]
  grid_energy_kwh: number | null    // 计入出力约束后的上网电量；无规则一律 null
  curtailed_kwh: number | null
  grid_power_kw: PowerPoint[] | null
  province_grid: ProvinceGrid | null  // 限电第二层参考，仅公开电站；自建场站与无数据为 null（17 §四）
  basis: ForecastBasis | null
  resolution_minutes: number        // 15：96 点；60：24 点
  assumptions: string[]
}

interface ProvinceGrid {             // 按省级月度利用率折算，只作参考，不改可发电量与指数
  region: string                     // 统计区域，内蒙古分蒙西、蒙东
  period: string                     // 利用率统计期：YYYY-MM 当月值，YYYY 全年值
  utilization: number                // 0–1
  energy_kwh: number                 // 可发电量 × 利用率
  curtailed_kwh: number
  source: string
}

interface StationOutlook {           // 单站未来 7 天，首页懒加载
  station_id: string; model: string; timezone: string; generated_at: string
  basis: ForecastBasis | null
  days: DailyOutlook[]
  assumptions: string[]
}

interface DailyOutlook {
  date: string; weekday: number; lead_days: number     // lead_days ≥ 3 为中期预报
  energy_kwh: number | null; grid_energy_kwh: number | null; curtailed_kwh: number | null
  province_grid: ProvinceGrid | null
  index_score: number | null; index_level: IndexLevel | null   // 指数只反映气象，不受约束影响
  weather_text: string | null        // 日间众数天气
  peak_kw: number | null
  power_kw: PowerPoint[]; grid_power_kw: PowerPoint[] | null
  resolution_minutes: number         // lead_days 0–3 为 15（96 点），4–6 为 60（24 点），07 §2.7
}

interface FleetPrediction extends GenerationPrediction {
  // …原有覆盖统计字段不变，顶层仍是今日
  days: FleetDay[]                   // 未来 7 天，days[0] 与顶层今日字段一致
}

interface FleetDay {
  date: string; weekday: number; lead_days: number
  energy_kwh: number | null; solar_kwh: number; wind_kwh: number
  power_kw: PowerPoint[]; regions: RegionPrediction[]
  province_grid: FleetProvinceGrid | null   // 旧留档可能缺省
}

interface FleetProvinceGrid {        // 已覆盖电站逐站按所在省利用率折算后的合计
  energy_kwh: number                 // 无省级数据的电站按可发电量计入
  curtailed_kwh: number
  applied_count: number; unapplied_count: number
  periods: string[]; source: string
}

interface RegionPrediction {         // 按电站所在省汇总，只含该日已覆盖的电站
  province: string                   // 省份不详归入「地区待补充」，不作为筛选项
  energy_kwh: number
  covered_count: number
}
```

`days` 上限取 `forecast_outlook_days`（7）。两个接口都支持 `weather_model` 选择。
口径见 [17 §二](./17-user-stations-and-outlook.md)。


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
- `cloud` 图层**整层同源**：一屏内的块要么全是 Himawari 实况（图例标题「云量强度」），
  要么全是预报云量（图例标题「云量预报」）。只要有一块拿不到卫星就整层退回预报 ——
  卫星是亮度拉伸的相对强度、预报是云量百分比，两种量混在同一个响应里，
  一个图例解释不了，`observed_at` 也会一半是观测时刻一半是预报时刻


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

**已落地。** 服务端按站点太阳高度角选波段：白天 `visible`（展示图是真彩合成），
夜间 `infrared`（红外亮温拉伸后的灰度图）；`vapor` V1 不用。
站点周边 ±2.5°（按 0.5° 对齐，相邻站点共图）重投影为 512px 等经纬度 PNG，
`bounds` 与 `station_marker` 均已按 `coord` 转换，客户端按 bounds 线性定位标记即可。
上游拿不到返回 `502`。数据出处是日本气象厅，**客户端展示时必须注明来源**（JMA 利用规约）。

近三小时时间轴：`GET /v1/satellite/cloud/history` 返回可用帧时刻，`GET /v1/satellite/cloud?at=` 取单帧真彩图。
冷帧需拉瓦片并重投影，约 2 秒一帧，因此：

- 白天当前云图（含 `/v1/alerts/current` 内的场景）顺带把同一真彩帧写入历史帧缓存，时间轴最新帧直接命中。
- 请求时间轴后，服务端在后台由新到旧预渲染其余帧；全进程同一时刻只渲染一帧，同一批帧不重复排队，
  前台单帧请求不经过这个闸门。
- 客户端时间轴与当前预警并行加载，先停在最新一帧，再从最早一帧循环播放。


---

## 八、趋势


```
GET /v1/trends?station_id={id}&metric={m}&range={r}&day_offset={k}
```

| 参数 | 取值 |
| --- | --- |
| `metric` | `radiation` \| `wind_speed` \| `cloud_cover` \| `hub_wind_speed`（仅风电站，其余返回 `400 INVALID_PARAM`） |
| `range` | `24h` \| `7d` |
| `day_offset` | `0`–`6`，默认 `0`（今日）。只作用于 `24h`：取今日起第 k 天 00:00 至次日 00:00，与七天预测所选日期对应 |

```ts
interface TrendSeries {
  metric: "radiation" | "wind_speed" | "cloud_cover"
  unit: string
  range: "24h" | "7d"
  y_max: number | null         // 固定纵轴上限；null 表示自适应
  points: TrendPoint[]
  hub_height: number | null    // 换算所用轮毂高度 m；仅 hub_wind_speed 有值
}

interface TrendPoint {
  time: string
  value: number | null         // 缺测为 null，前端断线不补 0
}
```

`y_max`：辐射固定 1000，云量固定 100，风速为 `null` 自适应。
由服务端下发而非前端硬编码，便于调整。

**已落地。** `24h` 今日 00:00 至明日 00:00 逐小时 25 点；`7d` 今日 00:00 起 7 天逐 3 小时
56 点（粒度待产品确认，服务端 `trend_7d_step_hours` 可调）。


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
  satellite_status: "ok" | "unavailable"   // satellite 为 null 只有上游拿不到一种情况
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

**已落地。** 夜间有红外，云图全天可用；`satellite` 为 `null` 仅在上游拿不到时
（`satellite_status = "unavailable"`），此时已生效的卫星预警**不会被解除**，最多保留 2 小时
（外推时效上限）；拿到云图确认云已散才解除。`cloud_motion` 的 `direction` 是云团**去向**，
`direction_detail` 说明来向与速度，如「自东北方向移来，约 32 km/h」。


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
  type: "city" | "poi" | "station" | "coordinate" | "plant"
  // station 为用户自己的站点；coordinate 为直接解析的经纬度输入；
  // plant 为公开电站目录命中，catalog_id 有值，可一键添加为自己的站点
  catalog_id: string | null
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

**已落地。** 未配置腾讯 key 时降级：`/reverse` 返回 503，`/search` 只匹配站点与坐标。
腾讯用 GCJ-02，服务端在调用前后转换，客户端无感知。建站时自动逆地理填 `address`，
失败不阻塞，定时任务每小时补。


---

## 十二、公共类型


```ts
type LayerType = "cloud" | "wind" | "temperature" | "radiation"

interface EnergyIndex {
  score: number | null                 // 0-100，不可算时为 null
  level: "excellent" | "good" | "fair" | "poor" | null
  summary: string | null               // AI 一句话结论
  estimated: boolean                   // true 表示部分输入由插值 / 昨日回填 / 降级得到（07 §六）
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
| 401 | `LOGIN_REQUIRED` | 游客访问自建电站，需先登录 |
| 403 | `STATION_FORBIDDEN` | 站点不属于当前用户 |
| 404 | `STATION_NOT_FOUND` | 站点不存在 |
| 404 | `LAYER_NOT_FOUND` | 图层类型不支持 |
| 404 | `CATALOG_NOT_FOUND` | 公开电站目录里没有该 id |
| 202 | `REPORT_GENERATING` | 报告生成中，稍后重试 |
| 429 | `RATE_LIMITED` | 请求过于频繁。已落地：每 token / IP 每分钟 120 次，响应带 `Retry-After` |
| 502 | `UPSTREAM_UNAVAILABLE` | 上游数据源不可用 |
| 503 | `UPSTREAM_RATE_LIMITED` | 气象源配额耗尽或处于冷却期，不自动重试 |
| 503 | `DATA_UNAVAILABLE` | 该区域暂无数据 |

`502` 与 `503` 的区别：`502` 是上游故障（可重试），
`503` 不自动重试；`DATA_UNAVAILABLE` 表示该位置无数据，`UPSTREAM_RATE_LIMITED`
明确展示气象配额错误。冷却期间立即返回，不让业务请求排队等待额度恢复。


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
| 401 自动重登 | 重登后重试一次，失败才抛错；并发请求共用一次续登；游客不主动登录，能否续登由各端 relogin 决定 |
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
| 1 | ~~站点是否支持多用户共享~~ 公开目录共享、自建站点按账号隔离（17 §一） | — |
| 2 | 预警是否需要订阅推送（微信订阅消息） | 需增加订阅管理接口 |
| 3 | ~~「我的」页面需求未定~~ 已实现，不需要用户配置类接口（登录静默，不采集头像昵称） | — |
| 4 | 7 天趋势的采样粒度（逐日还是逐 3 小时） | 影响 `TrendSeries` 的点数 |

图层补充可选 `source`、`model`、`resolution_km`、`run_at` 元数据。HRES 图层的
`observed_at` 表示预报有效时刻（沿用兼容字段），并非实测时间；客户端显示「预报」。


### 地图栅格模块补充（2026-09-10）

温度、风场、辐射沿用 `/v1/map/layers/{layer}`，但图片为固定 XYZ 瓦片而非任意视野大图。
`coverage` 为可选覆盖范围/等值线说明；图片 URL 包含起报批次、有效时效、图层、坐标系、z/x/y，
`bounds` 是对应坐标系的瓦片边界。GCJ 像素已经重采样，客户端不得二次平移边界。

HRES 支持全国 bbox，一屏最多 12 张图；`zoom` 保留兼容，实际层级按 bbox 的覆盖成本选择。
空间范围 60–150°E、0–65°N，部分超出时覆盖外透明；完全超出返回 400 `MAP_OUTSIDE_COVERAGE`。
云图仍采用原链路，超过 24° 返回 400，不随本次 HRES 改造改变卫星范围。
当前 COG 未就绪且无两小时内旧成果时返回 503 `MAP_PREPARING`；旧有效时刻须 `stale=true`，
`observed_at` 必须显示成果真实有效时刻，不改写为请求时间。没有在线下载原始场的隐式兜底。
