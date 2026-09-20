# 晴川观象测试与生产部署

测试环境配置、前端构建命令和回滚方式见 [部署目录说明](../deploy/README.md)。

本文生产部分对应 `deploy/` 和 `.github/workflows/prod.yml`。参考 `new-api-deploy` 的独立服务边界、固定镜像摘要，以及 `huahuadog` 的 GitHub Actions → ACR → ECS 发布流程。

## 资源与访问路径

| 项目 | 配置 |
| --- | --- |
| GitHub | 私有仓库 `xiaoguo123456/EnerSight` |
| 公网 API | `https://platform.qhzhiyin.com/enersight` |
| 生产 ECS | `39.105.228.11` |
| API 部署目录 | `/opt/enersight` |
| API 端口 | `127.0.0.1:8001` → 容器 `8000` |
| 共用网关目录 | `/opt/platform-gateway`，端口 `8089` |
| RDS 实例 | `pgm-2zea7vqeqsie080i`，PostgreSQL 16，北京 |
| 独立数据库 / 账号 | `enersight_prod` / `enersight_prod_app` |
| ACR 镜像仓库 | `weishen/enersight-backend`，私有 |
| GitHub 推送地址 | `prod-weishen-registry.cn-beijing.cr.aliyuncs.com` |
| ECS 内网拉取地址 | `prod-weishen-registry-vpc.cn-beijing.cr.aliyuncs.com` |

以上资源已于 2026-09-09 上线，公网数据库就绪检查通过。

`platform.qhzhiyin.com` 的 ALB HTTPS 规则 `rule-maeoh4pk2xiy5gopp2`（优先级 4000）已指向 `sgp-fngul7xm6tjm4xztmi`（platform-gateway-prod，端口 8089）。原服务器组 `sgp-2bta4zqj9hwh5hjhrd`（生产-new-api，端口 3001）保留用于回退。根路径仍由 New API 提供。

实际流量：ALB HTTPS → 独立 platform 网关 → `/enersight/` 转至 EnerSight，其余路径仍转至 New API。网关不复用或覆盖 huahuadog、steward 的 Nginx。今后每个服务使用独立前缀和一个 `services/*.conf` 文件；业务工作流只更新自身容器，不能覆盖共享网关配置。

## 数据库首次准备

在现有实例中创建 `enersight_prod`，UTF8 编码；新建专属普通账号 `enersight_prod_app`，只授予该库必需权限。迁移账号需要在该库创建表、索引和序列的权限。不要复用其他业务的数据库或高权限账号运行应用，不要开放公网数据库地址。

RDS 当前实例最多 50 个连接，配置每个 EnerSight 进程常驻 2、溢出 1 个连接。Alembic 迁移使用单独短连接。保持单个 Uvicorn 进程和单个调度器；多个进程会各自建立连接池和调度任务，扩容前重新核算连接预算。

数据库首次开通后运行镜像入口中的 `alembic upgrade head`。部署不执行 `drop`、`downgrade` 或 `create_all`。破坏性迁移必须另行设计备份和兼容升级，不依赖应用镜像回滚恢复数据。

## 首次配置服务器

在 `/opt/enersight` 放入 `docker-compose.yml`、`scripts/`，复制 `.env.example` 为 `.env` 后填写真实数据库 URL 和随机 JWT 密钥，权限设为 `600`。密码必须进行 URL 编码，Alembic 已处理 `%` 插值兼容问题。

生产关键配置：

```dotenv
ENERSIGHT_DEBUG=false
ENERSIGHT_ROOT_PATH=/enersight
ENERSIGHT_DB_POOL_SIZE=2
ENERSIGHT_DB_MAX_OVERFLOW=1
ENERSIGHT_WX_APPID=wx61a4a74de8bc126b
```

AppSecret 只放服务器 `.env`，禁止写进小程序、Git、Actions 日志。微信登录已接入 `code2session` 并验证异常响应、过期 code 和上游超时；配置 AppSecret 后还需真机联调。缺少配置时生产不会使用开发用户兜底。

应用缓存和归档保存在 `/opt/enersight/data`，UID/GID 为 `10001`。部署预检负责创建目录。容器限制 1536 MB 内存、1.5 CPU、256 个进程/线程，日志最多 3 × 10 MB。单机科学计算任务量增长时须监控峰值内存、CPU 和数据库等待时间。

服务器须事先登录 ACR 内网仓库。现有部署凭据的复用必须得到用户对新仓库 GitHub Secrets 的明确授权；不能从 GitHub 读出其他仓库 Secrets。

## 气象出网

后端的 Open-Meteo 点预报、历史小时资料、云量降级网格和模型元数据统一经
`app/providers/weather_transport.weather_get` 出网：默认直连；开启代理池（下节）时经代理池请求，
失败不退回直连。直连受 Open-Meteo 对服务器出口 IP 的额度限制。原生地图栅格
（`openmeteo.s3.amazonaws.com`）下载不经此入口。

这个入口同时是**唯一**处理预算与限流归属的地方（2026-09-16）：

- 项目总预算 `shared.take` 在这里统一计一次，调用方不再各自计费。此前 Provider、全目录、
  模型复核各计各的，而云量降级网格（每个 4° 块 25 个坐标、每小时刷新）整条链路从未计过费。
- 429 的冷却时长按上游正文里的窗口决定：`Minutely / Hourly / Daily API request limit exceeded`
  分别对应 60 / 3,600 / 86,400 秒，并把原因写进日志（`气象上游限流 scope=... reason=...`）。
  以前一律按 60 秒恢复，日额度耗尽时等于每分钟再去撞一次，且事后查不出撞的是哪个桶。
- 走代理池时归属由池按出口处理，Provider 与后台任务**不再**各设一次全局冷却，
  否则一个出口的分钟限流会停掉整池。

自建 Open-Meteo 以摆脱额度与商用限制的评估见[自建评估](./2026-09-16-open-meteo-self-host.md)，运行手册见 `deploy/open-meteo/`。2026-09-16 已在生产 ECS 上实测一轮：接口与 15 个字段完全正确，但北京到 us-west-2 单流只有 20–36 KB/s、16 并发 294 KB/s，20 个坐标的全字段请求 1,800 秒超时未取完，**这台机器不可用，瓶颈是带宽不是内存**（容器峰值 60 MB）。验证容器在 `/opt/open-meteo`，已 stop、未接流量、未改应用配置。另实测到数据桶按变量分别更新、同一响应会混两个起报批次（同 chunk 各变量 Last-Modified 跨度约 30 分钟），换机器也要处理。

自建实例已于同日部署在 Buffalo 的 `192.236.166.152`（`/opt/open-meteo`，仅监听回环 8090）：到 us-west-2 单流 8.2 MB/s，全冷单坐标 5.6 秒、热缓存 12 ms、100 坐标一次 334 秒，15 个字段与官方逐点最大绝对差 0.0000。**尚未对外暴露、尚未切流量**，暴露方案与批次错位处理见评估文档第九节。

**2026-09-15 移除 Cloudflare 气象转发**：按用户要求删除 `deploy/weather-relay` Worker 代码与
`ENERSIGHT_WEATHER_RELAY_BASE / _TOKEN` 配置项。Cloudflare 控制台上的 `enersight-weather-test`、
`enersight-weather-prod` Worker 及 `weather-test.weishenai.cn`、`weather.weishenai.cn` 域名需另行停用或删除。
服务器 `.env` 里遗留的两个转发变量可以删掉；Compose 以环境变量注入，遗留不影响启动，
但本地 `packages/server/.env` 文件里若还有这两项会因未知字段启动失败。

移除前的切换记录：2026-09-15 按用户明确要求，两套环境关闭气象转发，清空
`ENERSIGHT_WEATHER_RELAY_BASE` 后用原发布镜像重建 API，恢复直连 Open-Meteo；代理可用性实验仅在测试服务器进行。
直连仍受 Open-Meteo 对服务器出口 IP 的额度限制，不代表取消上游限额。
生产配置备份为服务器 `/opt/enersight/.env.before-direct-20260915T105107`（权限 600）；
原镜像 `17290a7aa432` 保持不变。切换后容器健康，首页及此前失败的两个公开场站详情
均返回 200；公网 `/ready` 和场站详情返回 200，详情天气与趋势均非空。
测试配置备份为 `/opt/enersight-test/.env.before-direct-20260915T111209`（权限 600），
原镜像 `3e6cb264e0a8` 保持不变。测试容器健康，运行配置确认转发地址为空；测试公网
`/ready`、首页和两个公开场站详情均返回 200，详情天气与趋势均非空。

`.env` 由 Compose 在创建容器时读入，修改后需重建：`set -a && . ./.release.env && set +a && docker compose up -d --no-deps api`。

## 气象代理池

策略与验收口径见[气象代理池维护策略](./2026-09-15-weather-proxy-pool-strategy.md)（2026-09-16 已实施，
未部署）。开关 `ENERSIGHT_WEATHER_PROXY_POOL_ENABLED`，默认关闭。通过 HTTPS CONNECT 获取公开气象
JSON，始终验证目标证书，不发送业务身份信息；统一入口仍为 `weather_transport.weather_get`，
天气缓存、坐标预算与数据时效不变。

**按实测出口记账，不按代理地址。** 实测 `202.141.161.52:10808` 的出口是 `134.185.103.14`；
按代理地址记额度会把同一个出口算成两份。因此节点入池要用两次独立连接确认出口一致，
同出口的多个节点共用一本额度账，出口变化时旧账不清零。

