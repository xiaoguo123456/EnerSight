# 三模式区间、预报演变、实测订正、卫星辐照与积灰（2026-09-18）

五项功能的共同主题：**把不确定性量化出来，把实况与实测接进来。**

背景是 [07 §八](./07-metrics.md) 的校准结论：光伏转换链路几乎无偏（实测辐照驱动 0.92–1.03），
单站误差主要来自气象输入（±10–20%）、风电机型猜档（±25%）与未计入的限电。单站绝对值一时修不准，
但可以诚实地告诉用户「这个数准到什么程度」，并让实测把它逐站修准。这五项就是这件事的五个面。

本文定口径与页面落点。01 / 04 / 06 / 07 / 08 / 09 的对应章节在各里程碑落地时同批更新，
代码不先于文档。2026-09-18 的现状摸底与数据源核实见文末附录。

范围上明确不做的：51 成员集合与任何分位数（P10 / P90）、15 分钟实测文件上传、两个细则考核。
三模式并列展示、记电量就能订正、卫星辐照直接用现成产品，够用了。


## 零、总览

| # | 功能 | 一句话 | 落点 | 数据来源 | 里程碑 |
| --- | --- | --- | --- | --- | --- |
| 1 | 三模式区间（**默认模式**） | ECMWF / ICON / GFS 三模式同算，三个数并列，曲线画区间带 | 首页预测卡 | 现有四模式 | M1 |
| 2 | 预报演变与收敛度 | 同一目标日历次起报的变化，收敛即高置信 | 首页预测卡 | 预测留档 + 定时签发 | M1 |
| 3 | 实测随手记与订正 | 记日电量或月电量，几个数就拟出订正系数 | 首页「记一笔」、电站详情 → 实测对账页 | 用户输入 | M2 |
| 4 | 卫星辐照实况 | 葵花 5 km 卫星辐照产品，实况替代预报 | 预警页「卫星实况」卡 → 当前功率与当日累计 | Open-Meteo 卫星辐射接口（JAXA 产品） | M3 |
| 5 | 气溶胶归因与积灰损失 | CAMS 的 AOD / PM 进指数归因与积灰模型，给清洗建议 | 电站详情、指数归因 | CAMS（公开桶有）+ ECMWF 降水字段 | M4 |

四条硬规矩，五项通用：

- **缺失一律 null。** 区间、收敛度、偏差、积灰、卫星辐照拿不到就是 null，界面显示「暂无」；不用 0，不省略字段。
- **不确定性只描述，不评价。** 「模式一致 / 分歧」「收敛 / 摇摆」说的是预报有多稳，不是天气好坏，不套优良中差的配色。
- **带叫「模式区间」，不叫 P10 / P90。** 三个成员算不出分位数，也不做分位数；界面只说最低、最高、中位。
- **不宣称准确率。** 有实测的电站只报「偏差」（模型比实测高或低多少），不报准确率、合格率。
  AI 只接收档位文案与最终数字（[08 §5.1](./08-ai-design.md)），历次起报值、成员逐点值不进输入，避免污染数值一致性校验集。


## 一、三模式区间（默认模式）

### 口径

- **成员**：`ecmwf_ifs`、`icon_global`、`gfs_global`。每个成员各走现有链路
  （`metrics/pv.hourly_power`、风电功率曲线）算出 7 天逐 15 分钟曲线与日电量，不另写一份。
  可配第四个成员 `cma_grapes_global`（中国气象局 GRAPES 全球模式，公开桶里有，3 小时步长，
  带 30–200 m 各层风与辐射）；默认关，与前三家比对一轮后再决定是否加入。
- **主展示取「中位成员」，逐日独立选。** 先算出三家各自的日电量，取居中那家的**完整结果**
  作为这一天的主数字、曲线、指数、天气与峰值。不做逐点中位数合成曲线 —— 那条曲线的求和
  不等于任何一家的日电量，主数字与曲线会对不上；而且逐点取中位会把不同成员的时序混在一起，
  峰谷被削平，不是任何一个模式的预报。中位成员可能今天是 ECMWF、明天是 GFS，这是正常的。
- **日电量区间**：`energy_kwh_low / high` 取三家日电量的最小 / 最大，三家各自的值都放在
  `member_energy_kwh` 里，界面并列展示。
- **功率带**：`power_kw_low / high` 按时刻取三家的最小 / 最大，**只作视觉包络，不参与任何求和**。
  某时刻少于 2 家有值时该点为 null，按 null 分段绘制。
