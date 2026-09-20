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
| 4 | 卫星辐照实况 | 葵花 5 km 卫星辐照产品，实况替代预报 | 预警页「卫星实况」卡 → 当前功率与当日累计 | JAXA P-Tree 葵花 SWR（免费；非营利，对外展示前告知事务局） | M3 |
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

**起报时刻统一报三家中最早的那一家**（2026-09-18 定）。三家都每 6 小时起报一轮（北京 02/08/14/20 点），
但发布要晚 3–7 小时，同一时刻常常是 ECMWF 还停在 08:00、ICON 已经出了 14:00。只报其中一家会让人以为
三家是同一批，报最新的又夸大了新鲜度；区间里最旧的数据就是最早起报那家，所以报它。
`basis` 的起报、可用、拉取三个时刻同出这一家，`model` 为 `ensemble`、`resolved_model` 为 null；
任何一家拿不到起报就说不清最早是哪一刻，`issued_at` 给 null、只报最早的拉取时刻（17 §二「不猜」）。
选择器里写「最早起报 08:00」，ⓘ 里列出三家各自的起报。

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

| 档 | 输入 | 来源 | 最少数据 | 用法 |
| --- | --- | --- | --- | --- |
| 日电量 | 每天一个数，可补录 | 逆变器 App、电表、值班日志 | 7 个有效日 | 取最近 `correction_window_days`（60）天拟合，跟着季节滚动 |
| 月电量 | 每月一个数 | 结算单、电费单 | 2 个有效月 | 日电量不够时用，取最近 6 个月 |

- 数是**上网电量**还是**发电量**由用户选，默认「发电量」；选上网电量时 ⓘ 注明系数会把线损与厂用电一起吃进去。
- 只对我的电站，需登录（`CurrentUserDep`）；公开目录电站不能记（403）。实测是用户经营数据：不进全目录、
  不对其他账号可见、随删除电站或「删除我的数据」一并删除。
- 校验：日期不晚于昨天（北京时间），月份必须已经过完；非负；不超过装机容量 × 当期小时数 × 1.1；
  一次最多 31 条；同一天 / 同一月重复记录覆盖。

### 模型同期电量从哪来

拟合要拿实测去比「模型在同一天算出的电量」。三个来源比较过：

| 来源 | 问题 |
| --- | --- |
| 预测留档 | 本版才开始每天签发，用户补录上个月的结算单时根本没有留档 |
| ERA5 再分析 | 与线上预报不是同一个模型，拟出的系数修的是再分析的偏差 |
| **预报接口 `past_days` 回算** | 同一个 `best_match` 模型，上游把过去每个小时拼成最近一轮的短时效预报，最多回溯 92 天 |

取第三种：`providers/open_meteo.recent_hourly` 按电站坐标取过去的逐小时数据（字段同主请求，不新增计费权重），
回溯天数按需要分 7 / 31 / 92 三档（记昨天只拉 7 天，补录一个多月才拉满），同一电站当天同一档共用缓存；
逐日走 `energy.prepare(day=…)` 与 `energy.hourly_power` 同一条链路算日电量；
设了出力约束的电站取计入约束后的「预计上网」，避免把限电学进系数。92 天以前的日子没有模型值，不参与拟合。
AEMO 对账（07 §8.1）显示模型/实测比值在各预报时效间基本不变，所以用短时效回算拟出的系数可以用在 7 天预测上。

算出的模型值连同指纹（电站设备参数 + 计算参数）存在记录上；指纹变了（改了容量、机型、约束）就重算，
不会拿旧参数的模型值去拟合新参数。

### 存储

- `measured_energy(id, station_id, kind: day | month, period_start, period_end, kwh, basis, model_kwh, model_digest,
  prior_kwh, recorded_at)`，唯一 `(station_id, period_start, period_end)`。
- 日电量同步写进 `daily_generation`（`source = measured`），累计发电与减排从此用实测；被覆盖的推算值存进
  `prior_kwh`，删除这条记录时恢复原值（原来没有记录就删掉那一行）。月电量拆不到日，只用于拟合，不改累计。
