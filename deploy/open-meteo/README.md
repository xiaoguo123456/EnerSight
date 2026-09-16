# 自建 Open-Meteo 运行手册

免费接口的额度（600/分钟、5,000/小时、10,000/天，按坐标数计）和**禁止商用**这两条，
自建都能解决。选型、实测数据与三个方案的对比见
[自建评估](../../docs/2026-09-16-open-meteo-self-host.md)，先读它再动手。

本目录只管气象 API 这一个容器，和 `enersight-prod` 是两个独立 Compose 项目。

## 放在哪台机器

数据在 AWS 开放数据桶 `s3://openmeteo`（us-west-2），容器是**按需读 S3 + 本地缓存**，
不是本地全量库。官方原话是 storage latency 很关键、和 us-west-2 同区域才有合理性能。

**北京生产 ECS 已经实测过，不行。** 2026-09-16 在 `39.105.228.11` 上跑了一轮：
接口与 15 个字段完全正确、热缓存 8.5 ms，但单坐标全字段冷读 92 秒、
20 个分散坐标的一次请求 1,800 秒超时都没取完。瓶颈是跨境带宽
（单流 20–36 KB/s、16 并发 294 KB/s），不是内存 —— 容器峰值只有 60 MB。
数据与过程见[自建评估第八节](../../docs/2026-09-16-open-meteo-self-host.md)。

**现在跑在 Buffalo：`192.236.166.152`**（Ubuntu 24.04、3 vCPU、3.9 GB 内存、57 GB 磁盘）。
到 us-west-2 单流 8.2 MB/s，全冷单坐标 5.6 秒、热缓存 12 ms、100 坐标一次 334 秒，
与官方逐点最大绝对差 0.0000。登录用 `ssh -i ~/.ssh/enersight_openmeteo root@192.236.166.152`。

注意该机器出厂没有任何 DNS，`/etc/systemd/resolved.conf.d/99-dns.conf` 是我们补的；
netplan 由 SolusVM 生成会被覆盖，别改它。

## 批次混用：换主机也躲不掉

实测发现数据桶是**按变量分别更新**的：`meta.json` 已经宣布新一批可用，
但部分变量的滚动时序文件还没重写，于是同一个响应里会混着两个起报批次
（那次是 `cloud_cover` 与 100 m 风还停在上一批，差到 93 个百分点）。

上自建前必须处理其中一项：

- 设 `REMOTE_DATA_DIRECTORY_MINIMUM_AGE`（秒），只用达到该年龄的文件，给批次切换留缓冲；
- 或把我们的 `basis.issued_at` 从 `meta.json` 改成按变量文件修改时间取最小值 ——
  否则起报时刻会报得比实际数据新。

## 前置检查

```bash
free -m            # 实测 100 坐标请求峰值 813 MB，限额别低于 2 GB
df -h /var/lib/docker   # CACHE_SIZE 是预分配，按它再留余量
nproc
curl -sI https://openmeteo.s3.amazonaws.com/data/ecmwf_ifs/static/meta.json | head -1  # 期望 200
# 带宽是生死线：单流低于 1 MB/s 就别部署了
curl -s -o /dev/null -r 0-8388607 -w "单流 8MB: %{time_total}s %{speed_download} B/s\n" \
  https://openmeteo.s3.amazonaws.com/data/ecmwf_ifs/temperature_2m/chunk_987.om
```

## 起服务

```bash
install -d -m 750 /opt/open-meteo   # 放 docker-compose.yml 与可选的 .env
cd /opt/open-meteo && docker compose up -d
docker compose logs -f --tail 50 open-meteo
```

可调项写进同目录 `.env`（都有默认值，不填也能跑）：
`OPEN_METEO_CACHE_SIZE`、`OPEN_METEO_PORT`、`OPEN_METEO_MEM_LIMIT`、
`OPEN_METEO_CPUS`、`OPEN_METEO_LOCATIONS_LIMIT`。

## 验证

第一次请求是冷缓存，慢是正常的。**必须和官方响应逐字段对账后再切流量**：