- **分歧度**：`spread = (high − low) / median`（日电量）。`ensemble_spread_moderate`（15%）以下「模式一致」，
  `ensemble_spread_high`（40%）以下「模式分歧」，以上「严重分歧」。median 为 null 或 0 → 档位 null。
- **默认即三模式。** 模型选择器顺序：三模式（默认）/ ECMWF IFS · 自动 / ICON / GFS；本机记住。
  选三模式时主数字与曲线都是中位数，三家的数并列在下面；选单一模式时没有区间。

### 各链路的一致性

默认改成三模式后，首页数字是中位数，别的链路要跟着定口径，否则「今日发电」和「累计发电」会对不上：

**只有 `/v1/predictions/station` 走三模式。** 首页预测卡的主数字与曲线本来就优先取这个接口的
`days[selected]`，`/v1/home` 的 `prediction` 只是它加载完成前的占位，所以改这一个接口就够了，
首屏不会因为等三家而变慢。

| 链路 | 用什么 | 理由 |
| --- | --- | --- |
| `/v1/predictions/station`（首页预测卡、7 天、CSV 导出） | 三模式，主数字取中位成员 | 用户看到的主数字 |
| `/v1/home` 的今日 `prediction` | `best_match` | 只是 outlook 加载前的占位，不该让首屏等三家 |
| 电站详情、趋势、地图 | `best_match` | 单模型足够，可手动切模型 |
| 发电累积、全目录汇总 | `best_match` | 每小时一轮，三倍出网换不来相应的准确度 |
| 预警扫描 | `best_match` | 规则看瞬时量，多模式会让预警抖动 |
| AI 报告输入 | `best_match` | 与报告里引用的当日数字同源 |
| 指数 | 中位成员的指数，不给区间 | 指数只有一个分数 |

首页大数字（中位成员）与累计发电的当日增量（`best_match`）会差一点，量级在几个百分点，
ⓘ 里说明「累计按自动模型逐小时累积」。`weather_model.supports_selection` 白名单不放开；
全目录视图（含历史趋势页）的选择器里不出现「三模式」，已选的显示服务端实际用的「自动 · ECMWF」，
不改写用户的选择，切回本站仍是三模式。

### 成本

- 单点预报缓存本来就按模型隔离（`15m:{model}:{lat},{lon}`），三个成员各自受「每坐标每模型每天 4 次」约束，
  见 [04 §二](./04-data-specification.md)。自建实例在线时不按坐标计费；退回托管接口时相当于三倍坐标额度。
- 默认三模式意味着每次首页加载都算三成员。三成员的气象拉取与预警扫描共用缓存，实际额外出网只在缓存过期的时段。

### 实现

- `services/ensemble.py`：对每个成员用 `contextvars.copy_context()` 设 `current_model` 后并发跑
  `prediction.compute_days`（并发 3），再做上面的聚合；响应头 `X-Weather-Model` 回写 `ensemble`。
- `weather_model.MODELS` 加 `ensemble`，并成为 `/v1/predictions/station`、`/v1/home`、`/v1/stations/{id}/detail` 的默认；
  其余路径照旧强制 `best_match`。
- 留档：每个成员各存一份 outlook（现有 `save_outlook`），聚合结果另存一份 `kind: ensemble`。
- 某成员拉取失败：用剩余成员计算并在 `assumptions` 说明；少于 2 个成员 → `ensemble` 为 null，主数字退回自动模式。

### 接口（06 §六-b 追加）

```ts
GET /v1/predictions/station?station_id=&days=7          // 默认即 weather_model=ensemble

interface StationOutlook {
  // …现有字段
  ensemble: EnsembleSummary | null          // 单一模式请求为 null
}
interface EnsembleSummary {
  members: ForecastBasis[]                  // 参与的成员及各自起报
  spread_level: 'agree' | 'diverge' | 'strong' | null   // 按 days[0]
}
interface DailyOutlook {
  // …现有字段；三模式下 energy_kwh / power_kw / index_score 等整套取自中位成员
  energy_kwh_low: number | null
  energy_kwh_high: number | null
  member_energy_kwh: MemberEnergy[] | null  // 三家各自的日电量，按 members 顺序
  median_model: string | null               // 这一天主数字取自哪个成员
  power_kw_low: PowerPoint[] | null
  power_kw_high: PowerPoint[] | null
  spread_percent: number | null
  spread_level: 'agree' | 'diverge' | 'strong' | null
}
interface MemberEnergy { model: string; energy_kwh: number | null }
```

`basis` 取 ECMWF 成员。

### 页面