- `station_correction(station_id, fitted_at, method, sample_count, excluded_count, k, error_before, error_after,
  applied, reason)`，每站一行，只存最近一次拟合。
- `stations.correction_enabled`，默认开。

### 订正

- **样本**：有模型值、两边都大于 0、实测 / 模型在 0.2–5 之间的记录；比值出界的判为停机或录错，
  计入 `excluded_count`，界面标「未采用」。日电量够 7 条就用日电量，否则用月电量。
- **系数**：`k = Σ实测 / Σ模型`。光伏、风电同一公式。只做乘性订正，不做加性（夜间会出负数或凭空出力）。
- **回测（留一法）**：每条记录都用**其余**记录拟出的系数 `k₋ᵢ = (ΣY − yᵢ) / (ΣM − mᵢ)` 去修它，
  比较修正前后的**逐条平均误差** `mean |预测 / 实测 − 1|`（`error_before` / `error_after`）。
  原先打算按时间切末尾 20% 做回测，实测发现日电量本身就有 ±10–20% 的起伏，回测段只有一两天时，
  碰上一天异常（真实数据里十天偏低 17%、最近一天偏高 8%）就把整站订正否决掉，订正会今天开、明天关。
  留一法用上全部样本，问的是「这个系数对一般的一天有没有用」；单一乘性系数只有一个自由度，不怕用未来修过去。
- **启用条件**：订正后的逐条平均误差比订正前至少小 `correction_min_gain_pct`（3 个百分点），且 `k` 在 0.4–2.5 之间。
  出界说明参数本身错了（容量填成直流、机型档差一倍），这时不订正，提示先核对装机容量与机型参数。
  不满足就保存拟合结果但不应用，`reason` 写明原因或还差几条。
- **怎么乘**：在逐时出力进入出力约束之前乘 `k`，再按装机容量限幅，然后才算限电与上网 ——
  `P_corr = min(k × P_model, Cap)`，上网口径随之重算。**指数不乘**（指数只反映气象）。
- **作用范围**：该站的 7 天预测（三模式时每家先修再聚合）、首页今日、当前功率、AI 报告的日电量、
  累积表的推算行。**留档不乘**：签发留档与预报演变存的是模型原始值，否则系数一变演变就跟着跳；
  全目录汇总也不乘。
- **重拟合**：每次记录或删除后立即回算并拟合，但接口**最多等 `measured_refresh_budget_s`（6 秒）**：
  回算要出网，赶上上游慢或别的任务在抢，十几秒都可能（开发机上撞上地图预处理实测 18 秒），而小程序请求 10 秒就超时。
  超时就先回「回算中」，回算在自己的数据库会话里跑完，对账页与首页过几秒自动再取一次。
  回算拿不到时先存记录、模型值留空；每日任务 `fit_corrections`（北京 10:05）补缺失的模型值、按滚动窗口重拟合。
- 响应：`DailyOutlook.corrected`、`StationOutlook.correction`（启用时给系数、样本数、回测前后偏差），
  `GenerationPrediction.corrected`（全目录快照要能读回旧文件，这一个带默认值 false，同 `estimated`）；
  `assumptions` 加「已按 N 天实测订正：模型偏高 18%，按 0.85 倍修正（逐日误差 19% → 5%）」。

### 接口

```
GET    /v1/stations/{id}/measured              → MeasuredSummary
POST   /v1/stations/{id}/measured              body { entries: [{ kind: 'day' | 'month', date, kwh }], basis }
                                               date 为 YYYY-MM-DD 或 YYYY-MM；返回 MeasuredSummary
DELETE /v1/stations/{id}/measured/{entry_id}   → MeasuredSummary
PATCH  /v1/stations/{id}                       增加 correction_enabled: boolean
```

全部 `CurrentUserDep`，归属校验同现有自建电站接口。

### 页面

