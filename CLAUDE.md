# EnerSight — AI 新能源气象遥感分析平台

微信小程序（V1）→ App（V2）。基于气象、卫星遥感、地理空间数据与 AI 分析的
新能源电站环境分析平台。

**当前阶段：V1 功能全部落地，待真机验证与合规。** 详见本文末尾「当前状态」。


## 文档

改动任何东西之前先读对应文档。文档是唯一口径，代码与文档不一致时以文档为准，
若确认文档有误，先改文档再改代码。

| 文档 | 内容 | 什么时候读 |
| --- | --- | --- |
| [docs/01-prd.md](docs/01-prd.md) | 功能需求、页面清单与跳转 | 做任何页面前 |
| [docs/02-design-system.md](docs/02-design-system.md) | 颜色、字体、间距、圆角、图表规范 | 写样式前 |
| [docs/03-component-library.md](docs/03-component-library.md) | 37 个组件的结构与变体 | 写组件前 |
| [docs/04-data-specification.md](docs/04-data-specification.md) | 数据源字段、图层规格、单位约定 | 碰数据前 |
| [docs/05-architecture.md](docs/05-architecture.md) | 技术选型、工程结构、地图方案、容量测算 | 动结构前 |
| [docs/06-api-contract.md](docs/06-api-contract.md) | 16 个接口、公共类型、错误码 | 写接口前 |
| [docs/07-metrics.md](docs/07-metrics.md) | 环境指数算法、发电估算、预警阈值 | 写计算前 |
| [docs/08-ai-design.md](docs/08-ai-design.md) | AI 输出、模型选型、缓存、降级 | 碰 AI 前 |
| [docs/09-miniapp-compliance.md](docs/09-miniapp-compliance.md) | 备案、类目、权限、内容安全 | 上线前，且要早读 |
| [docs/10-deployment.md](docs/10-deployment.md) | 镜像、Compose、环境变量、升级回滚 | 部署前 |
| [docs/17-user-stations-and-outlook.md](docs/17-user-stations-and-outlook.md) | 自建场站、7 天预测与起报时间、限电三层口径 | 碰站点表单、预测天数、限电前 |
| [docs/19-forecast-uncertainty-and-observations.md](docs/19-forecast-uncertainty-and-observations.md) | 集合区间、预报演变、实测上传与 MOS 订正、积灰、卫星辐照反演 | 碰模型选择、预测留档、实测数据、空气质量、云图反演前 |

设计稿在 `packages/ui/`，6 张 PNG，对应 6 个页面。**实现页面前先看图。**


## 技术栈

| 层 | 选型 |
| --- | --- |
| 小程序 | Taro 4 + React 18 + TypeScript |
| 状态 | Zustand |
| 样式 | Sass + CSS 变量，尺寸用 `rpx` |
| 跨端复用 | `packages/core` — 类型（codegen）、API client、格式化 |
| BFF | Python 3.12 + FastAPI |
| 科学计算 | pvlib（太阳位置/辐射/光伏）、OpenCV（光流）、scipy、numpy、Pillow |
| 依赖管理 | pnpm workspace（前端）、uv（Python） |


## 目录

```
docs/                   文档，唯一口径
packages/
├── ui/                 设计稿 PNG
├── core/               纯 TS，零 UI 依赖
│   ├── types/          ★ codegen 生成，禁止手改
│   ├── api/            API client
│   └── format/         单位进位、千分位、时间
├── miniapp/            Taro 小程序
│   ├── src/pages/      7 个页面
│   ├── src/components/ 37 个组件
│   ├── src/store/      Zustand
│   └── src/styles/     设计 token
└── server/             FastAPI
    ├── app/routers/    接口，对应 06
    ├── app/schemas/    Pydantic → OpenAPI → core/types
    ├── app/providers/  Open-Meteo / Himawari / 腾讯位置服务
    ├── app/metrics/    指标计算，对应 07
    ├── app/render/     图层渲染，无状态
    ├── app/satellite/  云图与光流
    ├── app/ai/         AI 编排，对应 08
    └── app/jobs/       定时任务
```

