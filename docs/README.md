# EnerSight AI 新能源气象遥感分析平台

项目文档包 V1.0

## 产品一句话定位

基于气象数据、卫星遥感数据、地理空间数据与 AI 分析能力的新型能源环境分析平台，帮助用户快速了解某一区域的新能源环境、未来气象变化、云层影响与发电风险。

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [11 项目理解与接手指南](./11-project-understanding.md) | 基于源码的业务链路、模块职责、数据口径、启动步骤、实现缺口与本地验证记录；接手项目建议先读 |
| [01 功能需求文档 · PRD](./01-prd.md) | 产品定位、页面清单与跳转关系、首页 / 地图 / 预警 / 站点 / 站点详情 / AI 报告的功能需求 |
| [02 UI 设计规范 · Design System](./02-design-system.md) | 设计方向、颜色 / 字体 / 间距 / 圆角 / 阴影 / 图标 / 图表规范、布局原则与页面骨架 |
| [03 组件库规范 · Component Library](./03-component-library.md) | 基础 / 数据 / 地图 / 业务组件共 37 个，附组件与页面对照表 |
| [04 数据说明文档 · Data Specification](./04-data-specification.md) | 数据体系、气象 / 卫星数据、地图图层规格、站点运行指标、预警与 AI 分析字段、单位约定 |
| [05 技术架构 · Architecture](./05-architecture.md) | 技术选型、跨端策略、monorepo 结构、BFF 职责、地图图层方案、技术风险 |
| [06 API 接口契约 · API Contract](./06-api-contract.md) | 16 个接口定义、坐标系与单位约定、公共类型、错误码、前端调用约定 |
| [07 指标计算规则 · Metrics](./07-metrics.md) | 新能源环境指数算法、发电估算、环比、短临外推、预警阈值、参数配置清单 |
| [08 AI 分析设计 · AI Design](./08-ai-design.md) | AI 三类输出、模型选型与合规、调用时机与缓存、结构化输出、降级、成本估算 |
| [09 小程序合规清单 · Compliance](./09-miniapp-compliance.md) | 主体类目、备案、隐私协议、位置权限、生成式 AI、内容安全、上线检查清单 |
| [10-deployment.md](./10-deployment.md) | 部署 | 镜像、Compose、环境变量、升级回滚 SOP、上线清单 |

## 设计稿

设计稿位于 `packages/ui/`，本文档包全部内容已与之对齐。

| 文件 | 页面 |
| --- | --- |
| `首页.png` | 首页 |
| `地图.png` | 地图 |
| `预警.png` | 预警中心 |
| `站点.png` | 我的站点 |
| `站点2.png` | 站点详情 |
| `站点3.png` | AI 分析报告 |

「我的」页面已在底部导航占位，尚未出设计稿。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 小程序 | Taro 4 + React 18 + TypeScript |
| 状态 | Zustand |
| 跨端复用 | `packages/core`（类型 codegen、API client、格式化） |
| BFF | Python 3.12 + FastAPI（pvlib / OpenCV / numpy） |
| 地图图层 | 服务端渲染 PNG + `<map>` ground-overlay |

端演进：微信小程序（V1）→ App（V2，UI 重写，复用 core）。

## 一级导航

```
首页  地图  预警  站点  我的
```

## 设计基线速查

| 项 | 值 |
| --- | --- |
| Primary Blue | `#1677FF` |
| Energy Green | `#16A34A` |
| Warning Orange | `#F59E0B` |
| Danger Red | `#EF4444` |
| Background | `#F5F9FC` |
| 页面边距 / 卡片间距 | 16px / 12px |
| 圆角（卡片 / 大模块 / 按钮） | 16px / 24px / 16px |

## 待确认事项

| # | 事项 | 文档 |
| --- | --- | --- |
| 1 | AI 模型选型（境内备案模型 vs Claude） | [08 §2.2](./08-ai-design.md) |
| 2 | 地图页图例与激活图层不对应 | [04 §四](./04-data-specification.md) |
| 3 | 「我的」页面需求与设计稿 | [01 §二](./01-prd.md) |
| 4 | 站点是否支持多用户共享 | [06 §十五](./06-api-contract.md) |
| 5 | 预警是否接微信订阅消息推送 | [06 §十五](./06-api-contract.md) |

> 设计稿 `packages/ui/` 中的数值均为视觉示意值，不作为算法校准依据。
> 指数与发电估算的校准方法见 [07 §八](./07-metrics.md)。

> 坐标系约定：存储与计算统一 **WGS84**，仅在传给小程序 `<map>` 前转 **GCJ-02**。
> 详见 [05 §6.6](./05-architecture.md)。