- **首页预测卡（我的电站）**：卡片底部「记一笔」，弹层：日 / 月两段、日期（默认昨天）或月份、电量（MWh，
  实时换算 kWh）、发电量 / 上网电量两段，一步提交；成功提示「已记录 · 模型同期 12.1 MWh」，还在回算时提示「回算中」。
  **弹层贴顶而不是贴底**：软键盘从底部升起会盖住贴底的输入框与提交按钮（CLAUDE.md「已知的环境坑」）。
  订正生效时大数字旁徽章「实测订正」，ⓘ 说明系数与样本。
- **电站详情 → 实测对账页**（`pages/station/measured`，`PageHeader`「实测对账」）：
  Hero 是订正状态卡（模型偏高 / 偏低多少、按几倍修正、样本数、订正前后的逐日误差、开关、未生效时的原因）；
  下面是记录列表（日期、实测、模型同期、比值、是否采用），可补录、删除。

### 合规（09）

- 隐私协议与 mp 后台数据收集清单增加「电站实测发电数据（用户主动填写）」：用途为对账与订正，
  仅本账号可见，随电站删除。
- 不接逆变器 / SCADA 接口（V2）。


## 四、卫星辐照实况

### 数据源（2026-09-18 调研，不走 Open-Meteo 付费接口）

原计划用 Open-Meteo 卫星辐射接口的 `jma_jaxa_himawari`（Professional 付费档）。它底层就是 JAXA 的葵花短波辐射产品，
而 JAXA 自己的数据门户 P-Tree 免费提供同一份数据。逐一核实过的渠道（「原文」表示读过条款原文）：

| 渠道 | 产品 | 分辨率 / 步长 | 延迟 | 费用与条款 | 结论 |
| --- | --- | --- | --- | --- | --- |
| **JAXA P-Tree 葵花 SWR** | L2 短波辐射（另有 PAR、AOT），NetCDF | 0.05°（5 km），10 分钟；L3 逐小时 | 实测约 30–40 分钟（13:18 UTC 时最新到 12:40） | 免费。FAQ Q4-1 原文：2026-02-01（UTC）起的数据可商用；JAXA 研究数据条款原文 §2.3：可免费商用、可修改和分发，须**事先通知 JAXA**（earth＠ml.jaxa.jp）并**署名**。但注册页仍写「仅限非营利、不得再分发」，与 FAQ 冲突 | **首选**，注册前先书面确认 |
| 风云四号 FY-4B AGRI（国家卫星气象中心） | L2 地表短波辐射 SSR（含总辐照 SSI、直射、散射等 8 个要素），NetCDF | 4 km，15 分钟全圆盘 | 实测扫描结束后约 10–17 分钟 | 免费，个人实名注册。法律声明原文：未经书面许可不得以营利为目的使用；只能「检索 → 下单 → 取件」，L2 不能订阅推送，单文件 27–86 MB | 境内备选，须签资料提供协议 |
| 中国气象数据网 CLDAS-V2.0 实时产品 | 卫星反演与地面站融合的短波辐射 | 0.0625°，逐小时 | 约 1 小时 | 条款同上，须书面授权 | 境内备选，比纯卫星更贴近站点 |
| 哥白尼 CAMS 太阳辐射服务 | 地表辐照时间序列，2023-10 起接入葵花 | 逐点查询 | 1 天 | CC BY 4.0，原文明确面向商业下游；每用户每天 500 次 | 做不了实况；可做次日回看与对账 |
| NASA POWER | 全天空地表短波 | 1°（约 100 km），逐小时 | 实测约 80 天 | 免费可商用 | 不可用 |
| 韩国 GK-2A DSR | 下行短波辐射 | — | — | 门户从本机与抓取工具都连不上，规格与条款未核实 | 暂不可用 |
| NOAA 公开桶的葵花 / GK-2A L1b | 定标后的原始观测，无辐射产品 | 1 km 波段中国段一帧约 40 MB | 实时 | NODD 原文：可随意使用，须署名、不得暗示 NOAA 背书 | 只能自己反演，留作 V2 |

不再用现有 JMA 网页瓦片做云指数反演：0–255 显示灰度、JPEG 有损、调色板未知，反演出来说不清。