依赖方向单向：`miniapp → core`。`server` 不依赖 `core`，只通过 OpenAPI 产出类型。


## 硬约定

违反这些会产生难排查的 bug，或让后续返工。

### 坐标系

```
存储与计算   一律 WGS84
出口转换     仅在响应序列化时按 coord 参数转 GCJ-02
客户端       不做任何坐标转换
```

Open-Meteo / Himawari 是 WGS84，腾讯地图是 GCJ-02，境内偏移几十到几百米。
混用会导致「站点标记和云图错位」——不报错，只是位置差一点。

`pyproj` **不含** GCJ-02，它是中国特有的非线性加密偏移，需自行实现并覆盖测试。

### 单位

```
API 返回      基础单位裸数值：kW、kWh、W/m²、℃、m/s、km、kg、元
不返回        单位字符串，不做进位
展示层转换    core/format 负责 kW→MW、kWh→GWh、kg→吨、千分位
```

### 空值

```
数据缺失  →  null
不要      →  省略字段，或用 0 代替
```

`delta_percent` 为 null 时前端隐藏环比标签，`score` 为 null 时显示「数据获取中」。
用 0 代替会展示成「环比 0%」「指数 0 分」。

### 时间

ISO 8601 带时区偏移 `2026-09-07T14:00:00+08:00`，按站点当地时区。
不用时间戳数字，不用不带时区的字符串。

### 时间窗口有两个，别混

| 概念 | 定义 | 用途 |
| --- | --- | --- |
| 日间时段 | 按经纬度与日期算日出日落 | 指标聚合窗口 |
| 上午/下午/晚间 | 固定 06–12 / 12–18 / 18–24 | AI 报告展示分段 |

### 环比配色

上升红、下降绿。表示**变化方向**不是好坏，不要按股票涨跌习惯反转。
云量上升也是红色。

### AI 只解释，不算术

所有数值由 `app/metrics/` 按 07 算好后作为输入喂给模型，模型不做任何计算。
输出需过数值一致性校验：输出中的数字必须都在输入中出现过，否则判定幻觉走降级。


## 常见错误

- ❌ 在 `core/` 里写指标计算 —— 指标全在服务端，06 契约已定为算好返回
- ❌ 手改 `core/types/` —— 生成产物，改 Pydantic 模型后跑 `make codegen`
- ❌ 在 async 路由里同步调 numpy/OpenCV —— 阻塞事件循环，用 `run_in_executor`
- ❌ 手写太阳位置/晴空辐射公式 —— 07 附录 A 是口径定义，实现用 pvlib
- ❌ 用因子加权求和算环境指数 —— 已改为物理出力比，见 07 §1.2
- ❌ 按整点算太阳位置 / 晴空基准 —— Open-Meteo 小时值是前一小时均值，太阳位置取区间中点、晴空取小时均值，见 07 §2.1
- ❌ 把交流容量直接当 PVWatts 的 `pdc0` —— 站点容量是交流侧，`pdc0 = 容量 × 容配比`，出力按容量限幅
- ❌ 用固定风切变指数从 10 m 外推轮毂风速 —— Open-Meteo 有 80/100/120/200 m，按层插值；α 只是缺数据时的降级。
  ECMWF IFS 原生只有 10/100/200 m，轮毂高于 120 m 要靠 200 m 层插值，别用 100–120 m 斜率外推
- ❌ 气象缺测时把 NaN 当 0 算指数 / 发电 —— 会显示「0 分 较差」。缺测判定统一在 `services/energy.prepare`，不可算给 null
- ❌ 再写一份光伏逐时链路 —— 指数、发电、当前功率、预测、区域汇总全部走 `metrics/pv.hourly_power`
- ❌ 风电强风 / 切出预警看 10 m 风速 —— 要和功率曲线一样看轮毂高度
- ❌ 目录风电一律用通用功率曲线 —— 2015 年后的陆上低风速机型会少算一半；按投运年份与海上 / 陆上选机型档，
  走 `prediction_basis.catalog_turbine_class`，全目录曲线缓存键要带机型档，见 07 §2.2
