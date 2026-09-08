# EnerSight 部署说明

BFF 的生产部署约定与操作清单。前端（小程序）由微信托管，不在本文范围；小程序侧的域名、
备案与合法域名配置见 [09 §八](./09-miniapp-compliance.md)。

> 数据库密码、JWT 密钥、微信 AppSecret、服务器地址只允许存在于已忽略的 `deploy/.env`
> 与服务器上的同名文件，禁止写入本文、提交到 Git 或发到群聊。

## 1. 部署决策

### 1.1 固定摘要镜像 + 单服务 Compose

生产用 `packages/server/Dockerfile` 构建的镜像，通过 `deploy/docker-compose.yml` 运行，
只跑 `api` 一个容器。PostgreSQL 用外部托管实例（RDS），Compose 里不带数据库。

- 镜像写完整的 `tag@sha256:摘要`，禁止 `latest`、Watchtower 或任何自动跟随
- 容器以非 root（uid 10001）运行；启动时 `alembic upgrade head` 自动迁移，迁移失败不起服务
- `/app/data` 挂宿主机目录：图层瓦片、卫星归档帧、校准缓存。瓦片丢了会重新渲染，
  **归档帧丢了补不回来**（JMA 只留 35 小时）

### 1.2 流量入口

```
小程序 → https://api.<域名>  → ALB（HTTPS 终止，证书）→ ECS:8001 → 容器:8000
```

- ALB 到容器走 HTTP 回源；uvicorn 已开 `--proxy-headers`，日志与限流拿到真实客户端 IP
- ECS 安全组的 8001 只对 ALB 回源网段开放，不对公网开放；`ENERSIGHT_TRUST_FORWARDED_FOR`
  只有在这个前提下才能设 true，否则 `X-Forwarded-For` 可伪造绕过限流
- ALB 健康检查：HTTP `/health`，期望 200
- 图层与云图是图片 URL（`/tiles/...`），小程序 `<map>` 的 ground-overlay 与 `<image>` 直接拉，
  **该域名要同时配进小程序的 request 与 downloadFile 合法域名**

### 1.3 与其他项目同机

若与其他项目共用一台 ECS，边界同 New API 部署仓库的约定：不同 Compose 项目名与目录、
不共用 Nginx 和 Docker 网络、数据库账号各用各的、只映射 8001 不动 80/443。

### 1.4 单实例约束

定时任务（预警扫描、报告预生成、卫星归档、发电累积）与缓存都在进程内。
**V1 只跑一个实例。** 要扩到多实例时：扫描锁改数据库行锁、缓存改 Redis、
只在一台上开 `ENERSIGHT_ENABLE_SCHEDULER`，见 [05 §五](./05-architecture.md)。

## 2. 外部依赖

| 依赖 | 要求 |
| --- | --- |
| PostgreSQL | 独立数据库 `enersight` 与独立账号（DBOwner，迁移要建表）；RDS 白名单放行 ECS 内网；连接串密码 URL 编码 |
| 出网 | 容器要能访问 `api.open-meteo.com`、`archive-api.open-meteo.com`、`www.jma.go.jp`、`apis.map.qq.com`、`api.weixin.qq.com`、（可选）`api.anthropic.com`。境内 ECS 访问 JMA 与 Anthropic 可能不稳，预留代理 |
| 磁盘 | `data/` 按站点数增长：卫星归档每站每天约 6 MB（60 天保留）+ 瓦片缓存；10 站一年内 < 10 GB |

## 3. 环境变量

`deploy/.env.example` 是模板，复制为 `deploy/.env` 后填写，权限 600。关键项：

| 变量 | 说明 |
| --- | --- |
| `ENERSIGHT_DEBUG=false` | 生产必须。false 时拒绝 SQLite、拒绝开发用 JWT 密钥、关闭 CORS 与开发态免登录 |
| `ENERSIGHT_DATABASE_URL` | `postgresql+asyncpg://...` |
| `ENERSIGHT_JWT_SECRET` | ≥ 32 字节随机值 |
| `ENERSIGHT_WX_APPID` / `WX_SECRET` | 真 `code2session`。没有它 debug=false 下无法登录 |
| `ENERSIGHT_TENCENT_LBS_KEY` | 逆地理与搜索；缺省降级 |
| `ENERSIGHT_AI_PROVIDER` + `ANTHROPIC_API_KEY` | `rule` 或 `claude` |
| `ENERSIGHT_RATE_LIMIT_PER_MINUTE` | 每 token / IP 每分钟请求数，默认 120 |
| `ENERSIGHT_ENABLE_SCHEDULER` / `ENABLE_ARCHIVE` | 单实例开；多实例只开一台 |

全部键以 `packages/server/app/config.py` 为准。

## 4. 首次部署

1. 本机构建并验证镜像：
   ```bash
   docker build -t enersight/api:v0.1.0 packages/server
   docker run --rm -p 18000:8000 -e ENERSIGHT_DEBUG=true enersight/api:v0.1.0
   curl -fsS http://127.0.0.1:18000/health
   ```
2. 镜像中转到生产 ACR（ECS 通常拉不动 Docker Hub / GitHub）：本机 `docker save` 管道
   `ssh ... docker load`，ECS 上 `docker tag` + `docker push` 到 ACR，记录 `docker push`
   输出的摘要。ACR 仓库开版本不可变。
3. 把 `tag@sha256:摘要` 写进 `deploy/docker-compose.yml`，提交。
4. 服务器建目录 `/opt/enersight/`，`git archive HEAD deploy | ssh ... tar -xf - --strip-components=1 -C /opt/enersight`，
   `.env` 单独 `scp` 并 `chmod 600`。
5. `cd /opt/enersight && ./scripts/deploy.sh`。脚本依次预检、拉镜像、`up -d`、等 `/health`
   最多 180 秒，失败自动打日志。
6. 验证：`docker compose logs --tail=200 api` 里迁移成功、调度器启动；
   `curl https://api.<域名>/health`；小程序真机走一遍首页 / 地图图层 / 预警云图。

## 5. 升级与回滚

升级 = 重复第 1–5 步，并且：

- 先读本次改动是否含数据库迁移（`packages/server/alembic/versions/` 有新文件）。
  有迁移先做 RDS 备份/快照
- 用 `git diff` 确认提交里没有连接串、密钥、服务器地址

回滚：

```bash
./scripts/rollback.sh <ACR>/enersight/api:<上一版本>@sha256:<摘要>
```

只回滚应用镜像，不回退迁移；若新版本迁移不兼容旧代码，按备份恢复数据库，不能只换镜像。
回滚后把版本写回 `docker-compose.yml` 并提交，生产状态不能只存在于临时 Shell 里。

## 6. 上线前清单

- [ ] 域名 HTTPS 证书 + ICP 备案，并配进小程序 request / downloadFile 合法域名
- [ ] `.env` 无占位符、`preflight.sh` 通过
- [ ] RDS 账号是 `enersight` 库的 DBOwner，白名单放行
- [ ] ALB 健康检查 `/health` 绿
- [ ] 真机：地图四个图层能贴、预警页云图能显示（图片走 HTTPS 域名）
- [ ] 限流生效：连续请求返回 429 且带 `Retry-After`
- [ ] 日志里没有 `ERROR`；`scan_alerts` / `archive_cloud` / `accumulate_generation` 按点跑
- [ ] `data/` 目录在宿主机上并纳入备份（至少归档帧）