**决定：用 JAXA P-Tree 的 SWR 实况；FY-4B SSR 作境内备选；CAMS 留作次日对账。**
P-Tree 与 Open-Meteo 卖的是同一份数据，自己取就省掉订阅费。

### 怎么取：用自建 Open-Meteo 自带的下载器（2026-09-19 核实）

Open-Meteo 卖的卫星辐射，是它开源代码里的 `download-jaxa-himawari` 命令拿**用户自己的 P-Tree 账号**从 FTP 拉 L2 SWR
（`Sources/App/JaxaHimawari/`）。Buffalo 那台跑的官方镜像 1.6.0（2026-09-10 发布）的源码里有这个命令，
所以**不必自己写 FTP 与 NetCDF 解析**：

- **下载入库**：Buffalo 上每 10 分钟跑一次
  `openmeteo-api download-jaxa-himawari himawari_70e_10min --username … --password …`（70E 起的扩展格点，覆盖新疆西缘）。
  账号密码放 Buffalo 本机的环境文件，不进仓库；命令另起一个带 `mem_limit` 的一次性容器，与常驻服务共用数据卷。
- **它替我们做掉的三件事**：
  1. **扫描时刻校正**：L2 是**瞬时值**，文件名时间是全圆盘扫描**起点**，一帧从北往南扫约 10 分钟，
     日本一带的像元实际在起点后 8 分钟左右才观测到。下载器按 JMA 辅助文件里的逐像元观测时刻，
     把瞬时值换成「前 10 分钟均值」并标在**区间末**，与项目里 Open-Meteo 预报的辐射口径一致；
  2. 每天 02:40–02:50、14:40–14:50 UTC 卫星例行维护不观测，自动跳过；
  3. 葵花 8 / 9 号切换（2025-10-11 至 11-26 临时换回过 8 号）时文件名前缀会变，已按日期处理。
- **取值**：后端按电站坐标查自建实例的 `/v1/archive?models=jma_jaxa_himawari`，与其他气象请求一样走
  `providers/weather_transport`。直射、散射与倾斜面由 Open-Meteo 同一套辐射分解给出，不再自己做 Erbs 分解。
  **必须带 `temporal_resolution=native`**，否则只返回小时均值，实况要等整点过完。
  `minutely_15` 与 `current` 在这条路径上无效 —— 卫星走的是 archive 控制器，它的 `has15minutely` 是 false。
  2026-09-20 实测（苏州 31.3,120.62）：`01:40 → GHI 264 W/m²，直射 36.4，散射 227.6`，`01:50 → 289 / 44.2 / 244.8`，
  时间标签是区间末的 10 分钟均值，与预报口径一致。实况总延迟 = JAXA 落地 22 分钟 + 我们的拉取间隔 10 分钟 ≈ 25–35 分钟。
- **为什么放 Buffalo 不放北京**（2026-09-19 实测）：FTP 主机 `ftp.ptree.jaxa.jp`（133.56.101.71）的 21 / 990 / 2051 端口
  两台都连得通，TCP 往返北京约 95 ms、Buffalo 约 160 ms；但北京从 JAXA 网站下载只有 20–30 KB/s，
  跟不上 10 分钟一帧的全圆盘文件。Buffalo 测到约 470 KB/s，但那次连的是 CDN 节点，不代表 FTP 主机本身的速度。
### 账号与实测（2026-09-20，Buffalo）

账号已有（用户自有，UID 是注册邮箱把 `@` 换成 `_`，口令是 JAXA 发给所有用户的同一个默认串）。
凭据只存在 Buffalo 的 `/root/.ptree.netrc`（600），不进仓库、不进日志。