- 首页本站预测卡（`StationForecast`）：模型选择器默认「三模式」。大数字是中位成员的值，
  下面两行由 `ForecastSpread` 组件给出，格式统一为「文字 + 灰标签」：
  「区间 51–105 MWh」+「分歧很大」，下一行「ECMWF 105 · ICON 51 · GFS 91」，居中那家加粗。
- `TrendChart.ChartData` 加 `band?: { low: (number | null)[]; high: (number | null)[] }`：主色 0.12 透明度填充，
  按 null 分段，垫在最底层，纵轴把带的上沿算进去。**有带时主曲线不画面积渐变** —— 两层浅蓝叠在一起，
  带的下沿就看不出来了（截图验证时发现）。现有 `comparison`（可发 vs 上网）与带可同时出现。
- ⓘ：`assumptions` 里给成员说明、区间不是概率区间、累计按自动模型累积。
- 客户端防串味：首页、全目录、历史页按「响应模型 == 当前选择」丢弃旧响应，选了三模式要跟
  `best_match` 比（`store/weatherModel.servedModel`），否则这几处响应永远对不上、页面停在加载。

### 验收

- 三模式各自与单选该模式的结果逐点一致（回归测试）。
- 日电量 `low ≤ energy_kwh ≤ high`；`energy_kwh`、`low`、`high` 各等于某个成员的日电量，
  且 `energy_kwh` 与 `power_kw` 求和自洽（同一成员）。
- 成员缺一时结果与说明正确；缺两时 `ensemble` 为 null。
- 首页今日、累积当日增量、报告引用的数字三者一致（同一签发）。
- 模拟器里区间带、选中态气泡、双线并存三种状态各截一张图。


## 二、预报演变与收敛度

气象模式每 6 小时重新起报，每轮都报未来 7 天。对同一个目标日，从前 7 天到当天会陆续拿到二十多份预报，
现在只展示最新一份。把历次预报按签发时间排成一排，就是预报演变；最近几轮不再变，就是收敛。
这是预报员看天气的方式，不需要实测就能给用户一个「该信几分」。

### 口径

- **对象**：目标日 D 的日电量与峰值，按 `basis.issued_at` 去重后的历次预测，时效窗口 D−7 … D。
- **固定单一模型**（`evolution_model`，默认 `ecmwf_ifs`）。演变要回答「同一个模型改主意了没有」，
  混着不同模型比，看到的是模型间差异而不是预报演变。中位成员逐日会变，更不能用来串时间序列。
- **去重**：同一 `issued_at` 只留最新生成的一份；`issued_at` 为 null 的留档按 `fetched_at` 所在的 6 小时片归并，
  并标「按拉取时间」（[17 §二](./17-user-stations-and-outlook.md)：起报拿不到不猜）。
- **收敛度**：取最近 `evolution_issuances`（3）份不同起报，`range = (max − min) / mean`。
  小于 `evolution_stable_pct`（10%）「收敛」，小于 `evolution_swing_pct`（25%）「波动」，否则「摇摆」。
  不足 2 份 → null，界面「暂无演变」。
- **签发保障**：现在的留档只在用户请求时写（`services/home.py`、`routers/home.py`），没人打开的电站没有历史。
  新增定时任务 `issue_outlooks`，北京时间每日 08:30（全目录轮次之后、单点缓存新时段内）对全部我的电站
  算三模式并留档（三个成员各一份）；`db.pages` 分批，算并发写串行。公开目录电站不定时签发，
  有多少显示多少。这份签发同时是第三节订正拟合用的模型侧数据。
- **留档索引**：完整留档带着整份 15 分钟气象输入，扫一遍太重。`save_outlook` 同时写一份
  `{digest}.meta.json`，只含站点、模型、签发与起报时刻、每天的电量与峰值；演变只读索引。
  索引首键仍是 `station_id`，删站时按文件头一并清掉。旧留档没有索引，演变从本版上线起攒。
- **留档清理**：`prediction-archive` / `prediction-outlook` 目前不清理。新增 `prediction_archive_retention_days`（45）
  按日目录清理；删除电站时 `purge_station_archives` 照旧。

### 接口

```ts
GET /v1/predictions/station/history?station_id=&date=YYYY-MM-DD

interface ForecastEvolution {
  station_id: string; date: string
  issuances: Issuance[]                     // 按 issued_at 升序
  convergence: 'stable' | 'wobble' | 'swing' | null
  range_percent: number | null
}
interface Issuance {
  issued_at: string | null; generated_at: string
  lead_days: number; model: string
  energy_kwh: number | null; peak_kw: number | null
}
```