- ❌ 海上风电用上游默认格点 —— 默认 `cell_selection=land` 把近岸场站分到陆地格点，风速偏低三成。
  目录海上风电带 `sea`（`catalog_cell_selection`），点预报走 `weather.station_forecast`，
  全目录分组与缓存键都要区分，见 04 §二
- ❌ 拿设计稿里的数值对账 —— `packages/ui/` 中所有数字都是视觉示意值
- ❌ 客户端硬编码图层色阶 —— 图例由 `/v1/map/layers` 下发
- ❌ 客户端用自己请求的 bbox 贴图 —— 用服务端返回的对齐后 `bounds`
- ❌ 实现储能站相关功能 —— V1 不做，设计稿里有但已明确排除
- ❌ 把自建站点算进全目录汇总 —— 汇总只统计公开目录，自建站点仅本账号可见，见 17 §一
- ❌ 限电时把环境指数也折减 —— 指数只反映气象条件；出力约束只作用于电量与功率，见 17 §四
- ❌ 起报时间拿不到时用拉取时间冒充 —— `basis.issued_at` 为 null 就只显示拉取时间，不猜
- ❌ 预测做到 7 天以上 —— ICON 全球模式只有 7.5 天，7 天是四个模型的公共上限
- ❌ 把 `ensemble` 设进 `current_model` —— 它会被原样当成 Open-Meteo 的 `models` 发出去；
  三模式是路由层的模式（`request.state.ensemble`），`services/ensemble` 逐个成员设真实模型并发算，见 19 §一
- ❌ 三模式主数字用逐点中位合成曲线 —— 求和对不上任何一家的日电量；整套取当天居中那家，见 19 §一
- ❌ 客户端拿 `'ensemble'` 跟响应的 `model` 比来丢旧响应 —— 除 7 天预测外服务端都回 `best_match`，
  页面会一直停在加载；用 `store/weatherModel.servedModel`
- ❌ 预报演变混着模型串 —— 看到的是模型差异不是演变；固定 `evolution_model`，读留档索引 `*.meta.json`
- ❌ 实测订正拿 ERA5 或预测留档当模型同期值 —— ERA5 不是同一个模型，留档补录上月时根本没有；
  用预报接口 `past_days` 回算（`services/hindcast`），见 19 §三
- ❌ 订正系数乘在出力约束之后、或连指数一起乘 —— 在逐时出力进约束之前乘、按容量限幅，限电与上网随之重算；
  指数只反映气象不乘；留档与演变存原始值不乘（`correction.apply_snapshot` / `prediction.compute_days_pair`）
- ❌ 订正回测只切最近几天 —— 一天异常就能否决整站订正；用留一法的逐条平均误差，见 07 §九
- ❌ 记录实测时同步等回算 —— 上游慢时十几秒，小程序 10 秒就超时；最多等 `measured_refresh_budget_s`，超时转后台
- ❌ 把 15 分钟曲线当成更准的预报 —— 境内 `minutely_15` 是上游从逐小时插值的，只对齐两个细则的
  96 点格式；求和要乘 0.25 小时，光伏区间末标签要减 15 分钟而不是 1 小时，见 07 §2.7
- ❌ 风电功率曲线不做空气密度修正 —— 高原站会高估两到三成；密度从气压气温算，缺气压按海拔 ISA，见 07 §2.2
- ❌ 跟踪支架仍用倾角方位角算 POA —— `mounting=single_axis` 时姿态由 pvlib singleaxis 逐时刻给出
- ❌ 用户请求时同步调 AI —— 全部预生成 + 缓存，见 08 §三
- ❌ 给 `HOURLY_FIELDS` 随手加字段 —— Open-Meteo 超过 10 个变量按比例加权计费，
  加一个字段全项目每一次拉取都在付钱。先确认谁读它，并同步 04 §二