**候选数不等于额度宽度。** 100 是候选节点上限；真正决定宽度的是实测独立出口数，日志里是
`代理池 ... ready_exits=N`，低于 `MIN_EXITS` 会单独记一条 `代理池可用出口不足` 的降级事件。
免费列表的通过率通常只有一到三成，所以候选要远多于目标出口。

### 可调参数

全部前缀 `ENERSIGHT_`，默认值见 `app/config.py` 的 `weather_proxy_*`。

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `WEATHER_PROXY_POOL_ENABLED` | false | 总开关；关闭即直连 |
| `WEATHER_PROXY_CANDIDATES` | 500 | 候选节点上限。实测通过率约 5%，要 20 个出口就得按这个比例备候选 |
| `WEATHER_PROXY_TARGET_EXITS` / `_MIN_EXITS` | 20 / 5 | 可用独立出口的目标与低水位线 |
| `WEATHER_PROXY_NODES_PER_EXIT` | 2 | 同出口保留几个节点作连接备用 |
| `WEATHER_PROXY_EXIT_UNITS_PER_MINUTE/_HOUR/_DAY` | 300 / 2,000 / 5,000 | 每出口自设保守阈值，按坐标数计，不是上游承诺额度 |
| `WEATHER_PROXY_SLOTS` / `_BACKGROUND_SLOTS` | 4 / 2 | 全局并发与后台任务名额（每出口并发恒为 1） |
| `WEATHER_PROXY_ATTEMPTS` / `_DEADLINE` | 2 / 14 秒 | 一个用户请求最多几次实际访问、总时间 |
| `WEATHER_PROXY_REQUEST_TIMEOUT` / `_PROBE_TIMEOUT` / `_EXIT_TIMEOUT` | 6 / 4 / 4 秒 | 单次传输、气象探测、出口探测超时 |
| `WEATHER_PROXY_REFRESH_SECONDS` / `_TOPUP_SECONDS` | 900 / 120 | 列表刷新间隔、低水位补探的最小间隔 |
| `WEATHER_PROXY_PROBE_PER_ROUND` / `_PROBE_CONCURRENCY` | 100 / 8 | 每轮检测的候选数与并发。跟着候选数走：500 个候选按 20 / 2 探，探完一遍要两个多小时，掉线的出口补不回来 |
| `WEATHER_PROXY_EXIT_TTL` / `_HEALTH_TTL` | 900 / 1,800 秒 | 出口观测有效期、气象健康有效期 |
| `WEATHER_PROXY_DROP_STREAK` | 5 | 连续失败几次退出活跃池 |
| `WEATHER_PROXY_LEDGER_RECOVER_SECONDS` | 3,600 | 账本损坏或缺失时该出口的暂停时长 |
| `WEATHER_PROXY_DIRECT_FALLBACK` | true | 池子给不出出口时退回直连 |
| `WEATHER_PROXY_DIRECT_UNITS_PER_MINUTE/_HOUR/_DAY` | 480 / 4,000 / 8,000 | 直连出口额度，免费层上限各留两成余量 |
| `WEATHER_PROXY_SOURCE` / `_EXIT_PROBE` | 空 | 覆盖免费列表与出口探测地址，留空用代码默认（见下「源与探测点的选型」） |
| `WEATHER_PROXY_SEED_MAX_AGE` | 7 天 | 种子文件多久算过期 |

调用方那边还有一个相关旋钮：`FLEET_COORDS_PER_REQUEST`（默认 100）必须小于
`EXIT_UNITS_PER_MINUTE`，否则单批成本超过出口分钟额度，池会直接报「请拆批」而不是静默超发。

### 源与探测点的选型（2026-09-16 测试机实测）

默认值不是随便挑的，是在测试机（北京 ECS）上按可达性选出来的：

| 列表源 | 结果 |
| --- | --- |
| `api.proxyscrape.com`（原默认） | **直连超时拉不到**，池子因此永远是空的 |
| `proxylist.geonode.com`（现默认） | 200 / 0.75 s，JSON 带 `ip`/`port`/`lastChecked` |
| `cdn.jsdelivr.net` 的 TheSpeedX/PROXY-List 纯文本镜像 | 200 / 1.55 s，2,973 行 `ip:port` |

解析器三种格式都吃（ProxyScrape 的 `proxies[]`、geonode 的 `data[]`、纯文本每行 `ip:port`），
换源只改配置。纯文本镜像有几千行且长期不变，入池前会打乱顺序 —— 固定取前 N 个等于所有人
都在用同一批死代理。

出口探测点要选**代理能访问到**的，不是我们能访问到的。同一批代理实测：

| 探测点 | 通过率 |
| --- | --- |
| `api.ipify.org`（原默认） | 3/5 |
| `checkip.amazonaws.com`（现默认）、`icanhazip.com`、`cloudflare.com/cdn-cgi/trace`、`ifconfig.me/ip` | 5/5 |

用 ipify 会把四成本来能连上游的代理挡在池外 —— 12 个种子代理里 5 个能连 Open-Meteo，
只有 2 个能过 ipify。出口解析同时支持纯文本 IP、JSON 与 `ip=` 这种 key=value 行。

### 运行时行为

- **抓列表与探候选是两个节奏。** 按 `REFRESH_SECONDS` 增量拉列表（附少量随机延迟）；
  出口数低于目标且手上还有没验过的候选时，每 `TOPUP_SECONDS` 只跑一轮探测、**不重抓列表** ——
  候选还没探完就再抓一遍，等于拿新候选挤掉还没验过的老候选，出口数反而上不来。
  探测本身不花气象额度：拿不到出口的候选根本不会去打 `meta.json`，代价只有两次出口探测超时。列表优先直连，失败后最多用两个已有节点获取同一 HTTPS 列表；全失败则保留
  并复检已有条目，五分钟后再试。首次部署可写入 `data/weather-proxy-seeds.json`
  （公网代理 URL 数组、一天内的官方列表快照）；候选必须实测通过才能承接业务。
- 入池两步：先查实际出口（两次一致才认，动态出口不入池），再取一次模型元数据验证目标可用；
  元数据探测计入项目预算与该出口额度，出口探测不计。
- 请求先按出口「最近最少使用」分散，再在其中选稳定且较快的节点，避免全部流量压到最快的一个。
  仅传输错误可换**另一个出口**再试一次，第二次也计预算；超出 `DEADLINE` 抛业务异常，
  阻止外层重试叠加。
- 429 按窗口归属：认得出分钟/小时/日就只停该出口及其关联节点，其余出口照常服务；
  认不出则整池按共享冷却处理，并把原文记进日志，不靠换 IP 推断已解除限制。
  **分钟**限流允许换一个有余额的出口再试一次（仍受 `ATTEMPTS` 约束）；小时与日限流不换，
  换了也救不了当前这次请求，只会多烧一份额度。
- 网络失败按次数指数退避（60 秒起、最多 1 小时）；只接受可解析的 JSON，Provider 继续校验业务字段。
- **池子给不出出口时退回直连**（`WEATHER_PROXY_DIRECT_FALLBACK`，默认开）。直连在账本里
  也是一个出口（`DIRECT`），额度 `DIRECT_UNITS_PER_MINUTE/_HOUR/_DAY` 默认 480 / 4,000 / 8,000，
  对应免费层 600 / 5,000 / 10,000 各留两成余量；打满就不再兜底。只有空池、全冷却、无余额或
  几次尝试全是传输错误才兜底，上游已回答的 429 / 坏响应 / 400 都不兜底，共享预算冷却期间也不兜底。
  每次兜底记一条 `气象代理池不可用，本次退回直连：<原因>`。关掉它则池子一空整个气象层不可用 ——
  首次部署就是这么炸的，见下面的记录。
- 空池的错误信息自带计数：`候选 N、可用出口 N、冷却中 N；上次列表刷新 N 秒前成功/失败`，
  只有计数不含地址，没有 SSH 也能判断池子状态。
- 状态每 30 秒原子写入 `data/weather-proxies.json`（`version: 2`），包含节点统计、出口关联、
  三个窗口的用量明细与冷却。重启按最后一次落盘时间补一个分钟窗口的等待；账本缺失或损坏的
  出口先暂停 `LEDGER_RECOVER_SECONDS`，不假定小时与日额度为零使用。日志只有代理地址、出口、
  路径、状态、耗时与汇总，不含业务参数或凭据。

### 试验记录（2026-09-15，旧实现）

以下为按出口分账之前的试验，结论仍然成立（分钟限额按出口独立），实现细节已被上面一节取代。
当时的行为是：最多 40 个候选、目标 20 个可用，429 执行整池冷却。

#### 首次上线验证（2026-09-15）

- 测试镜像：`enersight-test-weather-proxy:trial-20260915-v1`，镜像 ID
  `sha256:fa42ed7f63ce0af9baf9012d0397bfe3870808e08012d0da92818edcea6b9029`。
  基于原测试 `3e6cb264e0a8`，只有配置、生命周期、统一出口与代理池四个 Python 文件变化。
  镜像仅存在测试主机；后续常规测试发布会覆盖本次试验镜像。
- 预热检测 20 个候选，4 个可用；启动后增量检测，状态快照为 40 个候选、12 个近期可用。
  这是瞬时可用数，不代表长期成功率。官方列表直连失败，通过已有代理更新成功。