只读留档，不触发计算，不写留档。

### 页面

- 首页本站预测卡，区间两行之下：「ECMWF 演变 12.1 → 11.8 → 11.9 MWh」+「已收敛」标签 + 「详情」。
  **写明是哪家**：演变固定看 ECMWF，最新值可能与中位成员的大数字不同，不写明会被当成对不上。
  点「详情」就地展开历次起报列表（起报时刻 + 电量）与两段说明。不用小折线：`TrendChart` 的横轴刻度
  按固定步长算，2–8 个不等距的起报点画出来是错的。今天以外的日子同样可看，跟着选中日走。
- 不足两份起报整行不出现；游客看公开电站也能看，有多少留档显示多少。
- AI 报告输入加一行「预报稳定度：…」，只有档位文字；规则模板在「来回摇摆」时把「临近时段以最新预报为准」
  排在建议第一条，建议总数仍不超过三条（`AIReport` 校验上限，有预警时本来就满了）。

### 验收

- 连续三天签发后，同一目标日能还原出三份不同 `issued_at` 的记录。
- 时效窗口外的留档不出现；同一小时内重复请求只留一份。
- 删除电站后历史接口返回 404，留档文件已清。


## 三、实测随手记与订正

订正需要的信息其实很少：模型整体偏高还是偏低、偏多少。一个乘性系数就能吃掉机型猜档、容量口径、
系统损耗这几项最大的系统性偏差，而系数只要几个电量数就拟得出来。不做文件上传，不做逐点序列。

### 两种输入

| 档 | 输入 | 来源 | 最少数据 | 能得到 |
| --- | --- | --- | --- | --- |
| A 月电量 | 每月一个数（kWh） | 结算单、电费单 | 2 个自然月 | 总体系数 `k` |
| B 日电量随手记 | 每天一个数（kWh），可一次补录最近 31 天 | 逆变器 App、电表、值班日志 | 7 个有效日 | 总体系数 `k`；30 天后按月 `k` |

- 数是**上网电量**还是**发电量**由用户选，默认「发电量」；选上网电量时 ⓘ 注明系数会把限电与线损一起吃进去。
- 「此刻功率」也可以记，只用于当前功率对照展示，不进拟合（单点噪声大）。
- 只对我的电站，需登录（`CurrentUserDep`）。实测是用户经营数据：不进全目录、不对其他账号可见、
  随删除电站或「删除我的数据」一并删除。
- 校验：非负、不超过装机容量 × 24 小时 × 1.1（日）或 × 当月小时数 × 1.1（月），重复期间覆盖。

### 存储

- `measured_energy(station_id, period_start, period_end, kwh, basis: 'generation' | 'grid', source: 'monthly' | 'daily', recorded_at)`，
  唯一 `(station_id, period_start, period_end)`。
- 日粒度同步到 `daily_generation`，`source = measured`，累计发电与减排从此优先取实测（模型注释早已预留）。

### 订正

- **公式**：`k = Σ实测 / Σ模型`，同期模型值取签发留档；重叠不足门槛时不拟合。光伏、风电同一公式。
  `P_corr = clip(k × P_model, 0, Cap)`。只做乘性订正，不做加性（夜间会出负数或凭空出力）。
- **回测**：B 档按时间顺序末 20% 的天留作回测；A 档样本太少不回测，只报「订正前后月偏差」。
- **应用条件**：B 档回测日电量偏差绝对值下降 ≥ `correction_min_gain_pct`（3 个百分点）才启用；
  A 档两个月的 `k` 相差 < 15% 才启用。否则保存拟合结果但不应用，界面说明原因。
- **作用范围**：该站的 7 天预测、首页今日、当前功率、累积表的 forecast 行。**不改指数**（指数只反映气象），不改全目录。
  有实测的日子累计直接用实测。
- **与出力约束的关系**：设了出力约束的电站，拟合时模型侧用「预计上网」而不是「可发」，避免把限电学进系数。
- **重拟合**：每日凌晨与每次记录后；`station_correction(station_id, fitted_at, sample_days, k, bias_before, bias_after, applied)`。
- 预测响应 `assumptions` 加「已按 N 天实测订正（日电量偏差 +18% → +4%）」；`GenerationPrediction` / `DailyOutlook` 加 `corrected: boolean`。

### 接口

```
POST   /v1/stations/{id}/measured        body { entries: [{ period_start, period_end, kwh }], basis }
GET    /v1/stations/{id}/measured        → { entries: MeasuredEntry[], correction: StationCorrection | null }
DELETE /v1/stations/{id}/measured?from=&to=
PATCH  /v1/stations/{id}                 增加 correction_enabled: boolean
```