- ❌ 把气象预报按短时间片缓存，或让新批次、跨日随时触发整轮重拉 —— 上游 6 小时才出一批。
  单点预报每坐标每天最多回源 4 次（按当地时段，跨时段批次没变不重拉，`weather.get_forecast`）；
  全目录每个模型每天只拉一轮，08:00 前沿用上一日快照（`fleet_prediction`）。见 04 §二
- ❌ 在遍历站点的定时任务里并发写 DB —— `AsyncSession` 不能被多个任务同时用。
  算与写分得开就「算并发写串行」（`services/accumulate`），分不开就「一任务一会话」
  （`generate_reports`，并发上限受 `db_pool_size` 约束）。取站点一律用
  `db.pages` / `db.id_pages` 分批，不要 `select(Station)` 全表 `.all()`
- ❌ 以为多坐标请求能省 Open-Meteo 额度 —— 限流按**坐标数**计（实测 429 精确落在
  第 600 个坐标），批次大小只省 HTTP 往返。要控的是坐标速率
  （`fleet_coords_per_minute`），且限额按 IP 与其他调用共享
- ❌ 直接 `http.get` Open-Meteo —— 统一走 `providers/weather_transport.weather_get`，否则测试环境开启
  代理池试验时会漏出直连，见 10「气象出网」
- ❌ 把 `minutely_15` 混进主请求 —— 只有单站 7 天预测要它，混进去等于让每次后台扫描
  都为它付权重。走 `get_forecast(..., fine=True)`
- ❌ 看图像亮度判昼夜 —— 缺帧黑图会误判；按太阳高度角（`services/satellite.is_day`）
- ❌ 卫星拿不到时清掉卫星预警 —— 未知 ≠ 消失，`apply_detections(satellite_known=False)`
- ❌ 用 `floor` 的整点去取辐射 / 光伏出力 / 晴空指数 —— 它们是前一小时均值标在区间末，
  「当前」是包含此刻的那一格（`Forecast.current_interval()`）。瞬时量（气温、风速、
  云量、weather_code）才用 `current_hour()`。取错一格在日出日落前后差几倍，见 04 §二
- ❌ 把两种时间语义一起平移 —— AI 报告「上午 06–12」里辐射取标签 07…12、
  云量取 06…11，不是整段挪一小时
- ❌ 拿「最近一条更早记录」当昨日算环比 —— 昨日缺记录就隐藏标签，见 07 §3.3
- ❌ 给 `daylight_window` 传当地零点 —— pvlib 按 UTC 日期取日序，东八区会算成前一天
- ❌ 全缺测时段用 0 填进 AI 报告 —— 0 会进数值一致性校验集，模型就能引用不存在的读数
- ❌ 只归档卫星最新一帧 —— 瓦片常未就绪，漏掉的帧 35 小时后再也补不回来，要回补最近几帧
- ❌ 同一个云图响应里混卫星实况与预报云量 —— 一个图例说不清，有一块拿不到就整层退回
- ❌ 按地区筛选时拿全国容量当覆盖率分母 —— 会把「云南省」显示成覆盖 0.9%。分母按省重算，
  逐省明细里存着该省的目录总数与总容量，见 17 §二
- ❌ 拿「覆盖了整个目录」判断全目录汇总的 `status` —— 被排除的场站永远不会被覆盖，
  状态会永远停在 partial；`status` 说的是算完没有，覆盖程度另有字段
- ❌ 分档文案自己写一套 85/70/55 —— 跟着 `classify` / settings 走，否则改配置会出现
  「82 分 · 条件较差」
