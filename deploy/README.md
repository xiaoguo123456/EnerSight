# EnerSight 生产部署

固定摘要的 Docker 镜像 + 单服务 Compose，数据库用外部 PostgreSQL。完整说明见
[docs/10-deployment.md](../docs/10-deployment.md)。

```
deploy/
├── docker-compose.yml      只跑 api 一个服务，镜像写固定版本 + 摘要
├── .env.example            复制为 .env 后填写，chmod 600，不提交
└── scripts/
    ├── preflight.sh        环境、.env、端口、Compose 语法检查，不输出敏感值
    ├── deploy.sh           预检 → 拉镜像 → 原地更新 → 等 /health
    └── rollback.sh         传入上一固定镜像回滚，不回退数据库迁移
```

服务器目录建议 `/opt/enersight/`，用 `git archive` 只上传已提交文件，`.env` 单独 scp。