| 项 | 实测 |
| --- | --- |
| 路径 | `/pub/himawari/L2/PAR/021/{YYYYMM}/{DD}/{HH}/H09_{YYYYMMDD}_{hhmm}_RFL021_FLDK.02801_02401.nc` |
| 一帧大小 | 46.6 MB（00:00 UTC）→ 54.1 MB（01:30 UTC），白天更大；同目录另有 1 km 日本区 `rFL021` |
| 落地延迟 | 01:30 那帧的 `Last-Modified` 是 01:51:45，约 **22 分钟** |
| 下载耗时 | Buffalo 10.6 s（约 5 MB/s）；北京 20–30 KB/s，一帧要半小时，不可用 |
| 下载器一轮 | 42 s（含首次取 136 MB 的扫描时刻辅助文件 `AuxilaryData.nc`，之后复用）；转换 1.74 s；`--memory=1500m` 够用 |
| 落盘 | 每帧约 **13 MB**；分块 `omFileLength = 288` 步 = **48 小时一个 `chunk_*.om`** |
| 出网 | 144 帧/天 × 约 50 MB ≈ **7.4 GB/天**（约 220 GB/月），HostPapa 套餐额度待确认 |

**保留策略**（2026-09-20 用户定：卫星只留 6 小时、不展示历史）：删除粒度是 48 小时的块，删不到单帧。
所以只保留正在写的那个块，上一个块轮换后 6 小时删掉（`find … -name 'chunk_*.om' -mmin +360 -delete`），
占用上限约一个满块 ≈ 3.7 GB。不能不删 —— 数据盘剩 26 GB，而 Open-Meteo 的 `CACHE_SIZE` 设的是 32 GB
（`cache.bin` 已 25 GB，还会继续涨），两边一起会把盘撑满，进而拖垮线上依赖的气象服务。

踩过的坑：
- 镜像 ENTRYPOINT 已经是 `./openmeteo-api`，`docker run` 只传子命令，再写一遍会报 `Unknown command`。
  子命令列表里确认有 `download-jaxa-himawari`。
- `--run` 传 ISO 时刻报 `InvalidDateFromat`，格式没试出来；不用它也行，不带参数会按 `last.txt` 自动追帧（单轮最多跑 8 分钟）。
- 不要在常驻容器里 `docker compose exec` 跑子命令（`--help` 两分钟不返回）；用一次性容器。
- 到 Buffalo 的 ssh 长连接会被切断（8 分钟的下载跑一半断过），远端长任务要 `nohup` 后台跑、再轮询日志。

**产品本身的限制**：JAXA 标注 SWR 是 beta 版、**不做质量保证**；P-Tree 没有服务等级承诺。
所以卫星值只作实况参考，拿不到就退回预报，不影响任何主数字；精度按下文「校准」先对账再上线。

备选：下载器用不了时再自己写 FTP 与 NetCDF 解析，变量名 `SWR`（W/m²，int16 × `scale_factor` + `add_offset`，≤ −999 为缺测），
上面的扫描时刻校正必须照做，否则日出日落前后偏差很大。

### 授权与上线前要办的事

1. **公开展示前告知事务局（最重要）**：2026-09-19 用户确认 EnerSight **不盈利**，且已有 P-Tree 账号，
   注册页「仅限非营利用途」这一条满足。剩下的是注册页另两句：「不得向第三方再分发数据」，以及「研究成果对外公开前
   请先联系 P-Tree 事务局」。所以写信给事务局（Z-PTREE＠ml.jaxa.jp），说明是非营利小程序、界面上展示由 SWR 算出的
   各电站辐照与出力数值、署名 JAXA / P-Tree、不转发原始文件，请对方确认可以。拿到答复前可以接入做内部对账，不对外展示。
2. **若转为盈利**（接广告、收费功能、以公司名义经营）：按 FAQ Q4-1 只用 2026-02-01 之后的数据，
   并按研究数据条款 §2.3 事先邮件通知 JAXA（earth＠ml.jaxa.jp）。
3. **署名**：读数旁「JAXA / P-Tree」；与 JMA 云图的「日本气象厅」出处一起保留。
4. **境内备选**：若 JAXA 不同意，改走 FY-4B SSR，须与国家卫星气象中心签资料提供协议（dataserver@cma.gov.cn）。
   两种都要按《气象信息服务管理办法》向营业执照所在省的气象局备案（查到的是 2015 年版，是否修订未核实），
   与 [09 §九](./09-miniapp-compliance.md) 的其他合规事项一起办。