- ❌ 校准脚本自己 `fillna(0)` —— 复用 `services.energy.prepare`，两套预处理校准不出线上行为
- ❌ 拿黑瓦片当无云 —— JMA `targetTimes_fd.json` 先于瓦片更新，非 200 要退回上一帧
- ❌ 删掉云图卡上的「日本气象厅」出处 —— JMA 利用规约要求注明来源
- ❌ 删掉公开电站列表底部的 WRI / GEM 署名 —— CC BY 4.0 署名条款
- ❌ 在小程序里用 `import` 引入 marker 图片 —— vite 会内联成 base64，`iconPath` 不认；用 `/assets/markers/` 文件路径，config 里有 copy 规则
- ❌ 可见光与红外用同一个云像素阈值 —— 红外亮温整体偏暗，阈值见 `CLOUD_THRESHOLD`
- ❌ Pydantic 响应字段给默认值 —— OpenAPI 会标成可选，前端类型多一层 undefined。可空字段写 `x: float | None = Field(...)` 不带 default，契约要求缺失一律 null 不省略
- ❌ `core/` 里用 TS 构造器参数属性（`constructor(readonly x)`）—— Taro 的 babel 链路不认
- ❌ 公开数据接口要求登录，或让游客自动 `wx.login` —— 游客可看公开数据，只有我的电站与导出需要登录；
  自建电站接口用 `CurrentUserDep`，公开接口用 `OptionalUserDep` 或不取身份，见 09 §4.3
- ❌ 用 emoji 当图标 —— 用 `<Icon name="..."/>`，字形随系统变化不可控
- ❌ 改了 pvlib 模型参数或分档断点不重跑 `scripts/calibrate.py` —— 校准结论要跟着更新到 07 §8.1
- ❌ 改完 UI 不看结果就交付 —— H5 跑 `make shot`，小程序跑 `devtools-shot.sh`，实际查看 PNG
- ❌ 只在 H5 验证小程序样式 —— WXSS 与浏览器 CSS 差异大（选择器容错、`:root`、原生组件层级），必须在开发者工具里看
- ❌ 一页多个 Hero / 四宫格图标四种颜色 / 顶栏放品牌口号 —— 层级原则见 docs/02 §九


## 命令

```bash
# 看页面效果
make preview-bg                      # 后台预览 → http://127.0.0.1:4173
make preview-stop                    # 停掉后台预览
make shot PAGE=home                  # H5 截图自检 → /tmp/enersight-home.png

# 小程序真实效果（需开发者工具已打开项目 + 终端有「屏幕录制」权限）
TARO_APP_LAUNCH=map pnpm --filter @enersight/miniapp build:weapp   # 切启动页，免手点
packages/miniapp/scripts/devtools-shot.sh /tmp/sim.png             # ★ 截模拟器画面

# 小程序
pnpm --filter @enersight/miniapp dev:weapp    # watch，产物 dist/weapp/
pnpm --filter @enersight/miniapp build:weapp

# BFF
uv run fastapi dev                   # 开发，含 /docs
docker build packages/server         # 构建镜像；部署见 deploy/ 与 docs/10
uv run python scripts/calibrate.py   # 指数校准（07 §八），报告写到 docs/reports/
uv run python scripts/import_catalog.py wri|gem <文件>   # 导入公开电站目录（04 §七）
uv run python scripts/replay_flow.py --lat .. --lon .. --start .. --end ..   # 光流回放评估

# 类型同步（改了 Pydantic 模型必跑）
make codegen                         # openapi.json → core/types/
```

`make codegen` 已纳入 CI，生成结果与仓库不一致则构建失败。


## 服务器访问

本机有生产服务器的部署私钥，排查线上问题不用再等 GitHub Actions：

```bash
ssh -i ~/.ssh/huahuadog_deploy    root@39.105.228.11    # 生产 ECS（北京）
ssh -i ~/.ssh/enersight_openmeteo root@192.236.166.152  # 气象自建机（Buffalo NY，HostPapa）
```

两把钥匙都在 2026-09-16 验证可登 root。`~/.ssh/id_ed25519`（默认钥）对两台都被拒，
直接 `ssh root@...` 不通，必须带 `-i`。`known_hosts` 里已有两台的指纹，
保持 `StrictHostKeyChecking=yes`。

气象机的 root **密码**不记在仓库里 —— 它在你的密码管理器里，需要时自己取。
`~/.ssh/enersight_openmeteo` 是为这台机器单独生成的无口令部署钥（2026-09-16 装上），
日常自动化只用它；密钥登录确认可用后建议把 `PasswordAuthentication` 关掉。

