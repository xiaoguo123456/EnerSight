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
