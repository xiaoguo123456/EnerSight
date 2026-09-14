# 气象转发 Worker

公司网络要求业务服务器不直接以 IP 访问外部服务，后端的 Open-Meteo 请求统一经受控域名的 Cloudflare Worker 转发。测试与生产共用 `worker.mjs`，各建独立 Worker、密钥和来源 IP 限制。后端配置见 [部署说明「气象转发」](../../docs/10-deployment.md#气象转发)。

| 环境 | Worker | 域名 | `ALLOWED_IPS` | 状态 |
| --- | --- | --- | --- | --- |
| 测试 | `enersight-weather-test` | `weather-test.weishenai.cn` | `47.93.60.25` | 2026-09-14 已建立并验收 |
| 生产 | `enersight-weather-prod` | `weather.weishenai.cn` | `39.105.228.11` | 2026-09-14 已建立并验收 |

## 建立

1. 在目标服务器上确认出口 IP：`curl -s https://checkip.amazonaws.com`。经 NAT 出网时可能与 ECS 地址不同，以实测为准。
2. Cloudflare 控制台新建 Worker，粘贴本目录 `worker.mjs` 全部内容。
3. 变量：`ALLOWED_IPS` 为文本，多个 IP 用逗号分隔；`RELAY_SECRET` 为机密，在目标服务器上用 `openssl rand -hex 32` 生成，同时写入服务器 `.env` 的 `ENERSIGHT_WEATHER_RELAY_TOKEN`。
4. 绑定自定义域名，关闭 workers.dev 与预览地址。

也可用 `wrangler.jsonc`：`npx wrangler secret put RELAY_SECRET [--env production]`，再 `npx wrangler deploy [--env production]`。修改代码时先部署测试 Worker 验收，再同步生产。

## 行为

- 只接受 GET，先校验来源 IP（`CF-Connecting-IP`），再校验 Bearer 密钥。
- 只放行 `/v1/forecast`、`/v1/archive` 与三种模型的 `meta.json`；参数名、模型、坐标数（不超过 100）走白名单，其余返回 400。后端新增参数或模型时需同步这里。
- 上游响应原样流式透传，包括 429 与 `Retry-After`，并带 `X-Weather-Relay: cloudflare`。Worker 自身的拒绝不带该头，后端据此判定为转发异常，不重试。
- 不重试、不缓存、不跟随重定向，不向上游传递密钥或 Cookie。
- 不含原生地图栅格文件下载。

## 验收

在目标服务器部署目录执行，不请求 Open-Meteo，不打印密钥。预期依次为 401 与 200：

```sh
B=$(sed -n 's/^ENERSIGHT_WEATHER_RELAY_BASE=//p' .env); T=$(sed -n 's/^ENERSIGHT_WEATHER_RELAY_TOKEN=//p' .env)
curl -s -o /dev/null -w '%{http_code}\n' "$B/health"
curl -s -w '\n%{http_code}\n' -H "Authorization: Bearer $T" "$B/health"; unset T B
```

单元测试：`node --test deploy/weather-relay/worker.test.mjs`。

测试环境额度排查见 [2026-09-14 排查记录](../../docs/2026-09-14-weather-quota-audit.md)。