北京那台还跑着 new-api、steward、weishen、yingji 与 platform 网关共 17 个容器，
总内存 7.5 GB、可用约 4 GB，磁盘 99 GB 可用 56 GB。**在上面起任何新容器都要设
`mem_limit`**，否则 OOM 会波及别人的服务。不要 `docker prune`、不要重启别人的容器。

Buffalo 那台是我们独占的：Ubuntu 24.04、3 vCPU、3.9 GB 内存、磁盘 57 GB，
只跑 `/opt/open-meteo` 一个 Compose 项目。它出厂没配任何 DNS，
`/etc/systemd/resolved.conf.d/99-dns.conf` 是我们补的（netplan 由 SolusVM 生成，会被覆盖，
所以别改那个）。到 AWS us-west-2 单流 8.2 MB/s —— 北京那台只有 20–36 KB/s，
这就是气象自建不能放北京的原因，见 docs/2026-09-16-open-meteo-self-host.md。

私钥与密码不进仓库、不进日志、不贴进对话；只记路径。生产库口径仍看 docs/10。


## Git

- 提交信息用中文，说明「做了什么」和「为什么」
- 文档与代码同批提交，不要让文档滞后


## 已知的环境坑

搭脚手架时踩到的，都已修复，记下来免得再踩：

| 坑 | 处理 |
| --- | --- |
| Taro 插件与 babel preset 用 require 解析未声明的同级依赖 | `.npmrc` 设 `node-linker=hoisted`；`babel-preset-taro` 还需手动装 `@babel/plugin-proposal-class-properties` |
| Taro 4.0.9 的 peer 是 vite@4 / @vitejs/plugin-react@4 / @babel/core@7 | 版本已 pin，升级前先确认 Taro 的 peer |
| `sass.resource` 会注入到每个 scss | 只放 mixin（无 CSS 输出）；token 由 `global.scss` 引入一次进 app.wxss |
| hoisted 布局把所有 `@types` 拉进隐式作用域 | 两个 tsconfig 都显式写了 `types` |
| `designWidth` 决定 px→rpx 比例 | 取 375（02 的字号是 375pt 基准标注）；换基准改 config，不要改 token 数值 |
| H5 构建需 `src/index.html` 里的 Taro 占位符 | `<script><%= htmlWebpackPlugin.options.script %></script>` 不能删，否则不注入入口 |
| CSS 变量的根选择器两端不同 | token 要给 `page` 与 `:root` **各写一条独立规则**。合写成 `page, :root` 会让小程序端全部变量失效 —— WXSS 对逗号选择器里的非法项是整条作废，且不报错 |
| `navigationStyle: custom` 要自己避开状态栏与胶囊 | 用 `getSafeArea()`（`hooks/useSafeArea.ts`）算 paddingTop / paddingRight，不要写死 |
| `libVersion` 写了不存在的版本 | 工具弹「下载基础库失败」，页面全白。用本地已缓存的版本（`~/Library/Application Support/微信开发者工具/*/WeappVendor/`） |
| map 浮层 | 基础库 3.16.2 起支持同层渲染，浮层用普通 `View`，不用 `cover-view`；底部面板可叠在地图上 |
| 开发者工具每次重编译回到启动页 | `TARO_APP_LAUNCH=<page>` 临时切启动页调试，生产构建不要设 |
| 连续快速重编译会把开发者工具搞挂 | 表现为所有页面空白但 tabBar 在、控制台无报错、连旧代码都白。**先怀疑工具再怀疑代码**：`git stash` 构建上一提交验证；重启工具即恢复 |
| `:active` 在小程序不响应触摸 | 按下态用 `hoverClass="pressed"`，H5 走 `:active`，mixin `pressable` 两者都写 |
| 把唯一的提交按钮放进 `position: fixed` 底栏、再按 `onKeyboardHeightChange` 收起 | **实测**（魅族 21 / Android，微信 8.0.69，基础库 3.17.3）：自建电站表单填完装机容量后按钮消失且不再出现，表单交不出去；模拟器没有真键盘、事件从不触发，永远测不出来。改成放进文档流后真机正常（2026-09-18）。**功能性控件放文档流里，别押在键盘事件上**。事件为何没把状态放开没有取证；fixed 底栏在 iOS 上会被键盘盖住是通行说法，本项目未实测 |
| `ctx.roundRect` 小程序 canvas 行为不一致 | 手画圆角矩形（`arcTo`），见 TrendChart/draw.ts |
| 模拟器不渲染 `ground-overlay` | 调用后既不回调也不拉图，只能真机看。客户端错误可用 `clientLog()` 上报到后端日志（仅连本地后端时生效） |
| `map` 的 `regionchange` 会被贴图本身触发 | 只响应 `causedBy` 为 drag/scale 的 end 事件，否则形成请求循环 |
| 用 `: > log` 截断正在写的日志文件 | 会写出大段空字节。重启进程换新文件 |
| Playwright 截图 `fullPage` 截不全 | Taro 的 `.taro_page` 是内层滚动容器，用足够高的视口代替 fullPage |
| tabBar 图标只接受图片文件 | 不支持 data URI；用 `scripts/gen-tabbar-icons.mjs` 由 SVG 渲染 PNG |
| 两端共用 `outputRoot` 会互相覆盖 | 已按 `dist/${TARO_ENV}` 分目录 |
| Taro 组件 props 不兼容 `exactOptionalPropertyTypes` | miniapp 的 tsconfig 关掉该项，core 保留 |
| 本机起 BFF 用 `.claude/launch.json` 的 `api` 配置（`ENERSIGHT_DEBUG=true uv run uvicorn app.main:app`） | 开发态免登录靠 DEBUG；`.env` 里没有它，且 venv 没装 `fastapi[standard]` CLI，`make dev-server` 的 `fastapi dev` 起不来 |
| 本机系统代理下并发出网偶发 TLS 拒连/超时 | 上游拉取一律带重试退避、限并发（Himawari 瓦片并发 4，Open-Meteo `upstream_retries` 3 次；429/400 不重试）；卫星拿不到按 `unavailable` 处理 |
| H5 构建默认读 `.env.production`，连不上本机后端 | `make shot` / `make preview` 已注入 `API_BASE`（默认 127.0.0.1:8000） |