调研原始记录（每条标了是否读过原文）在会话临时目录，结论已全部并入上表。

### 口径

- **拉取**：Buffalo 的下载器每 10 分钟入库（见上文「怎么取」）；后端 `satellite_irradiance` 任务按电站坐标批量查自建实例，
  取最近一个非空的 10 分钟值；缺帧退回上一帧，超过 `satellite_irradiance_stale_minutes` 判过期。
- **用途**：
  1. 预警页「卫星实况」卡：辐照大数字 + 「晴空的 78%」（分母 `metrics/solar.clearsky_interval_mean`）+ 观测时间；
  2. 当前功率：用卫星的 GHI / 直射 / 散射直接算 POA，走 `pv.hourly_power` 只替换当前区间，标「卫星实况」；
  3. 当日累计「卫星订正」：今天已过去的区间用卫星辐照重算，累积表 forecast 行更新，`source = satellite`；
  4. 指数不改（指数是全天预报口径）；地图辐射图层「实况」变体留 V2。
- **降级**：接口不可达、夜间、延迟超过 `satellite_irradiance_stale_minutes`（90） → `irradiance` 为 null 并给 `status`，
  当前功率退回预报值。卫星辐照与预报辐照差超过 60% 时仍用卫星值，但在 ⓘ 提示「与预报差异大」。
- **风电不适用。**

### 校准（不用等归档）

P-Tree 存档回溯到 2015，可直接与 `enersight-validation-data` 里 PVOD 河北 10 座光伏站 2018–2019 的实测总辐照对账。
2026-02 之前的数据限非营利，拿它做内部精度评估属研究用途，在书面确认里一并问清楚。判据：
逐小时 RMSE ≤ 25%（相对日间均值）、偏差在 ±10% 内视为达标；同时对比 ERA5 驱动的误差
（[07 §8.1](./07-metrics.md) 2026-09-16 一节）看卫星比再分析好多少。结论写入 07 §8.1，达标前只做用途 1。

### 接口

**挂在 `/v1/alerts/current` 上，不挂云图接口**（2026-09-20 改）：预警页的第一个请求就是它，
而云图要拉瓦片、重采样，慢得多；Hero 卡不该等云图。

```ts
interface CurrentAlertResponse {
  // …现有字段
  irradiance: SatelliteIrradiance | null   // 关掉开关才是 null
}
interface SatelliteIrradiance {
  ghi_w_m2: number | null
  direct_w_m2: number | null
  diffuse_w_m2: number | null
  clear_sky_ghi_w_m2: number | null
  clear_sky_index: number | null
  observed_at: string | null                // 非 ok 状态为 null
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
- **「卫星实况」卡**（`SatelliteNowcast`，**已落地 2026-09-20**）：辐照大数字 + 「晴空的 44%」+ 观测时间 +
  直射 / 散射一行 + 署名；无预警时 `hero` 放大数字。下方 `CloudMotionStats` 三宫格保持；再下 `SatelliteTimeline` 不变。
- 电站详情「当前气象」卡底部加一行「卫星辐照 612 W/m² · 14:20」（**未做**，等回测达标一起）。
  首页四宫格的辐射项不换来源，环比口径不动。


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
| `fit_corrections` | 每日 02:05（北京 10:05）；记录后立即 | 补缺失与参数变过的模型同期值、按滚动窗口重拟合 |
| `satellite_irradiance` | 每 10 分钟 | 从自建 Open-Meteo 取各电站最新的葵花 SWR（Buffalo 下载器入库），更新当前功率与当日累计（M3） |
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
| `correction_min_days` / `correction_min_months` | 7 / 2 | 日电量 / 月电量拟合门槛 |
| `correction_window_days` / `correction_window_months` | 60 / 6 | 拟合只看最近这些天 / 月 |
| `correction_min_gain_pct` | 3 | 留一法：订正后逐条平均误差至少小几个百分点才启用 |
| `correction_k_min` / `correction_k_max` | 0.4 / 2.5 | 系数出这个范围不订正，提示核参数 |
| `correction_ratio_min` / `correction_ratio_max` | 0.2 / 5 | 实测 / 模型出界判为停机或录错 |
| `hindcast_max_days` / `ttl_hindcast` | 92 / 6 小时 | 回算最多回溯天数与缓存 |
| `measured_refresh_budget_s` | 6 | 记录接口最多等回算几秒，超时转后台 |
| `ptree_host` / `ptree_user` / `ptree_password` | `ftp.ptree.jaxa.jp` / 环境变量 / 环境变量 | 卫星辐照；账号密码只走环境变量，不进仓库 |
| `satellite_irradiance_stale_minutes` | 90 | 超过即退回预报 |
| `air_quality_base` | `https://air-quality-api.open-meteo.com/v1` | 空气质量接口 |
| `soiling_cleaning_threshold_mm` / `soiling_wash_loss_pct` | 0.5 / 5 | 积灰模型与清洗建议 |