```bash
P=${OPEN_METEO_PORT:-8090}
Q='latitude=39.9&longitude=116.4&models=ecmwf_ifs&timezone=auto&forecast_days=8&past_days=1&wind_speed_unit=ms'
F='temperature_2m,wind_speed_10m,wind_speed_80m,wind_speed_100m,wind_speed_120m,wind_speed_200m,shortwave_radiation,diffuse_radiation,direct_normal_irradiance,surface_pressure,cloud_cover,weather_code,apparent_temperature,relative_humidity_2m,wind_direction_10m'

# 1) 15 个字段是否都有值（自建）
curl -s "http://127.0.0.1:$P/v1/forecast?$Q&minutely_15=$F" > /tmp/self.json
python3 - <<'PY'
import json
d = json.load(open('/tmp/self.json'))
m = d.get('minutely_15', {})
print('时间点:', len(m.get('time', [])), '起报:', d.get('utc_offset_seconds'))
for k, v in m.items():
    if k == 'time':
        continue
    n = sum(1 for x in v if x is not None)
    print(f'{k:32} 非空 {n}/{len(v)}')
PY

# 2) 同参数打官方，对比同一时刻的数值（会消耗一次官方额度，从本机打、别从生产机打）
curl -s "https://api.open-meteo.com/v1/forecast?$Q&minutely_15=$F" > /tmp/official.json
```

对账要看三件事：字段是否都非空、`minutely_15` 点数是否一致、同一时间戳的数值是否
在浮点误差内。辐射类和风速任一字段整列为空就**不要切**，回到评估文档的字段表排查。

元数据（起报时刻）另外确认一次，我们的 `ModelMeta.parse` 只认这三个键：

```bash
curl -s https://openmeteo.s3.amazonaws.com/data/ecmwf_ifs/static/meta.json \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print({k:d.get(k) for k in ('last_run_initialisation_time','last_run_availability_time','update_interval_seconds')})"
```

## 只让指定来源访问

**Docker 发布端口会绕过 ufw**，所以来源限制必须写在 `DOCKER-USER` 链上。
DNAT 发生在 FORWARD 之前，因此匹配的 `--dport` 是**容器端口 8080**，不是发布的 8090
（两个都挡上更稳妥）。**先加规则、再开端口**，不要留裸开的窗口。

```sh
BJ=39.105.228.11                     # 北京生产 ECS 的出口 IP，与其公网 IP 相同
for P in 8080 8090; do
  iptables -A DOCKER-USER -i eth0 -p tcp --dport $P -s $BJ -j RETURN
  iptables -A DOCKER-USER -i eth0 -p tcp --dport $P -j DROP
done
netfilter-persistent save            # 写入 /etc/iptables/rules.v4，重启后仍生效
```

规则就位后再把 `.env` 的 `OPEN_METEO_BIND` 改成 `0.0.0.0` 并 `docker compose up -d`。
回环版 `.env` 备份成 `.env.bak-loopback`，随时可以退回不对外暴露的状态。

验证必须做两头：

```sh
# 白名单内（在北京机器上跑）——期望 200
curl -s -o /dev/null -w "%{http_code}\n" "http://<US_IP>:8090/v1/forecast?latitude=39.9&longitude=116.4&hourly=temperature_2m&forecast_days=1"
# 白名单外（在任意第三台机器上跑）——期望 curl 退出码 28，包被丢弃
curl -s -o /dev/null --max-time 20 "http://<US_IP>:8090/v1/forecast?latitude=39.9&longitude=116.4&hourly=temperature_2m"
```

出口 IP 变了就会整体断流，换机房或加 NAT 后要重新确认并改规则。
传输是明文 HTTP（公开气象数据）；要 TLS 就再前置一个 nginx。

## 切换应用

不用改代码，三个基址都是 `ENERSIGHT_` 前缀的配置项。在 `/opt/enersight/.env` 里加：

```dotenv
ENERSIGHT_OPEN_METEO_BASE=http://192.236.166.152:8090/v1
ENERSIGHT_OPEN_METEO_ARCHIVE_BASE=http://192.236.166.152:8090/v1
ENERSIGHT_OPEN_METEO_META_BASE=https://openmeteo.s3.amazonaws.com/data
```