- 通过代理请求单坐标预报返回 200 / 96 个点；测试公网健康检查、首页、两个场站详情均为
  200，天气与趋势非空。应用日志确认实际 `/v1/forecast` 走代理，三次成功耗时
  1.33、1.33、1.57 秒，CF 转发地址为空。
- 本地全量 502 项测试通过；本机 Conda 的 PROJ 路径会污染地图用例，验证命令为
  `env -u PROJ_LIB -u PROJ_DATA .venv/bin/pytest -q`。故障注入覆盖最多两次请求、预算计数、
  429 跨重启冷却、空池不直连、证书验证配置、候选过滤、异常响应与列表更新降级。
- 恢复原测试镜像与直连配置（在测试服务器执行，持有部署锁）：

```sh
cd /opt/enersight-test
(
  flock -n 9 || exit 1
  cp -p .env.before-proxy-20260915T121347 .env
  cp -p .release.env.before-proxy-20260915T121347 .release.env
  docker compose --env-file .release.env up -d --no-deps --pull never api
) 9>.deploy.lock
```

#### 单代理分钟限流实测（2026-09-15 13:54，北京时间）

使用测试容器内独立诊断进程，固定代理 `153.80.240.2:8080`，不轮换，不修改应用自身
480 单位/分钟的预算。测试单字段 `hourly=temperature_2m`、一天预报、`best_match`，
每批最多 100 个坐标，总提交上限 650；异常立即停止。

| HTTP 请求 | 本次坐标数 | 累计提交坐标数 | 上游状态 |
| --- | --- | --- | --- |
| 1～6 | 每次 100 | 600 | 全部 200，数据完整 |
| 7 | 50 | 650 | 429 |

全部请求在同一个自然分钟内，总耗时 6.55 秒。上游错误原文为
`Minutely API request limit exceeded. Please try again in one minute.`，没有 `Retry-After`。
试验后观测到出口 IP 为 `153.80.240.2`；试验前出口查询失败，不能据此声称逐请求验证了
出口一致性，但整个试验始终使用同一代理地址，未轮换。

结论：该代理仍受分钟限流；7 次 HTTP 请求就能触发，不能把 HTTP 请求数当作坐标额度。
本次定位到「600 个坐标成功、随后 50 个坐标的整批被拒绝」，没有逐个探测第 601 个。
该结果只代表此代理、此参数与此时段，不验证小时或日额度，也不代表整个代理池没有限制。
13:55:14 通过同一代理复测一个坐标，返回 200 / 24 个小时数据点；测试服务 `/ready`
同时返回 200。分钟窗口后恢复，未修改应用限流或代理池配置。

#### 不同出口的分钟限流对照（2026-09-15 14:27，北京时间）

在测试容器内使用独立诊断进程，先查询实际出口，确认不同后再开始；同一自然分钟中，
A 最多提交 650 个坐标，首次 429 即停止，随后 B 只请求一个坐标。字段与上一实验相同。

| 代理 | 代理地址 | 试验前后观测出口 IP | 天气结果 |
| --- | --- | --- | --- |
| A | `103.237.102.191:11111` | `103.237.102.191` | 前 600 坐标成功，随后 50 坐标整批返回分钟 429 |
| B | `202.141.161.52:10808` | `134.185.103.14` | A 返回 429 后立即请求一个坐标，200 / 24 个小时数据点 |

A 第 7 次请求开始于 14:27:04.753，0.29 秒后返回分钟限流；B 请求开始于
14:27:05.044，同一分钟返回有效天气数据。两个代理在试验前后的出口查询结果各自一致。
结果支持这两个出口的分钟额度独立，不能推广为所有代理无限额；出口通过 ipify 观测，
未读取 Open-Meteo 服务端连接日志。代理地址与出口 IP 不一定相同。

本次未改动应用配置。测试应用现有实现仍会在 429 后执行全局冷却；此对照使用独立进程，
不代表应用当前会在 429 后立即改用另一个出口。