全部 `CurrentUserDep`，归属校验同现有自建电站接口。

### 页面

- **首页预测卡（我的电站）**：大数字旁一个小按钮「记一笔」，弹层：日期（默认昨天）、电量 kWh、发电量 / 上网电量两段开关，
  一步提交。记录成功后卡片显示「昨日实测 11.2 MWh · 预测 12.1」。有订正时大数字旁徽章「实测订正」，ⓘ 说明来源。
- **电站详情 → 实测对账页**（`pages/station/measured`，`PageHeader`「实测对账」）：
  Hero 是订正状态卡（系数、样本天数、偏差前后、开关）；下面是记录列表（月 / 日），可补录、删除。
- CSV 导出增加 `measured_kwh` / `corrected_kw` 列；电站列表卡「实时功率」当日有实测时显示实测并标「实测」。

### 合规（09）

- 隐私协议与 mp 后台数据收集清单增加「电站实测发电数据（用户主动填写）」：用途为对账与订正，
  仅本账号可见，随电站删除。
- 不接逆变器 / SCADA 接口（V2）。


## 四、卫星辐照实况

### 数据源

用 Open-Meteo 卫星辐射接口的 `jma_jaxa_himawari`：JAXA 用葵花 9 号反演的地表短波辐射产品，Open-Meteo 再分出直射与散射。
亚洲全境、0.05°（5 km）、上游 10 分钟、延迟约 30 分钟、存档回溯到 2015。2026-09-18 拿敦煌前一日实测，
逐小时返回 GHI / 直射 / 散射，峰值 802 W/m²，量级正常。托管接口 `satellite-api.open-meteo.com`，
Professional 档起；不在公开桶里，自建实例服务不了。

不再用现有 JMA 网页瓦片做云指数反演：0–255 显示灰度、JPEG 有损、调色板未知，反演出来说不清。
NOAA 公开桶里的葵花 L1b（1 km 波段中国段一帧约 40 MB）留作 V2 连云图一起换源的路，与本节无关。

### 授权

- JAXA P-Tree 条款：JAXA 产品版权归 JAXA；JMA 标准数据限非营利；不得向第三方再分发。我们不直接用 P-Tree，
  经 Open-Meteo 商用订阅取得，商用授权链条要向 Open-Meteo 书面确认，与 JMA 云图授权一并交法务
  （[09 §九](./09-miniapp-compliance.md)）。
- 署名：读数旁「JAXA / 日本气象厅 · 经 Open-Meteo」。

### 口径

- **拉取**：`providers/satellite_radiation.py`，走 `weather_transport.weather_get`；变量 `shortwave_radiation`、
  `direct_radiation`、`diffuse_radiation`，取过去 3 小时到当前。我的电站每 30 分钟一次（`satellite_irradiance` 任务），
  公开目录电站按需拉并缓存 30 分钟。每次 1 个坐标、3 个变量，计 1 次。
- **用途**：
  1. 预警页「卫星实况」卡：辐照大数字 + 「晴空的 78%」（分母 `metrics/solar.clearsky_interval_mean`）+ 观测时间；
  2. 当前功率：用卫星的 GHI / 直射 / 散射直接算 POA，走 `pv.hourly_power` 只替换当前区间，标「卫星实况」；
  3. 当日累计「卫星订正」：今天已过去的区间用卫星辐照重算，累积表 forecast 行更新，`source = satellite`；
  4. 指数不改（指数是全天预报口径）；地图辐射图层「实况」变体留 V2。
- **降级**：接口不可达、夜间、延迟超过 `satellite_irradiance_stale_minutes`（90） → `irradiance` 为 null 并给 `status`，
  当前功率退回预报值。卫星辐照与预报辐照差超过 60% 时仍用卫星值，但在 ⓘ 提示「与预报差异大」。
- **风电不适用。**

### 校准（不用等归档）

存档回溯到 2015，直接与 `enersight-validation-data` 里 PVOD 河北 10 座光伏站 2018–2019 的实测总辐照对账：
逐小时 RMSE ≤ 25%（相对日间均值）、偏差在 ±10% 内视为达标；同时对比 ERA5 驱动的误差
（[07 §8.1](./07-metrics.md) 2026-09-16 一节）看卫星比再分析好多少。结论写入 07 §8.1，达标前只做用途 1。

### 接口