### 数据表（alembic 迁移）

| 表 / 字段 | 说明 |
| --- | --- |
| `measured_energy` | 月 / 日电量记录，唯一 `(station_id, period_start, period_end)` |
| `station_correction` | 订正拟合结果与留一法回测误差 |
| `station_environment_daily` | 逐日 PM / AOD / 降雨 / 积灰比 |
| `station.last_cleaned_on`、`station.correction_enabled` | 电站新增字段 |

删除电站与「删除我的数据」要一并清这三张表（[09 §4.3](./09-miniapp-compliance.md)）。


## 七、里程碑与验收门

| 里程碑 | 内容 | 进入下一步的门槛 |
| --- | --- | --- |
| M1 ✅ | 三模式默认、区间带与三家并列、预报演变、`issue_outlooks`、留档清理、各链路口径统一 | 第一、二节验收项全过；H5 截图已看，开发者工具截图待合入主仓库后补 |
| M2 ✅ | 随手记、订正、实测对账页 | 真实上游数据走通记录 → 回算 → 拟合 → 订正生效；H5 截图已看，开发者工具截图待合入主仓库后补 |
| M3 | 卫星辐照：Buffalo 下载器 ✅、后端取值 ✅、预警页卫星实况卡 ✅（2026-09-20）；**剩 PVOD 回测**，达标后才做当前功率与当日累计切换 | 已有 P-Tree 账号（2026-09-19 密码待找回）；对外展示前需事务局答复（§四「授权」第 1 步）；回测结论写入 07 §8.1 |
| M4 | 空气质量接入、气溶胶归因、积灰卡、`precipitation` 字段 | 敦煌 / 拉萨 / 苏州三点归因数值合理；04 §二字段表已更新 |

M1 与 M2 共用签发任务，先后做；M3 的回测不依赖归档，找回 P-Tree 密码即可开工，对外展示等事务局答复；M4 独立可并行。


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
| 2 | JAXA 书面确认 P-Tree SWR 可在商业小程序里展示衍生数值 | 卫星辐照的前提；不同意就改走 FY-4B，须与国家卫星气象中心签协议 |
| 3 | 气象信息服务备案（《气象信息服务管理办法》） | 用卫星辐照前向营业执照所在省气象局备案，与 09 §九合规事项一起办 |
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
- 风云：FY-4B 的地表短波辐射产品叫 SSR（总辐照 SSI 是其中一个变量），4 km、15 分钟，实测扫描结束后 10–17 分钟可取。
- JAXA P-Tree：葵花 SWR 5 km、10 分钟，实测延迟 30–40 分钟；FAQ Q4-1 写明 2026-02-01 起可商用，注册页旧表述冲突。
- 实测订正（真实上游）：苏州 30 MW 自建站记 10 天、实测约为模型 0.85 倍，拟合 k = 0.84，逐日误差 19% → 5%；
  回算过去 92 天逐小时数据单次拉取约 3 秒、10 天计算 0.1 秒。