## 当前状态

**前后端全部打通，18 个接口全部落地并被前端消费，`make check` 全绿（150+ 测试）。**
`src/mocks/` 已删除，9 个页面全部跑真数据（含站点表单页、公开电站选择页）。

| 接口 | 状态 | 备注 |
| --- | --- | --- |
| auth / stations / home / trends / stations/{id} / map/overview / me | ✅ | 游客可看公开数据，我的电站与导出需登录（09 §4.3）；开发态免登录 |
| alerts / alerts/current | ✅ | 预报规则 + 卫星短临两类来源，进程内按站点串行扫描 |
| trends | ✅ | 24h 逐小时 25 点；7d 逐 3 小时 56 点（粒度待产品确认） |
| reports | ✅ | 规则模板兜底；`ANTHROPIC_API_KEY` 配置后走 Claude |
| geo/search / geo/reverse | ✅ | 无腾讯 key 时降级 |
| map/layers | ✅ | 4° 块、1° 网格服务端渲染；云图层用 Himawari 实况 |
| satellite/cloud | ✅ | JMA 瓦片，白天可见光/真彩、夜间红外；光流外推见 07 §四 |
| stations/catalog | ✅ | 公开电站目录运行中 17,836 座（GEM 17,735 + WRI 101；另 928 条 WRI 与 GEM 重复），按月自动同步；导入后统一做数据质量处理（占位坐标、重复、命名，见 04 §七） |
| stations（自建） | ✅ | 站点页「我的站点」，定位 / 地图选点 / 手输经纬度，每用户 10 座，可填出力约束（限电第一层），见 17 |
| predictions/station | ✅ | 单站未来 7 天逐日预测，首页懒加载；`basis` 带模型起报时刻；公开电站与全目录带省级限电参考（限电第二层，界面默认关，见 17 §四） |
| predictions/fleet | ✅ | 全目录未来 7 天 `days[]`，顶层仍是今日；按目标日 + 签发日留档 |
| predictions/station 三模式 + history | ✅ | 默认 ECMWF / ICON / GFS 三家同算，主数字取中位那家，起报统一报三家中最早的；`/history` 读留档给预报演变；我的电站每日 08:30 签发（19 §一、§二） |
| stations/{id}/measured | ✅ | 我的电站记日电量 / 月电量，回算模型同期值，留一法拟合乘性系数，作用于 7 天预测、首页今日、当前功率与累积；指数与留档不乘（19 §三、07 §九） |