```ts
interface SatelliteCloudResponse {
  // …现有字段
  irradiance: SatelliteIrradiance | null
}
interface SatelliteIrradiance {
  ghi_w_m2: number | null
  direct_w_m2: number | null
  diffuse_w_m2: number | null
  clear_sky_ghi_w_m2: number | null
  clear_sky_index: number | null
  observed_at: string
  status: 'ok' | 'night' | 'stale' | 'unavailable'
  source: string                            // 署名文案
}
// StationDetailResponse / HomeResponse 的当前功率增加
current_power_source: 'forecast' | 'satellite' | 'measured'
```

### 页面：预警页

- **不改 tab 名。** 底部导航按用户任务命名（[02](./02-design-system.md) 术语条），预警是订阅推送与首页
  「今日预警」的落点；预报规则类预警（强风、暴雨、高温）不是卫星来源。改名只涉及 tabBar 文案与
  `PageTitleBar`，列入待确认，随时可改。
- **页面结构**：无预警时 Hero 改为「卫星实况」卡（此前只有一句「当前未触发预警」，页面大部分时间是空的）；
  有预警时预警卡仍是 Hero，卫星实况卡在其下。一页仍只有一个 Hero。
- **「卫星实况」卡**：辐照大数字 + 「晴空的 78%」+ 观测时间；下方 `CloudMotionStats` 三宫格保持；再下 `SatelliteTimeline` 不变。
- 电站详情「当前气象」卡底部加一行「卫星辐照 612 W/m² · 14:20」。首页四宫格的辐射项不换来源，环比口径不动。


## 五、气溶胶归因与积灰损失

### 数据来源

- **气溶胶与颗粒物：CAMS。** 欧洲中期天气预报中心运营的哥白尼大气监测服务，全球 0.4°、逐小时、5 天预报、每天两次。
  两条拿法：托管的 Open-Meteo 空气质量接口（`air-quality-api.open-meteo.com`，变量 `aerosol_optical_depth`、`dust`、
  `pm10`、`pm2_5`）；或自建实例，公开桶 `cams_global/` 里这四个变量都有（2026-09-18 核实），
  能否由自建实例直接服务需实测一次。备选与校验：风云 FY-4B AGRI 气溶胶产品（境内）、MODIS / VIIRS 日 AOD（有云即缺）。
- **降雨：不需要新数据源。** ECMWF 的 `precipitation` 在公开桶 `ecmwf_ifs/precipitation/` 里，自建实例免费；
  托管接口下加进 `HOURLY_FIELDS` 做第 16 个字段，权重 1.5 → 1.6，按每坐标每天 4 次算每站每天多 0.27 次，
  比单独发一次请求（1.0 次）便宜。消费方是积灰模型，[04 §二](./04-data-specification.md) 字段表同步；全目录字段清单不加。
  想要实况雨量再考虑 NASA GPM IMERG（0.1°、30 分钟、需账号），V1 不做。
- 每站每天拉一次空气质量并带 `past_days=1`；新表 `station_environment_daily(station_id, day, pm10, pm2_5, aod, dust, rain_mm, soiling_ratio)`，
  首次用 `past_days=92` 回补。

### 气溶胶归因

- **指数定义与理想值不变**：理想仍按 pvlib 的 Linke 浊度气候值算晴空。只在归因里加一项 `aerosol`：

  ```
  Δ_aerosol = 指数(理想按当日 AOD 浊度) − 指数(理想按气候浊度)
  ```

  通常为负，文案「气溶胶 · 沙尘使晴空辐照降至气候值的 88%」。
  这样敦煌晴天 77 分的成因看得见（[07 §8.1](./07-metrics.md)），但不改指数的分布与校准。
- 当日 AOD → 浊度：`atmosphere.angstrom_aod_at_lambda` 换到 380 / 500 nm → `bird_hulstrom80_aod_bb` → `kasten96_lt`；
  可降水量由 `gueymard94_pw` 从气温与湿度估。第四节的晴空分母可用时优先用这个浊度。
- AOD 缺失 → 该项不出现（不是 0）。`IndexAttributionFactor` 加 `AEROSOL`；风电指数仍无归因。

### 积灰

- `pvlib.soiling.hsu(rainfall, cleaning_threshold, surface_tilt, pm2_5, pm10)` 逐小时，从上次清洗日起算；
  `cleaning_threshold` 默认 0.5 mm/h（`soiling_cleaning_threshold_mm`），沉积速度用 pvlib 默认，
  分区校准留待有实测站点对账。
- 电站字段：`last_cleaned_on: date | null`。我的电站表单「出力模型参数」内增加，并有「今天已清洗」快捷键；
  公开目录电站与未填的按「只靠降雨」计算。