`META_BASE` **必须**指数据桶：实例本身的 `/data/{slug}/static/meta.json` 返回 404
（上游把 `FileMiddleware` 注释掉了）。不改不会崩（`model_meta` 异常一律返回 `None`），
但 `basis.issued_at` 会永远取不到，界面上起报时刻空着。

`.env` 由 Compose 在创建容器时读入，改完要重建：

```bash
cd /opt/enersight && set -a && . ./.release.env && set +a && docker compose up -d --no-deps api
```

注意 `open_meteo_archive_base` 也是同一个 `/v1`（自建二进制里 `/v1/archive` 就是 ERA5），
不是官方那个独立的 `archive-api` 域名。

切换后**先只改测试环境**，观察一轮全目录扫描的耗时与失败率，再动生产。
自建实例单请求比官方慢，以下配置项可能要一起调（都在 `app/config.py`，有 env 覆盖）：

| 配置项 | 现值 | 自建后 |
| --- | --- | --- |
| `ENERSIGHT_UPSTREAM_UNITS_PER_MINUTE` | 480 | 不再受上游额度约束，改成按自建实例的并发能力定 |
| `ENERSIGHT_FLEET_COORDS_PER_MINUTE` | 480 | 同上；坐标限速的原因消失了 |
| `ENERSIGHT_FLEET_COORDS_PER_REQUEST` | 100 | 可提到 `LOCATIONS_LIMIT`（默认 1000） |
| `ENERSIGHT_UPSTREAM_RETRIES` | 3 | 保留；自建实例冷缓存超时比官方常见 |

缓存层（`forecast_refreshes_per_day`、`fleet_refresh_hour`、按内容指纹落盘）**不要拆**。
自建是慢在冷请求上，缓存比之前更重要，不是更不重要。

## 回退

自建实例出问题，把上面三行从 `.env` 删掉再重建 API 容器即可回到官方直连；
气象缓存与落盘格式不变，不需要清数据。自建容器可以留着不动。

```bash
cd /opt/enersight
cp -p .env ".env.before-rollback-$(date +%Y%m%dT%H%M%S)" && chmod 600 .env.before-*
# 删掉三个 ENERSIGHT_OPEN_METEO_* 行后
set -a && . ./.release.env && set +a && docker compose up -d --no-deps api
```

## 磁盘

缓存在命名卷 `open-meteo-data`（`/var/lib/docker/volumes/open-meteo_open-meteo-data`）。
`CACHE_SIZE` 是上限，卷要留出比它更多的空间。要放到独立数据盘，先把盘挂到
`/var/lib/docker` 所在位置之外，再改用绑定挂载 —— 那时必须自己把目录 chown 给镜像内
的 `openmeteo` 用户，uid 查法：

```bash
docker run --rm --entrypoint id ghcr.io/open-meteo/open-meteo@sha256:e1517a01a061fd96e2a9017d022c5809bb51725bcd733bcbed435e7ea32dd9da
```

**不要**改成本地全量同步（`sync` 子命令）：`data/ecmwf_ifs/` 每个变量每 21 天的
chunk 就是 2.0–2.3 GB，我们要的十几个变量一个 chunk 组约 30 GB，且每批起报都会重写
整个文件。实测数据见评估文档。

## 升级

改本目录 `docker-compose.yml` 里的摘要并提交（项目规则：固定摘要、禁止 latest）。
取新摘要：

```bash
TOKEN=$(curl -s "https://ghcr.io/token?service=ghcr.io&scope=repository:open-meteo/open-meteo:pull" | python3 -c "import json,sys;print(json.load(sys.stdin)['token'])")
curl -sI -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.oci.image.index.v1+json" \
  https://ghcr.io/v2/open-meteo/open-meteo/manifests/<版本号> | grep -i docker-content-digest
```

升级后重跑上面的 15 字段对账再放流量。

## 许可与署名

- 代码 AGPL-3.0：直接跑官方镜像没问题；**一旦改了代码并对外提供服务，就要公开改动**。
- 数据 CC BY 4.0：署名要求不变，见 docs/09 的署名清单。
- 自建对商用与非商用都开放 —— 这是它相对免费接口的关键差别，免费接口禁止商用。