来源：[ProxyScrape 免费列表](https://proxyscrape.com/free-proxy-list)、
[HTTPX CONNECT 代理](https://www.python-httpx.org/advanced/proxies/)。

## 测试环境自建气象试验（2026-09-16）

按用户决定，测试环境切到 Buffalo 的自建实例，并**关掉代理池**
（`ENERSIGHT_WEATHER_PROXY_POOL_ENABLED=false`）—— 自建与代理池是互替的两条策略，
叠起来一次故障会同时动用两套限流账本。兜底也不经代理池，直连官方。

试验镜像 `enersight-test-weather-fallback:trial-20260916-v1`
（`sha256:8e2a947d68ad94203629244530c2e4727fb09da3f0633d131042c602a3f09f13`），
基于当时的测试镜像 `2c68d1293bd2` 派生，只覆盖本次涉及的 7 个 Python 文件，
不升级依赖、不迁移数据库。镜像只存在测试主机；之后常规测试发布会覆盖它。
该提交与 main 的 Python 无差异（`bc91788` 只改文档），派生是安全的。

测试服务器 `.env` 新增：

```dotenv
ENERSIGHT_OPEN_METEO_BASE=http://192.236.166.152:8090/v1
ENERSIGHT_OPEN_METEO_ARCHIVE_BASE=http://192.236.166.152:8090/v1
ENERSIGHT_OPEN_METEO_META_BASE=https://openmeteo.s3.amazonaws.com/data
ENERSIGHT_OPEN_METEO_FALLBACK_BASE=https://api.open-meteo.com/v1
```

Buffalo 侧的 `DOCKER-USER` 白名单已加测试机出口 `47.93.60.25`
（与生产 `39.105.228.11` 并列，三家查询确认出口即公网 IP）。

回滚（在测试服务器执行，持有部署锁）：

```sh
cd /opt/enersight-test
(
  flock -n 9 || exit 1
  cp -p .env.before-selfhost-20260916T233704 .env
  cp -p .release.env.before-selfhost-20260916T233704 .release.env
  docker compose --env-file .release.env up -d --no-deps --pull never api
) 9>.deploy.lock
```

全目录压测前把当天快照与气象缓存移到
`/opt/enersight-test/roundtest-backup-20260916T234119/`（170 MB，可还原），
以强制一轮全冷拉取；`roundtest2-backup/` 是跨日那轮的半成品。两个目录都可以删。

压测结果：一轮全冷 **40 分钟**跑完，`status=ready`、`covered 8218/8218`、`failed 0`、
整轮 0 次 429 与 0 次主源失败、兜底一次未用。详见
[自建评估第十一节](./2026-09-16-open-meteo-self-host.md)。
压测用的 `ENERSIGHT_FLEET_REFRESH_HOUR=0` 已删除，测试环境除气象源外与生产行为一致。

## 生产气象切自建（2026-09-17）

生产分两步切：先发镜像 `295725a15700@sha256:88e9f1b1…`（此时 `.env` 还没有
`ENERSIGHT_OPEN_METEO_*`，行为不变），确认健康后再追加四行并重建 api 容器。
`.env` 备份为 `/opt/enersight/.env.before-selfhost-20260917T083056`（权限 600）。

```dotenv
ENERSIGHT_OPEN_METEO_BASE=http://192.236.166.152:8090/v1
ENERSIGHT_OPEN_METEO_ARCHIVE_BASE=http://192.236.166.152:8090/v1
ENERSIGHT_OPEN_METEO_META_BASE=https://openmeteo.s3.amazonaws.com/data
ENERSIGHT_OPEN_METEO_FALLBACK_BASE=https://api.open-meteo.com/v1
```

回滚：删掉这四行再重建 api 容器即可回到官方直连，气象缓存与落盘格式不变、不用清数据。
镜像回滚另有 `.previous-release.env`（上一版 `17290a7aa432`）。

切换后半小时验证：公网 8 个接口全部 200（首页 0.27 s、详情 0.11 s），
场站详情天气完整且 `index.score=60.9`，全目录轮次 `ready / 8218 of 8218 / failed 0`，
`primary_requests 59`、`fallback_requests 0`、`breaker_trips 0`，0 次 429。
全目录跑着的时候生产首页稳定 0.22 秒，1.5 核没有被挤爆。
详见 [自建评估第十二节](./2026-09-16-open-meteo-self-host.md)。

Buffalo 侧内存限额已从 2g 提到 3g（两轮跑完到 1.773 GiB / 2 GiB，贴得太近）。

**已修**（见评估文档第十四节）：主源单独超时 `weather_primary_timeout`（30 秒）、
自建请求不再可能被路由进代理池、`primary_requests` 覆盖后台批量、
新批次沉降窗口 `weather_batch_settle_seconds`（1800 秒，只对自建生效）、
`fleet_refresh_hour` 只在用官方额度时才等。

**未解决**：浏览公开电站时打开一座没缓存的要 5–6 秒（官方是 0.73 秒）。
成本是「每个新坐标」的，邻近坐标沾不到热，粗网格预热覆盖不了 36,105 个格点。
自己的电站不受影响（`scan_alerts` 每 15 分钟遍历全部 `Station` 保持热）。
三条可选路线见评估文档第十三节第 4 小节，需要定。

Buffalo 内存限额已从 3g 提到 3900m（机器共 3,915 MB、另有 2 GB swap，上面只跑这一个服务）。
注意限额贴到物理内存意味着它不再保护主机 —— 真涨上去是靠 swap 兜，不是靠 cgroup 拦。

## GitHub Secrets 与发布

仓库设置中配置下列 Secrets，均不进入代码：

| Secret | 用途 |
| --- | --- |
| `ACR_REGISTRY` | GitHub 推送 ACR 地址 |
| `ACR_PULL_REGISTRY` | ECS 内网拉取地址 |
| `ACR_NAMESPACE` | `weishen` |
| `ACR_USERNAME` / `ACR_PASSWORD` | 镜像仓库登录凭据 |
| `PROD_SSH_HOST` | 生产 ECS 地址 |
| `SSH_KEY` | 已授权的部署 SSH 私钥 |
| `SSH_KNOWN_HOSTS` | 事先核验的生产主机公钥，禁止运行时盲目信任 |

在 Actions → **生产部署** → Run workflow，填入已经合入 `main` 的完整 40 位提交 SHA。工作流检查提交属于 main 历史，然后执行：

1. codegen 一致性、TypeScript 和 Ruff 检查、单元测试。
2. PostgreSQL 16 临时测试库上执行全量迁移、`alembic check`，验证逐日发电重复写入、实测保护和报告 JSON 更新，测试事务回滚。
3. 构建 `linux/amd64` 镜像并推送 ACR，标签为提交前 12 位；部署时附带构建输出的 SHA256 摘要。
4. 上传自身 Compose 与脚本，通过 SSH 拉镜像和更新 API。
5. 等待 Docker 健康状态以及 `/ready` 数据库就绪检查，成功后将镜像写入 `.release.env`，上一版本写入 `.previous-release.env`。

发布使用 GitHub concurrency 和服务器 `flock` 双重串行。没有 `latest` 标签，也不执行全局 `docker prune`、其他项目 `compose down` 或全局容器重启。

日常手工查看：

```sh
cd /opt/enersight
# Compose 需读取已部署镜像元数据；应用环境变量由 env_file 的 .env 加载。
docker compose --env-file .release.env ps
curl -fsS http://127.0.0.1:8001/ready
```

失败会尝试恢复上一健康镜像；首次发布没有历史版本可回退。回滚不恢复数据库或旧 Compose 文件，涉及配置兼容变化时应同时核对版本中的部署配置。

```sh
cd /opt/enersight
bash scripts/rollback.sh
```

## 共用 Nginx 网关

首次网关安装使用 `deploy/gateway/`。将 `.env` 中 `PLATFORM_NGINX_IMAGE` 固定为经过验证的 Nginx 镜像摘要，目录和服务名独立。使用 host 网络监听 `8089`，业务 API 的 `8001` 仅监听回环地址。

网关保留 New API 的流式响应、长连接和 WebSocket；ALB 提供 HTTPS 协议头。`/enersight/` 的代理保留路径前缀（`proxy_pass http://127.0.0.1:8001` 不带末尾斜杠），后端 `root_path=/enersight` 统一处理接口与挂载的静态图片路径；剥离前缀会使静态图片返回 404。修改共享网关时，先备份目标配置，验证 `nginx -t`，再 reload；不通过业务发布流覆盖全部配置。

切换前：验证网关本机 `/gateway-health`、`/enersight/ready` 和根路径 New API。ALB 新建独立服务器组，目标为同一 ECS 的 `8089`，检查 VPC / 安全组的最小必要连通性。仅在健康后将 platform 域名规则从原 New API 组切到新网关组，保留旧组用于回退，不改其他域名规则。

切换后：验证公网 `/enersight/ready`、根路径 New API 页面、流式请求及其他生产域名。失败时先把 ALB 规则恢复为旧组。检查 HTTPS 证书续期，网关自身不申请重复证书。

## 小程序上线还需完成

小程序生产 API 地址已改为 `https://platform.qhzhiyin.com/enersight`。微信后台 request / downloadFile 合法域名填写 `https://platform.qhzhiyin.com`，不填路径。另需完成真实微信登录、真机联调、备案、隐私声明和小程序审核。云端服务健康与小程序可发布是两个独立验收步骤。

## 实施记录（2026-09-09）

- 私有 GitHub 仓库已推送项目代码，8 项发布 Secrets 已配置；仅有一套生产部署环境。
- 后端 169 项测试通过；核心包 19 项测试、TypeScript、Ruff、代码生成一致性检查通过。发布工作流中的 PostgreSQL 16 迁移及业务写入检查通过。
- 已创建数据库 `enersight_prod` 和专属普通账号 `enersight_prod_app`，仅授予该库 DBOwner。生产实查 `rolsuper`、`rolcreatedb`、`rolcreaterole` 均为 false，迁移版本 `37c98398bf8c`。
- 生产配置写入 `/opt/enersight/.env`，权限 `600`。数据库密码和 JWT 密钥未提交 Git、未输出到聊天。
- [生产部署工作流](https://github.com/xiaoguo123456/EnerSight/actions/runs/34300643803) 已成功，代码版本 `e8c567a4a3ae`。实际镜像见下方；服务器 `.release.env` 保存当前完整摘要。
- API 容器健康，Nginx 语法检查通过，ALB 已接入独立网关组。公网 `/enersight/ready` 与 `/gateway-health` 返回 200；New API 首页与切换前内容一致。
- 未更改安全组，未重启其他业务容器。现有 New API、steward、weishen 容器运行状态正常；未使用真实用户令牌进行流式请求或微信真机验收。
- 微信 AppID 和 AppSecret 已配置到生产环境，后端容器已重建并加载配置。公网就绪检查返回 200，使用无效登录 code 验证时返回 400 / INVALID_CODE，微信凭证校验链路已接通；真实用户登录仍需小程序真机联调。示例文件仅保留空密钥项。

首次部署镜像（当前版本以服务器发布记录为准）：

```text
prod-weishen-registry-vpc.cn-beijing.cr.aliyuncs.com/weishen/enersight-backend:e8c567a4a3ae@sha256:88bdc585909d4fdac0bfbf5bc39eeb07306de704cdac9873d1e739244946cb8a
```

### 网关应急回退

在阿里云控制台，将 HTTPS 监听器 `lsn-6hw30xr2sartlc2gb8` 中规则 `rule-maeoh4pk2xiy5gopp2` 的转发组恢复到 `sgp-2bta4zqj9hwh5hjhrd`，权重 100。保持域名条件 `platform.qhzhiyin.com` 和优先级 4000。这会恢复 New API 直连，同时暂停该域名下 EnerSight 的访问，不回退数据库。

Cloud Shell 的 CLI 对此 ALB 接口需要扁平参数；`--force` 用于跳过 CLI 本地旧参数模型校验，服务端权限与参数校验仍然生效：

```sh
aliyun alb UpdateRuleAttribute --region cn-beijing --force \
  --RuleId rule-maeoh4pk2xiy5gopp2 \
  --RuleActions.1.Type ForwardGroup --RuleActions.1.Order 1 \
  --RuleActions.1.ForwardGroupConfig.ServerGroupTuples.1.ServerGroupId sgp-2bta4zqj9hwh5hjhrd \
  --RuleActions.1.ForwardGroupConfig.ServerGroupTuples.1.Weight 100
```

### 小程序联调记录（2026-09-09）

- 重新构建生产小程序包，确认产物使用 `https://platform.qhzhiyin.com/enersight`，不再请求示例域名。
- 项目配置与本地配置均开启 `urlCheck`；开发者工具界面中“不校验合法域名、web-view（业务域名）、TLS 版本以及 HTTPS 证书”未勾选。
- 修复客户端首次收到 `UNAUTHORIZED` 时未启动登录的问题；首次未登录、令牌过期均只尝试登录后重试一次，避免循环。新增 3 项回归测试，核心包共 22 项测试及小程序类型检查通过。
- 开发者工具模拟器已完成真实微信登录；生产日志确认登录接口和首页接口均返回 200。首页显示正常的无站点状态，地图底图可显示；无站点时地图概览返回 404 并展示添加入口，属于现有空状态逻辑。
- 已生成真机预览二维码，手机扫码验收仍待用户完成。当前账号无站点，尚未验证站点卫星图片加载。开发者工具基本信息页未正常显示域名列表，未确认手动刷新按钮操作，但开启校验后的实际请求已经通过。

### 公开电站目录迁移（2026-09-09）

- 将本地 SQLite 中 `catalog_plants` 的公开基础资料迁入生产 PostgreSQL，只迁目录表，不迁本地用户、站点或登录数据。
- 生产目录由 0 条增加至 18,764 条：光伏 13,472、风电 5,292；GEM 17,735、WRI 1,029；其中 17,506 条有中文名称，全部为 operating。
- 本地有 10 条区县名称超过原有 32 字符限制，最长 44 字符。首次导入事务因此整体回滚；随后通过迁移 `a83d192f4b60` 将字段扩展为 128 字符，未截断数据。
- [生产发布 34306090558](https://github.com/xiaoguo123456/EnerSight/actions/runs/34306090558) 发布代码 `2b8904f5185dd65ff4b6adc169fc4525cf36a049`，包含字段修复及 PostgreSQL 长地名验证。
- 重新导入使用单事务，按 ID 去重；全部字段按 ID 排序并规范化时间后，生产与本地 SHA256 一致：`56bd78a33976c5a88ec053d421b1fb14fdf414fcb1100d7db920b75e6beaccb8`。
- 中文搜索、附近电站、地图范围、风电筛选均返回结果；公网 `/enersight/ready` 正常。用户可进入“选择公开电站”选择已有电站，无需上传站点资料。
- 本次迁移的是已有目录快照；没有自动把全部电站加入任何用户的“我的站点”，也没有重新下载外部数据集。

### 主分支迁移合并与预警计时修复（2026-09-09）

容量溯源 `c291b8d4f230` 与预警检测时间 `c4f1a2b7d9e0` 保留原有迁移，
通过合并节点 `c5a092e1d830` 汇合；随后 `c6b103f2e941` 新增可空的
`clear_since`、`last_clear_check_at`。已有预警不回填稳定期，升级后从首次有效未命中重新计时。
可从任一旧分支或同时已执行两个分支的数据库升级，仍使用 `alembic upgrade head`。
发布前必须检查 `alembic heads` 仅有一个节点，并验证空库和两个分支各自升级到最新版本；
不能只依赖使用 `create_all` 建库的单元测试。

### model-v4 发布说明

`ENERSIGHT_MODEL_V4_START_DATE` 默认 2026-09-10（目标日，北京时间），发布前可设为计划
切换日期。v4 缓存与旧版本隔离，历史不回算、不覆盖；参见 16。小程序历史记录已实现，
入口为首页 → 全部场站 → 历史记录。本次本地核验生产该接口仍返回 404，需后端发布后
才能读取线上记录；小程序已重新构建，不应将此问题诊断为前端缓存。

### IFS HRES 地图栅格模块（2026-09-10）

同一 API 容器内接入 Rasterio/GDAL、omfiles、fsspec，不新增地图容器。
后台需要访问 `https://openmeteo.s3.amazonaws.com/data_spatial/ecmwf_ifs/`，不需要 API 密钥。
`ENERSIGHT_ENABLE_SCHEDULER=true` 时启动 10 秒后预处理，之后每 10 分钟检查。
首次部署数据尚未准备好会返回明确的 `MAP_PREPARING`，API 健康检查不代表地图已就绪。

数据卷目录：
- `map-rasters-v3/{run}/{valid}/`：浮点 COG、显示用 GCJ COG、完整成果清单及锁。
- `tiles/hres-v3/{run}/{valid}/{layer}/{coord}/smooth-v2/{z}/{x}/{y}.png`：固定版本图片。
- `hres-grid-v1/`：原生解码中间缓存，仍保留 24 小时；COG/新瓦片按有效时刻保留 48 小时。

同容器的预处理/渲染各一个子进程，数值库内部线程设为 1，沿用 1.5 CPU/1536MB 容器上限。
持久卷必须可写；文件锁用于同一数据卷上的多进程去重。暂不支持跨主机独立数据卷的共享锁。

可手工预热：`docker exec enersight-prod-api-1 /app/.venv/bin/python -m scripts.prepare_map --hours 2`。
本机若启用了 Conda 的 `PROJ_DATA`，运行前清除该变量以使用 Rasterio 自带匹配的 PROJ 数据库：
`env -u PROJ_DATA -u PROJ_LIB uv run python -m scripts.prepare_map --hours 2`。

Nginx 直出需要同步 `deploy/gateway/docker-compose.yml` 和 `services/enersight.conf`，
新增只读挂载 `/opt/enersight/data/tiles:/srv/enersight-tiles:ro`。先更新挂载并重建网关，
再验证 `nginx -t` 与 PNG 可访问；勿只更新 alias 而遗漏挂载。未启用直出时 FastAPI `/tiles`
仍能提供相同图片，但会占用 API 静态文件请求。现有后端发布工作流不会自动修改共用网关。

验收应包含：当前三个图层成果已就绪、全国/新疆视野、PNG 透明缺测、热缓存响应、
小程序真机地面覆盖层与模拟器显示；不能只以 `/ready` 成功判断地图发布完成。


### 测试环境上线（2026-09-13）

- 测试入口：`https://test-www.qhzhiyin.com/enersight`；ECS `47.93.60.25`，部署目录 `/opt/enersight-test`，回环端口 `8002`。
- 已创建独立数据库 `enersight_test`，普通账号 `enersight_test_app` 仅绑定该库 owner；不复制生产用户数据。
- 服务器 `.env` 权限为 600，数据库密码与 JWT 独立生成；经用户授权，复用本项目微信 AppID、AppSecret 与腾讯位置服务配置。
- GitHub `test` 环境和测试主机 Secrets 已配置。后端或部署文件推送到 main 后，自动检查、构建并部署测试；生产继续手工指定提交。
- [首次成功自动部署](https://github.com/xiaoguo123456/EnerSight/actions/runs/34704250341)，提交 `5cf561f4ed82b46a49392723f968b56976765df2`。代码检查、单元测试、PostgreSQL 迁移与写入检查、镜像地图依赖验证均通过。
- 修复报告测试依赖主机日期的问题：测试数据使用站点时区，UTC 环境下 17 项报告测试通过。
- Nginx 语法检查通过并已平滑加载，原配置备份为 `/opt/weishen/deploy/nginx.conf.before-enersight-20260913001428`。原有测试首页、花花狗测试与生产健康接口、EnerSight 生产就绪接口均仍为 200。
- 测试公网 `/enersight/ready` 返回 200；无效微信 code 返回 400 / `INVALID_CODE`。真实用户登录及手机端合法域名仍需小程序联调。
- `pnpm build:test` / `pnpm build:prod` 已实际构建并核验产物地址。测试默认关闭全量定时采集和卫星归档，地图预热不在本次上线验收范围内。
- 测试服务器 Nginx 已生效，花花狗仓库 main 已同步同一配置（提交 `7f65c4e`），配置 SHA256 与线上一致。该次同步跳过花花狗自动部署；之后 main 发版会保留 `/enersight/` 路由，独立的 `enersight-test` 容器和数据目录不受花花狗 Compose 更新影响。

本次只将部署配置与必要测试修复合入 main，没有合入当前开发分支其余 7 个提交。

### 七天预测及审查修复测试发布（2026-09-13）

- 修复提交 `f90c418`；集成提交 `ee096266eda45cbb8d990246ac487f8f0aa337cc` 已推送至 `main` 和 `feat/user-stations-7d-forecast`。合并主分支部署基线无冲突，集成提交与修复提交代码树一致。
- [测试部署 34737949011](https://github.com/xiaoguo123456/EnerSight/actions/runs/34737949011) 与 [CI 34737949116](https://github.com/xiaoguo123456/EnerSight/actions/runs/34737949116) 均成功。云端 `make check` 通过（后端 387 项、前端 40 项）；PostgreSQL 16 迁移到 `a713d91e2001`、`alembic check` 及写入检查通过。
- 已发布镜像标签 `ee096266eda4`，摘要 `sha256:75ce0b52a23b7d9a6a470427186764256c01082308e8fcd580113c71855e099c`。测试服务就绪检查通过，公网 OpenAPI 已包含 `hub_height`、`common_energy_kwh` 和 `input_archive_id` 等本次新字段。
- `pnpm build:test` 与 `pnpm --filter @enersight/miniapp build:h5:test` 均成功。两套产物已核验使用 `https://test-www.qhzhiyin.com/enersight`，不包含生产 API 或临时本机 API 地址；“我的”人像图标已进入小程序包。
- 微信开发者工具已载入测试包，但控制台明确报告测试域名不在 request 合法域名列表。用户选择自行在微信后台添加 `https://test-www.qhzhiyin.com` 的 request / downloadFile 白名单，完成后再刷新项目配置及重新编译验收。未关闭域名校验，未将测试包改连生产。
- 本次只发布测试后端；未触发生产工作流，H5 仅完成测试地址构建，没有发布 H5 站点。

### 公开目录同步及小程序导航验收（2026-09-13）

- 用户已配置测试域名；刷新微信项目配置后，request / downloadFile 列表均包含 `https://test-www.qhzhiyin.com`，域名与证书校验保持开启。
- 按用户要求，将生产 `enersight_prod.catalog_plants` 的公开目录同步至 `enersight_test.catalog_plants`：共 18,764 座，其中光伏 13,472 座、风电 5,292 座。未复制生产账号、私人电站或其发电记录。
- 导出使用只读、可重复读事务；导入强制校验数据库名称，仅允许空测试目录或与快照完全一致的目录，单事务分批写入并在提交前全量回读。重复执行验证通过，没有新增重复记录。
- 按 ID 排序、字段名排序及 ISO 时间格式规范化后的全量内容 SHA256：`6e8a8b4ab6514f10b615e27db8c20471d49060c40b866c45ad9aee0d85f8b45e`，生产快照与测试回读完全一致。
- 底部图标排查：10 张 PNG 和 `app.json` 引用均正确，开发者工具代码依赖分析确认 `assets/tabbar` 已打包；清除文件缓存后，原 `packages/miniapp` 工程仍只显示文字。将**同一份产物直接从 `packages/miniapp/dist/weapp` 导入**后，首页、地图、预警、站点、我的五个图标全部恢复，选中状态可切换。“我的”使用人像图标。
- 当前工具版本为 Stable `2.02.2608060`。测试预览统一采用：仓库根目录执行 `pnpm build:test`，微信开发者工具导入 `packages/miniapp/dist/weapp`（工程名“EnerSight 测试预览”）。该目录内生成的 `project.config.json` 已设置 `miniprogramRoot: "./"`，避免本次嵌套工程目录的模拟器图片解析异常。每次重新构建后，在该预览工程点击编译即可；不要手改生成的图标路径或改连生产。
- 模拟器验收：公开目录显示 18,764 座；首页已加载公开光伏站、当天功率曲线和未来七天电量；“我的”显示微信登录成功。首次旧令牌的 401 已由重新登录恢复，清空控制台后切换“我的”未新增错误。本次未完成手机真机验收，也未预热测试环境的全量地图栅格。

### 测试地图与页面精简（2026-09-13）

- 图层不可用的根因：测试环境 `ENERSIGHT_ENABLE_SCHEDULER=false` 同时关闭了地图预处理，三个图层均无成果；目录数据库同步不会生成气象栅格。
- 新增独立开关 `ENERSIGHT_ENABLE_MAP_SCHEDULER`，未设置时沿用总开关。测试 Compose 显式开启地图任务，每 10 分钟准备当前及后续时效；全目录累积、报告等任务仍由总开关控制。测试容器内存上限调整为 1 GiB、内存加交换区上限为 1.5 GiB，CPU 保持 0.75 核。
- 预警页删除重复检查时间、模型说明和发布时效注释，仅保留站点、刷新、预警内容；持续生效的预警不再因首次发布时间较早而误标“数据较早”。检查过期或刷新失败仍直接显示。
- 功率图标题旁只保留单位。15 分钟气象输入直接取 Open-Meteo `minutely_15`，中国区域为上游逐小时插值；平台再计算出力，没有将整条小时功率曲线插值为 15 分钟。默认前 4 天为 96 点、第 5–7 天为 24 点；回归测试验证该分辨率策略及逐小时降级。
- 界面默认不加解释性小字，也不机械新增 ⓘ；该偏好已写入本项目 PRODUCT.md 和用户全局 AGENTS.md。
- 本地 `make check` 通过：后端 390 项通过、1 项跳过；前端 40 项通过；类型生成、TypeScript 与 Ruff 检查通过。地图调度的四种开关组合新增回归验证。
- 修复提交 `7c8e966c1c189349d355f790412a3d7a32c0c7e6` 已部署至测试；[测试部署 34760988895](https://github.com/xiaoguo123456/EnerSight/actions/runs/34760988895) 与 [CI 34760988860](https://github.com/xiaoguo123456/EnerSight/actions/runs/34760988860) 均成功。镜像摘要 `sha256:4a0baa54ff77659bbf10044b81f26520ac7ae9c7c9aa9c306ccd5f05206b8834`，容器健康，启动日志为 `scheduler started: ['map_prepare']`。
- 首次准备：温度由测试自行生成；风速和辐照复用生产 `20260913T0600` 批次的公开 COG，校验传输快照 SHA256 后仅原子补齐缺失文件，两套坐标文件先于清单发布。快照指纹 `56ab1b1a8f40d60de02fe9b4fd774c72a29e9bcc0eca0992d198d00ef9962ae6`；后续由测试独立调度更新。未修改生产服务。
- `pnpm build:test` 成功，微信模拟器已验收紧凑预警页、功率图单位、温度着色、风场动画和辐照着色。风速与辐照恢复请求均返回 200，最终控制台无新增错误。跨整点时允许两小时内旧成果回退，并按实际有效时刻标注“缓存”；不冒充新时效。
- 测试环境实际七天留档（9 月 13–19 日）已回读：时间步长 `[15, 15, 15, 15, 60, 60, 60]`，点数 `[96, 96, 96, 96, 24, 24, 24]`。本地另有短缺测插值修补，不应笼统称“没有任何插值”。

### 地图自由浏览修复（2026-09-13）

- 原地图中心持续绑定场站／搜索坐标，拖动结束后未同步实际中心及缩放，页面再次显示还会清空搜索中心。现用独立视野状态保存手势结果，首次载入及切换场站才自动定位；返回地图、刷新同站数据、切图层都保留用户视野。搜索、聚合点及定位按钮仍可主动移动地图。
- 原生事件缺少中心或缩放时读取 MapContext；异步读取过期后不能覆盖新手势、切站或搜索结果。缩放按钮基于实际中心放大缩小。定位统一更新视野状态，去掉重复且开发者工具不支持的 moveToLocation 调用。
- 前端测试 45 项通过（含新增 5 项视野回归），TypeScript 与差异空白检查通过，`pnpm build:test` 成功。微信开发者工具验证拖动、缩放、切换温度图层、切到首页再返回均保持浏览区域；主动定位能回到场站。尚未进行手机真机验收。
- 本次只改小程序前端，测试预览仍导入 `packages/miniapp/dist/weapp`，不需要更新测试后端镜像。

### 点预报统一 15 分钟（2026-09-13）

- 单站改为一次请求完整 14 个 `minutely_15` 字段，取昨日及今天起八天；首页、预警、报告和七天预测共享缓存。全目录、自动模型复核、云量降级点网格也采用 15 分钟请求。原生空间栅格和历史小时资料保留原始分辨率。
- 七天功率每日 96 点，电量积分乘 0.25 小时；当前功率、昨日同期、区间均值、预警晴空基准、报告分时统计及跨夜限电同步适配。24 小时气象趋势 97 点，七天趋势 672 点，前端按响应步长和实际时间绘图。
- 计算版本升为 `model-v4-修订2`，旧预测缓存自动失效。单站只保存实际使用的一份气象输入及其分辨率。历史留档不回填、不覆盖。
- 全目录气象改用 `fleet-weather-cells/{model}/{date}/` 按内容指纹落盘，`*-weather-15m.json` 保存索引；最近 8 格保留在内存。完整输入归档流式写入并同步计算指纹，避免一次加载或复制全部天气 JSON。单站缓存上限调整为 512 份，以控制高频序列内存。
- 本地 `make check` 通过：后端 395 项通过、1 项跳过；前端 47 项通过；接口类型生成、TypeScript、Ruff 检查通过。`pnpm build:test` 成功。
- 北京公开城市坐标实测自动、ECMWF、GFS、ICON 四模型，每个模型分别跑光伏与风电完整七天计算：全部每日 96 点，逐日电量与功率积分一致，今日预测与当前功率链路的日电量一致。该检查验证计算一致性，不代表误差回测或精度提升。
- 使用一份真实输入模拟 100 个独立网格，无额外上游请求：输出完整留档 8,430,319 字节，落盘与归档耗时 5.54 秒，新增 Python 分配内存峰值约 3.51 MiB（不含解释器和已加载依赖）。

### 测试环境气象配额与连接池故障（2026-09-14）

- 00:50 左右的小程序首页出现 502，随后本站、地图概览、预警和目录查询超时。测试容器没有重启或 OOM；`/health` 返回 200，但访问数据库的 `/ready` 返回 500，不能用进程存活代替业务可用。
- 从测试容器实际请求气象源得到 429，响应原因为 `Daily API request limit exceeded. Please try again tomorrow.`；当天免费额度已经耗尽。
- PostgreSQL 回读发现两个查询公开目录后的 `idle in transaction` 会话，持续超过 200 秒；应用连接池仅有常驻 1、溢出 1 个连接，日志持续出现等待连接 30 秒超时。预警列表和当前预警出网前未结束事务，共享气象预算冷却又持续等待，导致其他数据库请求被连带阻塞。
- 本地修复：预警查询场站后立即提交只读事务；共享预算在上游冷却期快速失败；上游 429 映射为 503 `UPSTREAM_RATE_LIMITED`，展示配额错误并阻止客户端自动重试。有效缓存仍可使用，配额未恢复时不伪造新气象数据。
- 本地完整检查通过：后端 419 项通过、1 项跳过，前端 55 项通过；接口生成、TypeScript 与 Ruff 检查通过。本机需移除指向其他安装的 `PROJ_DATA` / `PROJ_LIB` 环境变量，避免地图测试误用不匹配的坐标数据库。
- 用户已明确授权推送并部署测试，按既有 main 自动部署流程发布；部署结果以流水线和发布后的业务验收为准。

- 修复提交 `1ad4ad60c1bda303b9a28fff3d99e6ff34d66fb3` 已部署测试，[测试部署 34770389311](https://github.com/xiaoguo123456/EnerSight/actions/runs/34770389311) 成功；镜像摘要 `sha256:71f153f11c99ce39d9884910601f7285f06c2ef12f7d54dd5ce57c1bc438e67f`。
- 发布后验收：容器 healthy，公网 `/ready` 返回 200；微信模拟器公开目录恢复 18,764 座，地图目录查询返回 200。本站和预警明确提示气象配额错误，地图概览返回 503；全目录有效快照仍返回 200。数据库仅有一个正常 idle 连接，无 idle in transaction，未再出现连接池耗尽。气象源当天配额仍未恢复，不能宣称气象数据全部恢复。

### 气象转发 Worker（2026-09-14）

- 用户在 Cloudflare 控制台建立测试 Worker `enersight-weather-test`，代码为 `deploy/weather-relay/worker.mjs`，绑定 `weather-test.weishenai.cn`，`ALLOWED_IPS=47.93.60.25`。测试机 `.env` 已写入转发地址与 64 位密钥，权限 600；测试机实际出口 IP 与白名单一致。
- 从测试机验收：域名解析到 Cloudflare；无密钥与错误密钥均 401，正确密钥 `/health` 200，白名单外路径 400，POST 405。全程未请求 Open-Meteo。非白名单来源 403 由 Worker 单元测试覆盖，本机经代理无法实测。
- 旧探测 Worker `enersight-weather-probe` 已到期，所有请求返回 410，不再使用。
- 后端区分 Worker 拒绝与上游响应：透传响应带 `X-Weather-Relay: cloudflare`，无此头的 4xx 与任何 3xx 判为转发异常且不重试，避免白名单拒绝被误当成坐标越界。
- 转发代码在开发分支，尚未合入 main；当前测试镜像 `1ad4ad60c1bd` 不读取转发配置，合入并自动部署后生效。生产 Worker 尚未建立，建立前先在生产机确认实际出口 IP。

### 气象拉取限次（2026-09-14）

- 背景：测试环境日额度耗尽排查显示，全目录在新批次、跨日、索引 12 小时到期和元数据缺失时都会整轮重拉（每轮约 2,747 个坐标，四个模型各自一份），单点预报在元数据缺失时每 10 分钟回源一次。
- 全目录每个模型每个北京日整轮拉取一次，08:00 前沿用上一日快照；同日新批次不重拉，部分覆盖只补拉缺失坐标，续拉轮数上限 4。移除构建过程中每批次一次的元数据复核请求。
- 单点预报每坐标每模型每天最多回源 4 次，按当地时段均分；跨时段批次未变不重拉，元数据缺失时也受同一上限约束。
- 新增配置 `ENERSIGHT_FORECAST_REFRESHES_PER_DAY`、`ENERSIGHT_FLEET_REFRESH_HOUR`、`ENERSIGHT_FLEET_FETCH_ROUNDS_PER_DAY`，默认 4 / 8 / 4；移除 `ttl_forecast_batch`。
- 未改动：地图云量降级网格仍未计入共享预算；`best_match` 与 `ecmwf_ifs` 仍分别缓存。

### 登录体系、气象限次与预警提速测试发布（2026-09-14）

- 开发分支 `feat/user-stations-7d-forecast` 以快进方式合入 `main`（`85cbb2d..ad6e6dc`），共 12 个提交：气象请求经 Cloudflare Worker 转发、气象拉取限次、云层预警测试时钟修复、首页 CSV 导出与界面精简、单点预报加请求 200 m 风速、游客模式与登录体系、自建电站装机容量按 MW 输入、预警页首屏提速、移动端界面紧凑化及整合提交。合入后开发分支已删除（本地与远端）。
- 发布前本机 `make check` 通过：后端 468 项、core 52 项、小程序脚本测试、接口类型生成、TypeScript 与 Ruff。首轮检查发现新增登录页未开启好友分享导致「所有已注册页面开启好友分享」用例失败，修复为 `ad6e6dc` 后通过。
- [测试部署 34822908437](https://github.com/xiaoguo123456/EnerSight/actions/runs/34822908437) 与 [CI 34822908396](https://github.com/xiaoguo123456/EnerSight/actions/runs/34822908396) 均成功；镜像 `enersight-backend:ad6e6dc3ed29@sha256:b6907c66add14616fb175fef582a02ff24d53b1954609116e905d5584c5435df`，容器 healthy。本次无数据库迁移。
- 测试机回环验收：`/ready` 200；游客访问公开电站 200、访问我的电站 401 `UNAUTHORIZED`；当前预警冷启动 4.8 s，云图时间轴 0.13 s，最新帧 0.11 s（复用当前预警渲染结果），20 秒后中间帧与最早帧 0.12–0.14 s（后台预渲染已完成）。
- 气象转发：测试 `.env` 的 `weather-test.weishenai.cn` 转发配置已被新代码加载，单点每天回源 4 次、全目录 08:00 起拉取的默认配置生效；部署后 30 分钟内无转发拒绝、429 或预渲染失败日志。
- 小程序已按测试环境构建（`--mode test`），登录页、我的电站登录引导、MW 表单、预警页并行加载与紧凑布局待开发者工具与真机验收，`wx.login` 真实登录需真机验证。
- 尚未完成：生产 Worker 未建立（建立前确认生产机出口 IP）；小程序后台隐私保护指引按 09 §4.4 填写；《用户服务协议》草稿待法务审核；生产未发布。

### 生产气象转发 Worker 验收（2026-09-14）

- 用户建立生产 Worker `enersight-weather-prod`，绑定 `weather.weishenai.cn`，`ALLOWED_IPS` 为生产机实测出口 `39.105.228.11`；生产 `.env` 已写入转发地址与 64 位密钥。
- 从生产机验收：无密钥 `/health` 连续 3 次 401，带密钥 200；带密钥请求一次单点预报返回 200 且带 `X-Weather-Relay: cloudflare`。从测试机访问返回 403，来源 IP 限制生效。
- 域名解析：建立初期阿里云内网解析偶发 NXDOMAIN（新记录的否定缓存）。复查时生产机 `getaddrinfo` 50/50 成功，两台内网解析器 A 与 AAAA 各 50/50 成功，已稳定。
- 生产镜像仍为不读取转发配置的旧版本，发布含转发代码的镜像后生效。

### 气象趋势跟随预测日期与轮毂高度风速导出测试发布（2026-09-14）

- 提交 `f8eef0b`：`/v1/trends` 新增 `day_offset`（0–6），首页 24 小时气象趋势默认展开并跟随七天预测所选日期；预警页近 3 小时云图取消折叠。提交 `8e1e226`：新增 `hub_wind_speed` 指标，风电站导出气象 CSV 多一列「轮毂 N 米风速」，与发电预测同一换算；光伏站仍只导出 10 米风速。
- 本机 `make check` 通过：后端 473 项、core 52 项、小程序脚本 18 项。[CI 34829432491](https://github.com/xiaoguo123456/EnerSight/actions/runs/34829432491) 与 [测试部署 34829432525](https://github.com/xiaoguo123456/EnerSight/actions/runs/34829432525) 均成功，镜像 `enersight-backend:8e1e2260e8c4`，容器 healthy。本次无数据库迁移。
- 测试机回环验收（怀来风电场与怀来光伏站）：10 米风速 `day_offset=0` 与 `2` 各 97 点，分别覆盖当日与后天 00:00–次日 00:00；轮毂风速 `day_offset=2` 返回 97 点、`hub_height=100`、单位 m/s，日均 2.84 m/s（同日 10 米 1.74 m/s）；光伏站请求轮毂风速 400；`day_offset=7` 400。部署后无错误日志。
- 小程序已按测试环境重新构建，待开发者工具与真机验收。

### 旧版小程序逐小时曲线兼容（2026-09-14）

- 背景：生产网关日志中 Referer 版本号为 `1`（正式版）的请求自北京时间 9/13 19:22 起约 2,700 条，覆盖 Windows、iPhone、Android。该版本按逐小时画图，后端改为 15 分钟曲线后直接发布会导致横轴与悬浮时间错乱。另查生产旧后端（`ad7a1ae`）对游客请求首页、全目录预测、公开电站与趋势均返回 401，且无 7 天预测接口；新版小程序为游客模式，须先发后端再提审。
- 提交 `3e6cb26`：新版小程序固定携带 `X-Resolution-Minutes: 15`；不带的请求在响应出口降为逐小时，规则见 06 §2.8，不额外请求气象上游。本机 `make check` 通过（后端 477 项、core 52 项、小程序 18 项）。
- 测试部署成功，镜像 `enersight-backend:3e6cb264e0a8`，容器 healthy。怀来风电场回环对照（无头 / 带头）：首页趋势 25 / 97 点、功率 24 / 96 点，电量一致；风速与辐射趋势 25 / 97 点；电站详情趋势 25 / 97 点；全目录今日功率与 `days[0]` 24 / 96 点，电量一致。部署后无错误日志。
- 小程序生产包已按新代码重新构建（API 为 `platform.qhzhiyin.com`，含该请求头），待生产后端发布后提审。
- 删除时机：新版正式版上线、网关日志不再出现旧版本号 Referer 后，删除 `app/curve_resolution.py` 与请求头。

### 生产发布 17290a7（2026-09-14）

- 用户手动触发 [生产部署 34834104576](https://github.com/xiaoguo123456/EnerSight/actions/runs/34834104576)，提交 `17290a7aa432`，成功；上一版本为 `ad7a1ae`（9/10）。镜像 `enersight-backend:17290a7aa432`，容器 healthy，北京时间 18:42 启动。
- 启动时自动执行 3 个迁移 `c6b103f2e941 → e1f2a3b4c5d6 → f2a3b4c5d6e7 → a713d91e2001`，均为加列，旧镜像回滚仍可运行。
- 经公网域名验收：`/ready` 200；游客访问公开电站、全目录预测、首页、趋势 200，我的电站 401；7 天预测 200（7 天、首日 96 点、起报 08:00）；轮毂风速 `day_offset=2` 97 点、`hub_height=100`，光伏站 400。
- 旧版兼容（怀来风电场，无头 / 带头）：首页趋势 25 / 97 点、功率 24 / 96 点，电量一致；趋势与电站详情 25 / 97 点；全目录 24 / 96 点。
- 全目录预测：部署后按新规则整轮重建，约 1 小时内发布（此前接口为 `queued`，旧版首页全目录段为空）；覆盖 8,215 / 8,215 座、容量覆盖 69.0%、7 天、失败 0。
- 气象转发：容器已加载转发变量；出网 443 连接为 Worker 的 Cloudflare 地址，无直连 `api.open-meteo.com`（另有 CloudFront 连接，为不经转发的原生地图栅格）。
- 部署后约 1 小时：网关无 5xx；正式版（Referer 版本 1）69 次 200、1 次 401（令牌过期后续登）；后端无 Traceback、429、上游不可用或转发异常。
- 待办：用 `3e6cb26` 之后构建的生产包提审；过审发布后观察旧版 Referer 流量归零，再删除逐小时兼容。

### 代理池测试发布与回归（2026-09-16）

按出口分账的代理池连发三次，中间出过一次把测试环境打挂的回归。全程**没有服务器访问权限**，
所有判断来自公网接口的响应，不是容器日志 —— 池子的内部状态是从错误信息里的计数推断的。

| 提交 | 流水线 | 结果 |
| --- | --- | --- |
| `0fc99ef` | [35083310282](https://github.com/xiaoguo123456/EnerSight/actions/runs/35083310282) | 成功发布，但实时气象全部 502 |
| `2acaa30` | [35084627355](https://github.com/xiaoguo123456/EnerSight/actions/runs/35084627355) | 冷启动修复，仍 502，但错误自证原因 |
| `1c21394` | [35085609592](https://github.com/xiaoguo123456/EnerSight/actions/runs/35085609592) | 加直连兜底，恢复 |

- **发现：测试机的代理池开关一直是开的。** 9/15 试验后没关回去，`.env` 仍是
  `ENERSIGHT_WEATHER_PROXY_POOL_ENABLED=true`；旧代码用得好好的，新代码把池子清空了才暴露。
  部署前 `/v1/home` 200（12 秒），部署后 502「代理池暂无可用出口」，而
  `/v1/predictions/fleet` 因为读落盘快照仍是 200 —— 故障面不均匀，只看一个接口会误判。
- **两个回归原因**：① 首次入池的两次出口确认被拆到两个刷新轮，冷启动要等满 15 分钟；
  ② 新的 `load()` 只读 `nodes`/`exits`，把旧版 v1 的 `entries` 全丢了。都已修。
- **根因不在冷启动**：修复后错误信息给出「候选 0、可用出口 0、上次列表刷新失败」。
  ProxyScrape 的免费列表**从测试机拉不通**，而该机 `data/` 下也没有可用的旧候选。
  没有候选就没有路由去取列表，没有列表就没有候选 —— 池子自己起不来。
- **恢复**：加直连兜底后全部恢复。`/v1/home` 200 / 1.67 s（此前 12 s）、
  `/v1/map/layers/cloud` 200 / 3.50 s、公开电站 `trends` 200 / 1.22 s、
  `detail` 200 / 0.63 s、`predictions/station` 200 / 0.39 s、`alerts/current` 200 / 1.66 s，
  `/ready` 200。本机 `make check` 532 项通过。
- **当前实际行为是「全部走直连兜底」**：池子一个出口都没建起来，代理池等于没生效。
  要让它真正跑起来，先解决列表源可达性 —— 换一个境内可访问的源（`WEATHER_PROXY_SOURCE`），
  或手工放一批代理进 `data/weather-proxy-seeds.json`（一天内有效的 JSON 数组，池子实测后入池）。
  两条都要在服务器上操作。
- **观测缺口**：加了兜底之后，空池不再以 502 暴露，从公网就分不出这次是走代理还是走直连了。
  目前只能看容器日志里的 `代理池 ... ready_exits=N` 和 `气象代理池不可用，本次退回直连`。
  若要从外部区分，需要另加一个响应头或只读状态接口，尚未做。

#### 换源与换探测点后跑通（`343df3e`，流水线 35092321702）

拿到服务器访问权限后逐项实测，定位到三个都在默认值上的卡点（对照表见上面「源与探测点的选型」），
改掉后池子正常建立：

```
代理池 candidates=100 ready_nodes=5 ready_exits=5 paused_exits=1
usage={'direct': 2554.0, '103.237.102.191': 1, '14.251.13.20': 1, ...}

气象代理请求 path=/data/ecmwf_ifs/static/meta.json exit=103.237.102.191 cost=1.0 attempt=1
气象代理请求 path=/v1/forecast exit=14.251.13.20  cost=1.5 attempt=1
气象代理请求 path=/v1/forecast exit=27.185.218.213 cost=1.5 attempt=2
```

- 100 个候选实测出 **5 个独立出口**（通过率 5%，低于预期，说明候选上限不能再压）。
  业务请求确实走代理，`退回直连` 在该窗口为 0 次。
- 三条日志正好验证了三件设计：按出口归属与计价（`exit=` / `cost=`）、出口按最近最少使用
  分散（连续三次三个不同出口）、传输失败换**另一个**出口再试一次（`attempt=2`，首个出口失败
  后在第二个出口成功，该次请求 4.89 秒；命中缓存的后续请求 0.07 秒）。
- 账本落盘 `data/weather-proxies.json`（25,984 字节，version 2）。观测窗口内没有任何 429，
  所以**限流按窗口归属这条主路径仍未在真实 429 下验证过**，只有单元测试覆盖。
- `usage` 里 `direct: 2554` 是此前全部走兜底期间累计的，占直连日额度 8,000 的三成。

#### 整轮全目录汇总压过代理池（2026-09-16 20:2x）

手工触发一轮全目录重建，快照从 `error` 变 `ready`，覆盖 8,218 / 8,218。25 分钟窗口统计：

| 指标 | 结果 |
| --- | --- |
| 限流（任何窗口的 429） | **0 次** |
| 代理请求 | 11 次，合计 881 单位 |
| 换出口重试（`attempt=2`） | 4 次，全部是传输失败 |
| 退回直连 | 1 次，原因「气象代理连接失败」 |
| 各出口用量 | 303 / 202 / 185 / 103 / 102 单位 |

**这不等于「不会再 429」**，三条限制说明它没逼近阈值：

1. 本轮只推了 881 个坐标 —— 大部分格点已由此前那轮直连兜底取回并落盘复用。
   冷启动的完整一轮是 2,747 个坐标，尚未在池上跑过。
2. 单出口峰值 303 单位还是跨分钟累计的，离实测的「同一分钟第 600 个坐标返回 429」
   差着数量级，也没触到自设的 300 单位/分钟上限。
3. **限流按窗口归属这条主路径至今没有被真实 429 触发过**，仍只有单元测试覆盖。

真正暴露的瓶颈是另一件事：**一轮跑下来可用出口从 5 个掉到 2 个**
（`ready_nodes=2 ready_exits=2`），已经低于 `MIN_EXITS=5` 的低水位线。免费代理在负载下
大量掉线，当前挡在前面的是代理稳定性，不是上游额度。

#### 候选提到 500 之后的出口数（`2c68d12`，流水线 35099145124）

12 分钟内每 90 秒采样一次 `ready_exits`：

```
6 → 7 → 7 → 5 → 7 → 7 → 8 → 8 → 8      （限流仍为 0 次）
```

- 从改动前的 2 个回到 **7～8 个并稳住**，但**到不了 20 的目标**。
  稳定出口的实际产出率约 **1.6%（8 / 500）**，低于此前测的 5% 瞬时通过率 ——
  瞬时能连通和能持续承接业务是两回事。要凑到 20 个得备 1,200 以上的候选，或者换更好的源。
- 中途掉到 5 又回到 7，说明周转机制在工作：死出口退出、新出口补上。
- 因为 `ready_exits` 始终够不到 `TARGET_EXITS`，低水位探测会一直以 120 秒的节奏跑。
  这是有意的 —— 免费代理持续掉线，探测不停才能持续替换。代价可接受：10 分钟内约 343 次
  探测/请求，容器 CPU 3.09%、内存 340 MiB / 1 GiB，宿主 load average 0.65（该机还跑着
  另外三个项目的容器）。探测不消耗气象额度。
- 对明早冷启动一轮 2,747 个坐标的估算：全局预算 480 单位/分钟摊到 8 个出口，
  每出口约 60 单位/分钟，离自设的 300/分钟和实测的 600/分钟触发点都还远；
  按日算每出口约 343 单位，对 5,000/天的自设上限也宽松。**前提是这 8 个出口能活过整轮。**

---

## 卫星辐照入库（Buffalo，2026-09-20）

葵花 L2 短波辐射由 Buffalo 那台自己拉、自己入库，后端只是按坐标查自建实例。
口径与授权见 [19 §四](./19-forecast-uncertainty-and-observations.md)，这里只记运维事实。

| 项 | 位置 |
| --- | --- |
| 拉取脚本 | `/usr/local/bin/ptree-himawari.sh`（700） |
| 凭据 | `/root/.ptree.env`（600）。**不进仓库、不进日志**，仓库是公开的 |
| 定时 | `ptree-himawari.timer`，每 10 分钟（`OnCalendar=*:2/10`），`flock` 防重入 |
| 落盘 | 卷 `open-meteo_open-meteo-data` 的 `jma_jaxa_himawari_70e_10min/`，每帧约 13 MB |
| 保留 | 脚本每轮删 6 小时没再写过的 `chunk_*.om`。分块是 48 小时一个，所以实际上限约 3.7 GB |
| 出网 | 约 7.4 GB/天。套餐 7 TB/月，占比很小 |

为什么必须删：数据盘只剩 26 GB，而 `CACHE_SIZE` 设的是 32 GB（`cache.bin` 已 25 GB 且还在涨），
不清理会连自建气象服务一起拖垮。

应用侧只要 `ENERSIGHT_OPEN_METEO_SATELLITE_BASE`，留空则用 `ENERSIGHT_OPEN_METEO_ARCHIVE_BASE`。
测试与生产的归档基址已经指向自建实例，不用另配；主源换回官方时必须把这一项显式指到自建，
否则卫星辐照会一直是 `unavailable`（官方没有这个模型）。

排查：`systemctl list-timers ptree-himawari.timer`、`journalctl -u ptree-himawari.service -n 50`。
别在常驻容器里 `docker compose exec` 跑下载子命令（`--help` 都不返回），用一次性容器。