- 输出：

  ```ts
  interface SoilingStatus {
    loss_percent: number | null; since: string | null
    rain_next_5d_mm: number | null
    advice: 'ok' | 'rain_expected' | 'wash' | null
  }
  ```

  损失 ≥ `soiling_wash_loss_pct`（5%）且未来 5 天累计降雨低于阈值 → `wash`；有降雨 → `rain_expected`。
- **第一阶段只展示不折减**：不改可发电量、指数与预测；有实测站点对账后再作为出力约束的一种进入上网电量。
  风电不适用。

### 页面

- 电站详情（光伏）新卡「积灰与清洗」：损失 % 大数字、自上次清洗天数、建议文案、参考损失电量与电费
  （`tariff_yuan_per_kwh`，ⓘ 标参考）；我的电站有「已清洗」按钮。
- 指数归因弹窗多一行「气溶胶」；AI 报告输入加「积灰损失 3.2%（估算）」。


## 六、任务、配置与数据变更汇总

### 定时任务（`app/jobs/scheduler.py`）

| 任务 | 周期（UTC） | 做什么 |
| --- | --- | --- |
| `issue_outlooks` | 每日 00:30 | 我的电站三模式 7 天签发留档 |
| `prune_prediction` | 每日 21:40 | 按 `prediction_archive_retention_days` 清理 |
| `fit_corrections` | 每日 02:00，记录后立即 | 订正拟合与回测 |
| `satellite_irradiance` | 每 30 分钟 | 我的电站卫星辐照拉取，更新当前功率与当日累计 |
| `soiling_update` | 每日 01:00 | 拉空气质量与降水，更新积灰 |

都用 `db.pages` 分批、算并发写串行，见 CLAUDE.md「在遍历站点的定时任务里并发写 DB」。

### 配置（`config.py`，环境变量前缀 `ENERSIGHT_`）

| 项 | 默认 | 用途 |
| --- | --- | --- |
| `ensemble_members` | `ecmwf_ifs,icon_global,gfs_global` | 三模式成员；可加 `cma_grapes_global` |
| `ensemble_spread_moderate` / `ensemble_spread_high` | 15 / 40 | 分歧度分档（%） |
| `evolution_issuances` | 3 | 收敛度看最近几份起报 |
| `evolution_stable_pct` / `evolution_swing_pct` | 10 / 25 | 收敛度分档（%） |
| `prediction_archive_retention_days` | 45 | 留档保留 |
| `correction_min_days` / `correction_min_months` | 7 / 2 | B / A 档拟合门槛 |
| `correction_holdout_fraction` / `correction_min_gain_pct` | 0.2 / 3 | 回测比例与启用门槛 |
| `satellite_radiation_base` / `satellite_radiation_model` | `https://satellite-api.open-meteo.com/v1` / `jma_jaxa_himawari` | 卫星辐照 |
| `satellite_irradiance_stale_minutes` | 90 | 超过即退回预报 |
| `air_quality_base` | `https://air-quality-api.open-meteo.com/v1` | 空气质量接口 |
| `soiling_cleaning_threshold_mm` / `soiling_wash_loss_pct` | 0.5 / 5 | 积灰模型与清洗建议 |

### 数据表（alembic 迁移）

| 表 / 字段 | 说明 |
| --- | --- |
| `measured_energy` | 月 / 日电量记录，唯一 `(station_id, period_start, period_end)` |
| `station_correction` | 订正拟合结果与回测 |
| `station_environment_daily` | 逐日 PM / AOD / 降雨 / 积灰比 |
| `station.last_cleaned_on`、`station.correction_enabled` | 电站新增字段 |

删除电站与「删除我的数据」要一并清这三张表（[09 §4.3](./09-miniapp-compliance.md)）。


## 七、里程碑与验收门

| 里程碑 | 内容 | 进入下一步的门槛 |
| --- | --- | --- |
| M1 ✅ | 三模式默认、区间带与三家并列、预报演变、`issue_outlooks`、留档清理、各链路口径统一 | 第一、二节验收项全过；H5 截图已看，开发者工具截图待合入主仓库后补 |
| M2 | 随手记、订正、实测对账页 | 用 PVOD 站点的日电量走一遍记录 → 拟合 → 应用，偏差下降可复现 |
| M3 | 卫星辐照：接口接入、PVOD 回测、预警页卫星实况卡；达标后当前功率与当日累计切换 | 需 Open-Meteo Professional 订阅与授权确认；回测结论写入 07 §8.1 |
| M4 | 空气质量接入、气溶胶归因、积灰卡、`precipitation` 字段 | 敦煌 / 拉萨 / 苏州三点归因数值合理；04 §二字段表已更新 |

