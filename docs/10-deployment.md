# 晴川观象生产部署

本文对应 `deploy/` 和 `.github/workflows/prod.yml`。参考 `new-api-deploy` 的独立服务边界、固定镜像摘要，以及 `huahuadog` 的 GitHub Actions → ACR → ECS 发布流程。

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

网关保留 New API 的流式响应、长连接和 WebSocket；ALB 提供 HTTPS 协议头。`/enersight/` 的代理剥离路径前缀，后端 `root_path=/enersight` 负责生成包含前缀的图片地址。修改共享网关时，先备份目标配置，验证 `nginx -t`，再 reload；不通过业务发布流覆盖全部配置。

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

生产镜像：

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
