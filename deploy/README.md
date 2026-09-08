# 晴川观象生产部署

生产入口：`https://platform.qhzhiyin.com/enersight`。GitHub 私有仓库：`xiaoguo123456/EnerSight`。

- `docker-compose.yml`：单 API 容器，外部 PostgreSQL，主机回环端口 8001。
- `.env.example`：生产配置示例，实际 `.env` 不入库，权限 600。
- `scripts/deploy.sh`：固定摘要镜像发布、数据库就绪检查、失败恢复上一镜像。
- `scripts/rollback.sh`：回滚应用，保留数据库。
- `gateway/`：独立的 platform 域名共享 Nginx，根路径保留现有 New API，业务按前缀路由。
- `../.github/workflows/prod.yml`：手动选择 main 中的提交，经检查后构建镜像、推送 ACR、部署 ECS。

完整配置、Secrets、首次切换和实施状态见 [部署说明](../docs/10-deployment.md)。