M1 与 M2 共用签发任务，先后做；M3 的回测不依赖归档，订阅到位即可开工；M4 独立可并行。


## 八、各文档同步清单

| 文档 | 章节 | 内容 |
| --- | --- | --- |
| 01 | §三首页、§五预警、§六电站 | 预测卡区间与三家并列、演变、「记一笔」、预警页卫星实况卡、实测对账页与积灰卡 |
| 03 | 组件表 | `TrendChart` 的 `band`、`SatelliteIrradianceCard`、`QuickRecordSheet`、`CorrectionCard`、`SoilingCard` |
| 04 | §二字段表、新增卫星辐照与空气质量两节 | `precipitation`、CAMS 字段、JAXA 产品说明与署名 |
| 06 | §五电站、§六-b、§九预警 | 本文全部接口 |
| 07 | §1.6 归因、§2.1 当前功率来源、新增 §九实测订正 | 气溶胶因子、卫星辐照驱动、乘性订正 |
| 08 | §5.1 | 稳定度、订正、积灰三行输入 |
| 09 | §四隐私、§九授权 | 实测数据条目；JAXA 授权链条 |
| 10 | 环境变量 | 第六节配置项；Open-Meteo 订阅档位 |


## 九、待确认

| # | 事项 | 影响 |
| --- | --- | --- |
| 1 | 预警 tab 是否改名「卫星」 | 只改文案，随时可做；本文按不改写 |
| 2 | Open-Meteo 订阅升到 Professional | 卫星辐照接口的前提；不升则 M3 走不通 |
| 3 | JAXA 产品经 Open-Meteo 的商用授权 | 与 JMA 云图授权一并交法务 |
| 4 | 积灰是否进入上网电量折减 | 本文第一阶段只展示 |
| 5 | 自建实例能否服务 `cams_global` | 决定 M4 走自建还是托管 |


## 附录：2026-09-18 现状摸底与数据源核实

写本文前核过的事实，落地时以此为起点：

**仓库现状**

- 模型选择是请求级 ContextVar（`app/weather_model.py`），一次请求只算一个模式；单点缓存按模型隔离。
- 单站留档只在 `GET /v1/home` 与 `GET /v1/predictions/station` 成功时写；`scheduler.py` 的 12 个任务里没有按电站签发预测的。
- `prediction-archive` 与 `prediction-outlook` 没有清理任务。
- 卫星瓦片是 JPEG 灰度 0–255，`CLOUD_THRESHOLD` 是显示阈值；归档按电站 bbox 取瓦片、保留 60 天。
- `HOURLY_FIELDS` 15 个字段，没有降水；全仓库没有空气质量调用。
- `daily_generation.source` 已预留 `measured`；`Station` 无清洗日期与订正开关字段。
- 小程序：7 天预测与模型选择器只在首页；电站详情没有 outlook；`TrendChart` 最多两条线、无区间带；
  预警页无预警时只有一句文案；「我的」页没有导出入口。`pvlib 0.15.2` 含 `soiling.hsu`、`kasten96_lt`、`get_pvgis_horizon`。

**数据源**

- Open-Meteo 公开桶 `data/` 共 100 余个数据集；`cams_global/` 含 `aerosol_optical_depth`、`dust`、`pm10`、`pm2_5`；
  `ecmwf_ifs/precipitation/` 在；`cma_grapes_global/` 在，3 小时步长，含 `shortwave_radiation` 与 30–200 m 各层风。
  桶里没有卫星辐射数据集；`ecmwf_ifs025_ensemble/` 只有降水概率派生产品，成员数据不在桶里（已决定不做）。
- 托管卫星辐射接口 `jma_jaxa_himawari`：敦煌 2026-09-17 逐小时 GHI 峰值 802 W/m²，直射 630，量级正常；
  文档标 0.05°、10 分钟、延迟 30 分钟、2015 年起。定价页：Historical / Ensemble / Satellite Radiation 需 Professional 档起。
- NOAA `noaa-himawari9` 桶：`AHI-L1b-FLDK`、`AHI-L2-FLDK-Clouds`、`Winds`、`ISatSS`；1 km 波段中国段一帧约 40 MB。留作 V2 换源。
- JAXA P-Tree 条款：免费、需注册、JAXA 产品版权归 JAXA、JMA 标准数据限非营利、不得再分发。
- 风云：FY-4A 有 L2 SSI（4 km、15 分钟）；FY-4B 同款未核实。
