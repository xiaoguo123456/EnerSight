# EnerSight — AI 新能源气象遥感分析平台

微信小程序（V1）→ App（V2）。基于气象、卫星遥感、地理空间数据与 AI 分析的
新能源电站环境分析平台。

**当前阶段：文档完成，代码未开始。** 详见本文末尾「当前状态」。


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

设计稿在 `packages/ui/`，6 张 PNG，对应 6 个页面。**实现页面前先看图。**


## 技术栈

| 层 | 选型 |
| --- | --- |
| 小程序 | Taro 4 + React 18 + TypeScript |
| 状态 | Zustand |
| 样式 | Sass + CSS 变量，尺寸用 `rpx` |
| 跨端复用 | `packages/core` — 类型（codegen）、API client、格式化 |
| BFF | Python 3.12 + FastAPI |
| 科学计算 | pvlib（太阳位置/辐射/光伏）、OpenCV（光流）、numpy、Pillow |
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
- ❌ 拿设计稿里的数值对账 —— `packages/ui/` 中所有数字都是视觉示意值
- ❌ 客户端硬编码图层色阶 —— 图例由 `/v1/map/layers` 下发
- ❌ 客户端用自己请求的 bbox 贴图 —— 用服务端返回的对齐后 `bounds`
- ❌ 实现储能站相关功能 —— V1 不做，设计稿里有但已明确排除
- ❌ 用户请求时同步调 AI —— 全部预生成 + 缓存，见 08 §三


## 命令

```bash
# 小程序
pnpm --filter miniapp dev:weapp      # 开发，产物在 dist/，用开发者工具导入
pnpm --filter miniapp build:weapp    # 构建

# BFF
uv run fastapi dev                   # 开发，含 /docs
docker build packages/server         # 构建镜像

# 类型同步（改了 Pydantic 模型必跑）
make codegen                         # openapi.json → core/types/
```

`make codegen` 已纳入 CI，生成结果与仓库不一致则构建失败。


## Git

- 提交信息用中文，说明「做了什么」和「为什么」
- 文档与代码同批提交，不要让文档滞后


## 当前状态

**文档齐了，代码未起，有两类事情卡在前面。**

### 阻塞项：地图 spike 未做

[05 §6.7](docs/05-architecture.md) 列了 7 项验证，整套地图方案建立在其上。
其中第 6 项（Himawari 数据通道）决定卫星云图功能是否成立，应第一天确认。
spike 结论出来前不要大规模写地图页代码。

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

见 [docs/README.md](docs/README.md) 末尾。都不阻塞脚手架与 core 层开发。

### 可以立即开始（不依赖以上）

- 合规前置项办理 —— 周期最长，见 [09 §九](docs/09-miniapp-compliance.md)
- 工程脚手架、`core/format`、设计 token
- BFF 骨架 + Open-Meteo 接入 + `app/metrics/`（pvlib）
- 非地图页面的静态组件
