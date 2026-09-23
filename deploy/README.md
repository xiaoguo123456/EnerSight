# 晴川观象测试与生产部署

生产入口：`https://platform.qhzhiyin.com/enersight`。GitHub 私有仓库：`xiaoguo123456/EnerSight`。

- `docker-compose.yml`：单 API 容器，外部 PostgreSQL，主机回环端口 8001。
- `.env.example`：生产配置示例，实际 `.env` 不入库，权限 600。
- `scripts/deploy.sh`：固定摘要镜像发布、数据库就绪检查、失败恢复上一镜像。
- `scripts/rollback.sh`：回滚应用，保留数据库。
- `gateway/`：独立的 platform 域名共享 Nginx，根路径保留现有 New API，业务按前缀路由。
- `open-meteo/`：自建气象 API 的 Compose 与运行手册，独立 Compose 项目。生产机已实测过一轮并停掉 —— **北京带宽不够，不要装在这台机器上**，见 [自建评估](../docs/2026-09-16-open-meteo-self-host.md) 第八节。
- `../.github/workflows/prod.yml`：手动选择 main 中的提交，经检查后构建镜像、推送 ACR、部署 ECS。

完整配置、Secrets、首次切换和实施状态见 [部署说明](../docs/10-deployment.md)。

## 测试环境

测试入口：`https://test-www.qhzhiyin.com/enersight`。测试 ECS 为 `47.93.60.25`，
部署目录 `/opt/enersight-test`，容器项目 `enersight-test`，回环端口 `8002`。
使用 `docker-compose.test.yml`（上传后命名为 `docker-compose.yml`）与 `.env.test.example`。
数据库和账号分别为 `enersight_test` / `enersight_test_app`；JWT 与生产单独生成。

后端或部署配置推送到 main 后，`.github/workflows/test.yml` 自动检查、构建并部署测试；
生产仍然手动指定提交。测试新增 Secrets 为 `TEST_SSH_HOST` 和 `TEST_SSH_KNOWN_HOSTS`，
其余沿用本仓库已有部署凭据。测试环境不配置人工审批闸门。
测试镜像先在 GitHub runner 上构建并加载，再单独推送 ACR；每次上传限时 15 分钟，
超时或失败最多重试一次，日志管道保留上传命令的退出状态。
Dockerfile 将稳定的 Python 依赖与业务代码分层，日常代码发布只产生小的代码层。
测试发布在确认依赖清单、运行时 Dockerfile 未变且应用文件未删除时，可从最近一次成功部署的
固定摘要镜像叠加当前应用代码；条件不满足时自动完整构建。镜像仍推送 ACR、核对摘要并执行
同一套容器运行检查后才更新测试服务。
增量镜像由 Buildx 直接推送 ACR，以复用基底镜像的仓库分层；完整构建仍在 runner 加载后推送。
仅使用仓库返回的 SHA256 摘要验证和部署镜像。镜像未验证前不更新测试容器。

首次部署需先创建测试数据库和专属普通账号、填写服务器 `.env`（权限 600），
并在花花狗 Nginx 的 server 块加入 `gateway/enersight-test.location.conf` 中的 location。
花花狗仓库也必须保留同一修改，避免下次部署覆盖。该路由只匹配测试域名。
API 通过已有 Docker 网络 `weishen-test_default` 中的唯一别名被 Nginx 访问。
不新增公网端口，ALB 和证书继续沿用现有测试域名配置。

测试主机只有 2 GB 内存，默认限制 640 MB / 0.75 CPU，关闭定时任务及卫星归档。
因此健康检查通过不代表后台地图数据已预热；专项测试采集和地图前应重新评估内存。
数据卷为 `/opt/enersight-test/data`，不复用生产数据，也不自动复制生产用户数据。

2026-09-15 按用户要求，测试后端先恢复直连，随后启用免费代理池试验
（`ENERSIGHT_WEATHER_PROXY_POOL_ENABLED=true`）。当前天气请求通过后台验证的代理出网；
试验结果与恢复步骤见 [部署说明](../docs/10-deployment.md#测试环境免费代理池试验)。
Cloudflare 气象转发已于同日移除，出网方式见 [部署说明「气象出网」](../docs/10-deployment.md#气象出网)。

```sh
# 仓库根目录：构建小程序，产物在 packages/miniapp/dist/weapp
pnpm build:test
pnpm build:prod
# H5 同样支持两套地址，产物在 packages/miniapp/dist/h5
pnpm --filter @enersight/miniapp build:h5:test
pnpm --filter @enersight/miniapp build:h5:prod
# 测试服务器手工回滚应用，数据库迁移不回退
cd /opt/enersight-test
bash scripts/rollback.sh '' test
```

两种命令均为优化构建；`--mode` 选择 `.env.test` 或 `.env.production`。
同一平台的后一次构建覆盖前一次产物，上传前按目标环境重新执行对应命令。
微信小程序测试还需将 `https://test-www.qhzhiyin.com` 加入 request / downloadFile 合法域名。