已落地的关键实现：
- `server/app/metrics`：pvlib 出力模型与环境指数
- `server/app/satellite`：JMA Himawari 瓦片拉取（缺帧退回上一帧）、Mercator → 等经纬度
  重采样、按太阳高度角切可见光/红外、Farneback 光流云团外推
- `server/app/ai`：Provider 抽象 + 数值一致性校验 + 规则降级
- `core/api` client：401 重登、502 重试、坐标系注入；`core/format` 单位进位
- 小程序 27 个组件、`TrendChart` Canvas 自绘、`useMapLayer` 贴图
- 指数已按 07 §八校准（Perez 散射、容配比 1.2、风电按层插值 + 10% 损耗、分档断点），报告在 `docs/reports/`
- 定时任务：预警扫描 15 分钟、卫星归档 10 分钟、发电累积、报告预生成、地址回填
- 限流 120 次/分钟/token（429 RATE_LIMITED）；Dockerfile + `deploy/` Compose + CI 工作流

### 下一步

**真机验证（阻塞地图与云图的最终确认）：** 开发者工具模拟器不渲染
`ground-overlay`，贴图精度、GCJ-02 对齐、`downloadFile` 域名都只能真机看。
方法见 [05 §6.7](docs/05-architecture.md) 末尾。自建场站的 `wx.getLocation` /
`wx.chooseLocation` 授权流程、真实 `code2session` 与两个微信号的数据隔离也只能真机验。

**需要凭证才能通的：** 微信 AppID/AppSecret（真 `code2session`）、腾讯位置服务 key、
`ANTHROPIC_API_KEY`。都有降级，不阻塞开发。

**目录同步需要配置：** `ENERSIGHT_GEM_CONTACT_NAME / EMAIL / ORG`，定时任务用它提交 GEM
下载表单（每月一次）。不配则不同步，目录停留在最后一次手动导入。

**上线前：**
- 合规办理周期最长，见 [09 §九](docs/09-miniapp-compliance.md)；Himawari 商用授权要法务核实
- 光流外推精度：归档任务已在攒帧，满一个月跑 `scripts/replay_flow.py`
- 多实例部署时扫描锁改数据库行锁，缓存改 Redis（见 05）

**V2：** 卫星源换 NOAA 开放数据 L1b（摆脱 JMA 网页接口无 SLA 的风险）、
7 天趋势粒度（待产品确认）。风场粒子动画已于 2026-09-09 上线，见 docs/15。

**规划中（docs/19）：** M1 三模式与预报演变、M2 实测随手记与订正已完成；
M3 卫星辐照实况改走 JAXA P-Tree 的葵花 SWR（免费，2026-02 起可商用，但注册页旧表述冲突，须先书面确认）；
M4 气溶胶与积灰。明确不做：51 成员集合与分位数、实测文件上传、两个细则考核。

### 环境指数：物理出力比，不是加权评分

```
指数 = 今日预测发电量 / 今日理想发电量 × 100
```

光伏的理想值是晴空辐射 + 25℃ 下的出力，风电用容量因子分档。
四个气象因子全部通过 pvlib 物理模型进入，不需要人工权重。
指数与发电估算**共用同一套实现**，改一处两处都变。

上线前必须按 [07 §八](docs/07-metrics.md) 校准：
与 PVGIS 对账发电量（±15%），并在 5 个气候区 × 365 天上检查分数分布。

### 待产品确认（5 项）

见 [docs/README.md](docs/README.md) 末尾。都不阻塞当前开发。
