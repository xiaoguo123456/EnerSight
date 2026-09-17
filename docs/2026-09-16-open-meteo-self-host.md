# 自建 Open-Meteo 可行性评估

评估日期：2026-09-16。起因是免费接口日额度反复耗尽，见
[气象日额度排查](./2026-09-14-weather-quota-audit.md)。
运行手册见 [deploy/open-meteo/README.md](../deploy/open-meteo/README.md)。

**方案 A 已落地**：气象实例跑在 Buffalo 的 `192.236.166.152`，字段与官方逐点一致、
延迟能追上上游节奏，见第九节。北京生产 ECS 也实测过，**带宽差两个数量级、不可用**，
验证容器已删净，见第八节。第八、九节的实测数字优先于前面几节的估算。

**接口面已全部对账等价**（四个模型、`cell_selection=sea`、ERA5 归档，逐点最大绝对差
0.0000），对外暴露按 IP 白名单落地、北京已能访问。**但还没切流量** ——
还差 `meta.json` 改指数据桶、批次错位处理、兜底监控、限流重定四件事，见第九节第 8 小节。

## 一、结论

1. **接口完全兼容，已逐项实测。** 四个模型（`best_match` / `ecmwf_ifs` / `gfs_global` /
   `icon_global`）、15 个字段、`minutely_15`、8+1 天、`cell_selection=sea`、
   `/v1/archive`（ERA5），自建与官方**非空点数相同、逐点最大绝对差 0.0000**。
   应用侧不用改代码，三个基址都是现成的配置项 —— 但 `meta.json` 必须指数据桶，
   实例自己不提供（404）。
2. **生产 ECS 实测不可用，而且原因不是内存。** 容器在 2 GB 限额下峰值只用了
   60 MB、CPU 不到 1%，内存从头到尾不是瓶颈 —— 这一点和上游文档给的
   「8 GB 起、推荐 16 GB」对不上，至少对我们这种固定坐标的读法不成立。
   真正卡死的是**跨境带宽**：北京到 us-west-2 单流 20–36 KB/s，16 并发合计
   294 KB/s。20 个分散坐标的一次全字段请求跑到 1,800 秒超时都没取完。见第八节。
3. **要自建就放美西**，北京 ECS 走 HTTPS 调它。这样跨太平洋只走小 JSON，
   重的 S3 读取留在同区域。
4. **额度问题是结构性的，调参解决不了。** 按实测的 2,747 个唯一坐标实算，
   一轮全目录就要 4,120 个额度单位，免费层 10,000/天最多撑两轮，而
   `fleet_fetch_rounds_per_day` 是 4。第五节两项不需要新服务器的改动
   （元数据改指数据桶、裁掉两个没有信息量的风速字段，权重 1.5 → 1.3）
   只能争取余量；根治要么自建，要么买订阅。
5. 顺带解决一件合规事项：免费接口**禁止商用**（docs/09 里那条未勾选的
   「Open-Meteo 的商用条款已确认」）。自建对商用开放，付费订阅也开放。

## 二、字段逐条核对

默认路径是 `best_match`，境内实测等于 `ecmwf_ifs`（`services/model_resolution.py`
每日复核）。`models=ecmwf_ifs` 在上游解析为 `EcmwfEcpdsReader(domain: .ifs)`，
读 `data/ecmwf_ifs/`，**逐小时**（`temporal_resolution_seconds: 3600`）。

注意别和 `ecmwf_ifs025` 混：那是另一个域，3 小时分辨率、只有 100 m 风分量。

桶里 `data/ecmwf_ifs/` 的原始变量含 `wind_u/v_component_10m / 100m / 200m`、
`temperature_2m`、`dew_point_2m`、`shortwave_radiation`、`direct_radiation`、
`pressure_msl`、`cloud_cover`、`precipitation` 等。我们的 15 个字段落位如下：

| 我们请求的字段 | 自建 `ecmwf_ifs` 来源 |
| --- | --- |
| `temperature_2m` | 原始变量 |
| `cloud_cover` | 原始变量 |
| `shortwave_radiation` | 原始变量 |
| `wind_speed_10m` / `wind_direction_10m` | 由 10 m u/v 合成 |
| `wind_speed_100m` | 由 100 m u/v 合成 |
| `wind_speed_200m` | 由 200 m u/v 合成 |
| `wind_speed_80m` | **100 m 风速 × 0.97468**（固定系数，见第三节） |
| `wind_speed_120m` | **100 m 风速 × 1.02068**（固定系数） |
| `surface_pressure` | 由 `pressure_msl` + 目标高程反算 |
| `relative_humidity_2m` | 由 `temperature_2m` + `dew_point_2m` 算 |
| `apparent_temperature` | 由气温、湿度、10 m 风速、短波辐射算 |
| `diffuse_radiation` | 由短波与直射辐射分解 |
| `direct_normal_irradiance` | 由直射辐射按太阳位置换算 |
| `weather_code` | 由云量、降水等派生 |

其余接口面：

- `best_match` / `ecmwf_ifs` / `gfs_global` / `icon_global` 都是同一个二进制里的
  `MultiDomains` 枚举项，`params["models"]` 原样发过去即可。
- `minutely_15` 由 API 层从逐小时插值（和官方同一份代码，境内本来就是插值来的，
  见 docs/07 §2.7），行为不变。
- `/v1/archive`（ERA5）在同一个二进制里，桶里有 `copernicus_era5`，
  所以 `scripts/calibrate.py` 也能指向自建实例 —— 但基址是自建的 `/v1`，
  不是官方那个独立的 `archive-api` 域名。
- 模型元数据在桶里就有：`https://openmeteo.s3.amazonaws.com/data/{slug}/static/meta.json`
  返回 200，含 `last_run_initialisation_time`、`last_run_availability_time`、
  `update_interval_seconds` —— 正好是 `ModelMeta.parse` 读的三个键。
- `LOCATIONS_LIMIT` 默认 1000，自建后单请求坐标数可以从 100 提上去。

## 三、顺带查出来的问题：两个风速字段没有信息量

上游 `wind_speed_80m` 与 `wind_speed_120m` 对 `ecmwf_ifs` 不是独立层，而是
100 m 风速乘一个**固定对数系数**（FAO-56 的 `scaleWindFactor`，只看高度，
不含地表粗糙度与稳定度）：

```
80 m  = 100 m × 0.97468
120 m = 100 m × 1.02068
```

也就是说，我们现在每次请求都在为两个可以本地算出来的数付费，
而 `metrics/wind` 在 80/100/120 之间做的「按层插值」，插的是同一个数的三个倍数。
真正独立的层只有 10 m、100 m、200 m。

这和模型有关，不能一刀切：`dwd_icon`（`icon_global`）桶里确实有原生
`wind_u/v_component_80m / 120m / 180m`，是真层；`ncep_gfs013`（`gfs_global`）
只有 10 m，其余全靠派生。所以字段清单要按模型分，不是直接删。

CLAUDE.md 里「按层插值，α 只是缺数据时的降级」这条对 `icon_global` 成立，
对默认的 `ecmwf_ifs` 不成立 —— 那条路径上我们拿到的本来就是固定 α 的结果。

## 四、体量与官方性能说明

| 项 | 实测 |
| --- | --- |
| 镜像 | `ghcr.io/open-meteo/open-meteo:1.6.0`，11 层，压缩后约 225 MB，amd64 / arm64 都有 |
| 数据位置 | `s3://openmeteo`，us-west-2，AWS 开放数据，无需凭据 |
| `data/ecmwf_ifs/` 单变量单 chunk | 504 小时（21 天）一个文件，约 2.0–2.3 GB |
| 同目录历史归档 | `year_YYYY.om` 每变量每年 20–36 GB |
| `data/ecmwf_ifs025/` 单变量单 chunk | 104 个 3 小时步，约 67–72 MB |
| 官方硬件要求 | 8 GB 内存起、推荐 16 GB；≥100 GB NVMe（磁盘同时当缓存） |
| 缓存默认 | `CACHE_SIZE` 10 GB，可设到 TB 级；块大小 64 KB |

两条由此得出的判断：

- **不要做本地全量同步。** 我们需要的十几个变量，一个 chunk 组就约 30 GB，
  而且每批起报都会重写整个 chunk 文件，跨境链路撑不住。
- **只能用 `REMOTE_DATA_DIRECTORY` + 本地 LRU 缓存**，也就是按需读字节区间。
  官方说一次冷请求要读「几百个文件、几 MB」，秒级；缓存热了才快。
  这正是「和 us-west-2 同区域」那条建议的原因。

自建实例单请求比官方**慢**，所以现有缓存层（`forecast_refreshes_per_day`、
`fleet_refresh_hour`、按内容指纹落盘）不能因为「没额度限制了」就拆，反而更重要。

## 五、不需要新服务器、今天就能做的两件事

1. **元数据改指数据桶**，从 API 主机上挪走（一行配置，已验证返回值一致）：

   ```dotenv
   ENERSIGHT_OPEN_METEO_META_BASE=https://openmeteo.s3.amazonaws.com/data
   ```

   官方 `api.open-meteo.com/data/` 大概是 nginx 直接发静态文件、不走限流器，
   所以这一项**不一定**在消耗额度；改过去的价值是把它彻底移出争议范围，顺带少一跳。

2. **按模型裁字段清单**，默认路径去掉 `wind_speed_80m`、`wind_speed_120m`
   （本地用固定系数还原，或让 `metrics/wind` 直接从 10/100/200 m 插值）。
   `request_cost` 是 `坐标数 × max(1, 字段数/10) × max(1, 天数/14)`，
   15 → 13 个字段就是每次请求的权重 **1.5 → 1.3，降 13%**。
   这是改代码，要动 `HOURLY_FIELDS`、`metrics/wind` 和对应测试，得单独做一轮。

### 但把账算完：免费层撑不住现在的轮数

按实测的 2,747 个唯一坐标、8 天窗口，用 `request_cost` 实算一轮全目录：

| 字段数 | 单坐标权重 | 一轮全目录 | 占免费日额度 10,000 |
| --- | --- | --- | --- |
| 15（现状） | 1.5 | 4,120 | 41% |
| 13（裁掉两个风速层） | 1.3 | 3,571 | 36% |

也就是说免费额度最多只够**两轮**全量，加上单站预报和其他入口就更紧。
`fleet_fetch_rounds_per_day` 现在是 4。所以额度问题是结构性的，
不是调参能解决的 —— 上面两项改动争取到的是余量，不是根治。
根治只有两条路：自建（方案 A）或付费订阅（方案 C）。

## 六、三个方案对比

| | A 美西自建（**已部署**） | B 北京生产 ECS 自建 | C 买官方 Standard |
| --- | --- | --- | --- |
| 额度 | 无上游限制 | 无上游限制 | 100 万次/月，分钟/小时/日限流取消 |
| 商用 | 允许 | 允许 | 允许 |
| 月成本 | 已买：3 vCPU / 3.9 GB / 57 GB，HostPapa Buffalo | 现有机器，但带宽不够 | $29（docs/04 §二已记） |
| 性能 | 热缓存 12 ms、全冷单坐标 5.6 s，见第九节 | **实测不可用**，见第八节 | 官方专用端点，实测 733 ms |
| 运维 | 我们自己兜，镜像升级要重跑字段对账 | —— | 无 |
| 风险 | 无 SLA（自己的机器） | —— | 依赖第三方，但有 SLA 目标 |
| 额外收益 | ERA5、全模型随便用；坐标限速可取消 | 同左 | 历史/集合类接口要升到 Professional |

我们的量级按现状实算：4,120 × 4 轮 ≈ 16,480/天 ≈ 49 万/月，在 C 的 100 万次/月
里够用，余量约一倍；而 C 的月费比 A 那台机器还便宜。所以：**如果目标只是「别再超额度」，C 加上第五节两项改动最省事；
自建的价值在于彻底摆脱按调用计费、以及 ERA5 与全模型随取**。这是取舍，由你定。

## 七、已经准备好的东西

- [deploy/open-meteo/docker-compose.yml](../deploy/open-meteo/docker-compose.yml)：
  固定摘要、只开回环、按需读 S3、32 GB 缓存、健康检查探一次最小预报。
- [deploy/open-meteo/README.md](../deploy/open-meteo/README.md)：
  前置检查、起服务、**15 字段对账脚本**、来源白名单、应用侧切换与回退、磁盘与升级、许可与署名。

## 八、生产机实测（2026-09-16 夜，北京时间）

在生产 ECS `39.105.228.11` 上用 `/opt/open-meteo` 起了一个独立 Compose 项目做验证：
回环 8090、`mem_limit=2g`、`cpus=1.5`、`CACHE_SIZE=8GB`，镜像固定摘要
`sha256:e1517a01…`（`1.6.0`，落盘 682 MB）。**没有接任何应用流量，没有改
`/opt/enersight/.env`，验证完已 `docker compose stop`。**

### 1. 机器现状

4 vCPU、总内存 7,462 MB（可用约 4,200 MB）、磁盘 99 GB 可用 56 GB，
上面已有 17 个容器（new-api、steward ×6、weishen ×6、yingji ×2、platform 网关、
我们的 API）。缓存文件按 `CACHE_SIZE` 预分配，占掉 8 GB，停容器后磁盘剩 48 GB。

### 2. 出网带宽（这就是结论）

对 `data/ecmwf_ifs/temperature_2m/chunk_987.om` 直接发 Range 请求：

| 方式 | 结果 |
| --- | --- |
| 单流 8 MB | 412 s（20 KB/s）；重测 232 s（36 KB/s） |
| 串行 10 × 64 KB | 28.5 s，约每块 2.85 s |
| 16 并发 × 512 KB | 8 MB / 27.8 s，合计 **294 KB/s** |
| TLS 连接建立 | 约 0.34 s（RTT 约 170 ms） |

并发能把吞吐拉高 14 倍，但天花板就是 0.3 MB/s 量级。

### 3. 接口与字段：完全正确

生产形态请求（1 坐标、15 字段、`minutely_15`、`forecast_days=8&past_days=1`、
`models=ecmwf_ifs`、`timezone=auto`）返回 HTTP 200、76,186 B、864 个点
（9 天 × 96），**15 个字段全部 864/864 非空**，`elevation=49.0`、
`timezone=Asia/Shanghai`、`utc_offset=28800`，与官方响应的坐标与高程完全一致。
第二节靠源码推的字段结论，实测全部成立。

与官方同参数响应逐点对比（864 个点）：

| 字段 | 最大绝对差 |
| --- | --- |
| `temperature_2m` / `wind_speed_10m` / `wind_speed_200m` / 三个辐射量 / `surface_pressure` / `apparent_temperature` / `relative_humidity_2m` / `wind_direction_10m` | **0** |
| `wind_speed_100m`（连带 80m / 120m） | 3.3 m/s |
| `cloud_cover` | 93 个百分点 |
| `weather_code` | 42 |

### 4. 差异的原因：数据桶按变量分别更新

不一致的点全部落在 `2026-09-16T14:00`（= 06:00 UTC）及以后，而当时桶里
`meta.json` 的 `last_run_initialisation_time` 正是 **06z**、
`last_run_availability_time` 是 12:03 UTC。容器日志随后出现：

```
OmFileRemoteOmReader: ... Updated file data/ecmwf_ifs/cloud_cover/chunk_986.om (old size=... new size=...)
Remote S3 file system background update took 169s
```

也就是说：`meta.json` 已经宣布 06z 可用，但 `cloud_cover`、`wind_u/v_component_100m`
这几个变量的滚动时序文件**还没被重写**，我们读到的是上一批（00z）的值，
而官方生产库已经是 06z。**同一个响应里可以混着两个起报批次。**

这条和主机位置无关，去美西也一样，必须处理：

- `REMOTE_DATA_DIRECTORY_MINIMUM_AGE`（上游 `configure.swift` 里的环境变量）
  可以只使用达到指定年龄的文件，用它给批次切换留缓冲。
- 我们的 `basis.issued_at` 取自 `meta.json`，在这个窗口里会**报得比实际数据新**。
  切自建前要么设上面那个变量，要么把 `issued_at` 改成按变量文件的修改时间取最小值。

### 5. 冷读延迟：北京这台机器废在这里

| 请求 | 冷缓存 | 热缓存 |
| --- | --- | --- |
| 1 坐标 / 1 字段 / 1 天 | 4.9 ms | 2.0 ms |
| 1 坐标 / 15 字段 / 9 天 | **92.3 s** | 8.5 ms |
| 邻近坐标（+0.3°）/ 15 字段 / 9 天 | 22.2 s | —— |
| 远距坐标（乌鲁木齐）/ 15 字段 / 9 天 | 50.2 s | —— |
| **20 个分散坐标一次请求** | **1,800 s 超时，响应被截断** | 181 s |

热缓存是毫秒级，说明实现没问题；问题全在把块搬过来。20 个坐标半小时取不完，
全目录 2,747 个坐标按这个速率要几十小时，而上游每 6 小时出一批新数据 ——
**永远追不上**。容器峰值内存 60 MB、CPU 0.2%，加内存加 CPU 都没用。

### 6. 对生产的影响：无

全程只读，没改应用配置，没动别人的容器，没有 `docker prune`。验证后
`enersight-prod-api-1` 仍 healthy，本机与公网 `/enersight/ready` 均 200，
内存回到可用 4,207 MB。容器已停但保留，随时可以起回来看：

```bash
ssh -i ~/.ssh/huahuadog_deploy root@39.105.228.11 'cd /opt/open-meteo && docker compose start'
```

不需要了就 `docker compose down -v && docker image rm <摘要>`，回收 8 GB 缓存与 682 MB 镜像。

## 九、美西自建机：已部署并验证（2026-09-16）

方案 A 已落地。机器是用户当天购买的 `192.236.166.152`，**Buffalo NY（HostPapa，AS36352）**，
不在 us-west-2，但到数据桶的带宽完全够用。

### 1. 机器与环境

| 项 | 值 |
| --- | --- |
| 系统 / 架构 | Ubuntu 24.04 LTS / x86_64 |
| 规格 | 3 vCPU、3,915 MB 内存、磁盘 57 GB（部署前可用 52 GB） |
| Docker | 发行版仓库的 `docker.io` 29.1.3 + `docker-compose-v2` 2.40.3 |
| 登录 | `ssh -i ~/.ssh/enersight_openmeteo root@192.236.166.152`（专用无口令部署钥） |
| 部署目录 | `/opt/open-meteo`，Compose 项目 `open-meteo`，端口 **仅回环 8090** |
| 限额 | `mem_limit=2g`、`cpus=2`、`CACHE_SIZE=24GB`（预分配，部署后磁盘剩 26 GB） |

**出厂没有配置任何 DNS**：netplan（SolusVM 生成）和 systemd-resolved 都没有 nameserver，
所有域名解析失败，但出网本身是通的。已补 `/etc/systemd/resolved.conf.d/99-dns.conf`
（`DNS=1.1.1.1 8.8.8.8`、`FallbackDNS=9.9.9.9 1.0.0.1`）。不要去改 netplan，cloud-init 会覆盖。

### 2. 带宽：比北京快两个数量级

同一个文件、同样的测法：

| 测法 | Buffalo | 北京生产 ECS |
| --- | --- | --- |
| TLS 连接建立 | 0.113 s | 0.343 s |
| 单流 8 MB | **1.03 s（8.18 MB/s）** | 412 s（20 KB/s） |
| 16 并发 × 512 KB | 1.11 s（7.3 MB/s） | 27.8 s（294 KB/s） |
| 镜像拉取（682 MB 落盘） | 14 s | 数分钟 |

单流已经跑满，并发不再增益，说明瓶颈是链路带宽而不是并发度。

### 3. 延迟：能追上上游出数节奏

| 请求 | 冷缓存 | 热缓存 | 北京冷（对比） |
| --- | --- | --- | --- |
| 1 坐标 / 15 字段 / 9 天 | **5.58 s** | 10 ms | 92.3 s |
| 乌鲁木齐 1 坐标 | **2.08 s** | —— | 50.2 s |
| 20 个分散坐标一次请求 | **82.2 s** | 145 ms | 1,800 s 超时未取完 |
| **100 个分散坐标一次请求** | **333.8 s**（7.56 MB 完整） | —— | 未测 |

100 坐标 334 秒 ≈ **每坐标 3.34 秒**。全目录 2,747 个坐标全冷推算约 **2.5 小时**，
上游每 6 小时出一批 —— 追得上，且有余量。热缓存下 20 坐标只要 145 ms，
第二轮之后只需要补新批次变动的块，会远快于首轮。

容器在 100 坐标那次峰值内存 **813 MB / 2 GB**，CPU 0.12%，累计收流量 175 MB。
2 GB 限额是合适的，3.9 GB 的机器够用但不宽裕；**不要把限额压到 1 GB 以下**。

### 4. 与官方响应逐点对账：完全一致

在这台机器上同参数同时打自建与官方（1 坐标、15 字段、`minutely_15`、8+1 天、`models=ecmwf_ifs`）：

- 坐标 `(39.89455, 116.35983)`、高程 `49.0`、864 个点，两边**完全相同**。
- **15 个字段全部 864/864 非空，逐点最大绝对差 0.0000**，无一字段有差异。
- 响应体 76,135 B vs 76,136 B（差的是 `generationtime_ms`）。
- 自建 **12 ms**，官方 733 ms。

这同时说明北京那次观察到的 `cloud_cover` 差 93 个百分点、100 m 风差 3.3 m/s
**不是系统性差异**，而是第八节第 4 小节那个批次错位窗口，桶追平后就一致了。

### 5. 批次错位的实测量级

同一个 chunk、不同变量的 `Last-Modified` 实测跨度约 **30 分钟**
（`wind_u_component_200m` 06:23:54 GMT 最早，`shortwave_radiation` 06:56:20 GMT 最晚）。
也就是每批起报落地后有约半小时的窗口，响应里可能混两个批次。

上生产流量前要处理其中一项（都还没做）：

- 验证并设置 `REMOTE_DATA_DIRECTORY_MINIMUM_AGE`（秒）—— 这个变量的确切语义还没实测，
  设错会反过来一直读旧数据，必须先在这台机器上验；
- 或把 `basis.issued_at` 从 `meta.json` 改成按变量文件修改时间取最小值。

`fleet_refresh_hour=8`（北京时间）正好是 00:00 UTC，紧贴 00z 批次边界，属于高风险时刻。

### 6. 对外暴露：IP 白名单（已按用户选择落地）

按用户决定采用**公网端口 + 来源 IP 白名单**。规则先就位、再开端口，中间不存在裸开窗口。

北京侧出口 IP 确认为 `39.105.228.11`（checkip.amazonaws.com / ifconfig.co / myip.ipip.net
三家一致，与其公网 IP 相同）。

```sh
# Docker 发布端口绕过 ufw，必须用 DOCKER-USER 链；DNAT 在 FORWARD 之前，所以 dport 是容器端口 8080
for P in 8080 8090; do
  iptables -A DOCKER-USER -i eth0 -p tcp --dport $P -s 39.105.228.11 -j RETURN
  iptables -A DOCKER-USER -i eth0 -p tcp --dport $P -j DROP
done
netfilter-persistent save          # 写入 /etc/iptables/rules.v4，重启后仍生效
```

然后 `.env` 里 `OPEN_METEO_BIND=0.0.0.0`、`docker compose up -d`。
回环版 `.env` 备份在 `/opt/open-meteo/.env.bak-loopback`。

验证：

| 来源 | 结果 |
| --- | --- |
| 北京 ECS 宿主机 | HTTP 200，connect 0.218 s、total 1.31 s |
| 北京 `enersight-prod-api-1` **容器内** | HTTP 200，0.48 s，842 B |
| 本机（白名单外） | HTTP 000，`curl` 退出码 28 —— 包被丢弃 |

eth0 只有 IPv4 与链路本地 IPv6，发布规格是 IPv4-only，不存在 IPv6 绕过。
传输是明文 HTTP（公开气象数据）；要 TLS 可以再前置 nginx，目前没做。

### 7. 全部接口面逐项对账：与官方完全等价

在 Buffalo 机器上同参数同时打自建与官方，总点数 864（`icon_global` 852，两边一致）：

| 接口面 | 自建 | 与官方差异 |
| --- | --- | --- |
| `models=ecmwf_ifs` | 200，热 10 ms | 15 字段全 864/864，**最大绝对差 0.0000** |
| `models=best_match` | 200，冷 13.3 s | 非空数相同，**无任何数值差异** |
| `models=gfs_global` | 200 | 非空数相同，**无任何数值差异** |
| `models=icon_global` | 200，冷 7.6 s | 非空数相同，**无任何数值差异** |
| `cell_selection=sea`（海上风电） | 200，1.09 s | —— |
| `/v1/archive`（ERA5，校准脚本用） | 200，9.3 s，168 点 | 15 字段逐点 **0.0000**，响应同为 15,636 B |
| 多坐标 100 个一次 | 200，7.56 MB 完整 | `LOCATIONS_LIMIT=1000` |

两个「本来就没有」的空字段，自建与官方**同样为空**，不是自建引入的回归：

- `gfs_global` 的 `wind_speed_200m`：两边都 0/864（GFS 开放数据只有 10 m 风）。
- ERA5 的 `wind_speed_80m / 120m / 200m`：两边都 0/168（ERA5 只有 10 m 与 100 m）。

**`meta.json` 是唯一一处不能指向自建实例的**：`/data/{slug}/static/meta.json` 在实例上
返回 **404**（`FileMiddleware` 在上游是注释掉的）。三个 slug 都必须指向数据桶：

```dotenv
ENERSIGHT_OPEN_METEO_META_BASE=https://openmeteo.s3.amazonaws.com/data
```

不改这一项不会崩（`model_meta` 任何异常都返回 `None`），但 `basis.issued_at`
会一直取不到，界面上起报时刻永远空着。

### 8. 切流量前还差的四件事

1. **`meta.json` 改指数据桶** —— 上面那一行，必须做。
2. **批次错位处理** —— 见第 5 小节，`REMOTE_DATA_DIRECTORY_MINIMUM_AGE` 的语义要先实测。
3. **兜底与监控** —— 应用目前**没有**「自建挂了退回官方」的逻辑，这台 VPS 是单点、
   无 SLA、无监控。它一挂，首页、趋势、预警、预测、地图图层的气象全断。
   要么加 fallback，要么至少加健康探测 + 明确的快速回退步骤。
4. **限流参数重定与整轮压测** —— `upstream_units_per_minute=480`、
   `fleet_coords_per_minute=480` 是为上游额度设的，额度概念已消失，但它们同时在保护
   这台 3.9 GB 的小机器（100 坐标那次峰值 813 MB）。不能直接调大，要压测；
   全目录整轮 2,747 坐标也还没真跑过。

切流量本身只是北京侧 `/opt/enersight/.env` 加三行再重建 API 容器：

```dotenv
ENERSIGHT_OPEN_METEO_BASE=http://192.236.166.152:8090/v1
ENERSIGHT_OPEN_METEO_ARCHIVE_BASE=http://192.236.166.152:8090/v1
ENERSIGHT_OPEN_METEO_META_BASE=https://openmeteo.s3.amazonaws.com/data
```

```sh
cd /opt/enersight && set -a && . ./.release.env && set +a && docker compose up -d --no-deps api
```

回退就是删掉这三行再重建，气象缓存与落盘格式不变，不需要清数据。

## 十、兜底与监控（已实现）

自建实例是单点、无 SLA。应用侧加了主备与健康探测，全部收在
`providers/weather_transport.weather_get` 这一层 —— 它本来就是唯一处理预算与限流归属的
地方，四个调用点（单站预报、归档、全目录、地图网格、模型复核）都经过它。

### 1. 主备规则

| 情况 | 行为 |
| --- | --- |
| 没配 `open_meteo_fallback_base` | 与以前完全一致，一行代码都不绕 |
| 主源传输错误 / 5xx | 换成官方地址重试一次 |
| 主源 4xx | **不退回**。400 是坐标越界、429 是配额，换地址是同样结果 |
| 主源 429 | 不退回，但仍按窗口冷却共享预算 —— 「429 必冷却」不能因为换了主源就破掉 |
| 连续失败到 `weather_primary_trip_after`（3 次） | 熔断 `weather_primary_probe_seconds`（120 秒），期间直接走兜底，不让每个请求先白等一次超时 |
| 熔断到期 | 放一个请求去探主源，成功即恢复 |

两条刻意的边界：

- **兜底只给页面请求。** `background=True` 的后台批量路径（全目录、地图网格、模型复核）
  不退回官方 —— 它们一轮几千个坐标额度，退回照样超额，还会把页面那点额度一起吃掉。
  主源不可用就沿用旧快照，全目录本来就按「部分覆盖」处理。
  `allow_fallback` 不传时等于 `not background`，只在需要例外时显式传。
- **兜底不经代理池，且代理池已关。** 自建与代理池是互替的两条策略，叠起来一次故障会
  同时动用两套限流账本，排查时说不清是谁在限。

`meta.json` 走数据桶，不属于主源，不会被改写。
主源那一跳打的是我们自己的实例、不花官方额度，所以主备两跳在入口只计一次预算。

### 2. 监控

`scheduler` 注册 `weather_upstream`，每 5 分钟探一次主源（一个坐标、一个字段、一天，
自建上是缓存命中）。探测**绕过熔断**直接打主源，否则熔断后永远不会恢复。

这个任务放在总开关之前注册：测试环境 `enable_scheduler=false`，但主源挂了也要能看出来，
不能等页面报错才发现。健康 → INFO，异常 → WARNING，熔断与恢复各写一条
ERROR / WARNING。日志里能直接看到完整状态：

```
weather_upstream: {'primary_base': 'http://192.236.166.152:8090/v1',
 'fallback_base': 'https://api.open-meteo.com/v1', 'fallback_enabled': True,
 'healthy': True, 'breaker_open': False, 'consecutive_failures': 0, ...}
```

`/v1/debug/weather-upstream` 返回同一份状态，但**只在 debug 模式挂载** ——
线上与测试都是 `ENERSIGHT_DEBUG=false`，看上面那条定时日志。
不要把它挂到公网前缀下。

已知的观测缺口：`primary_requests` 计数只统计「可退回」的那条路径，
`background=True` 的批量请求不计入，所以这个数远小于实际出网量。
要看批量量级得看落盘的格点文件数或 Buffalo 侧的 net IO。

### 3. 代码是在 main 之上重做的

第一版兜底写在落后 main 8 个提交的分支上，而 main 恰好在同一块做了大改
（预算收口到 `weather_get`、429 按分钟/小时/日分档、代理池按实测出口记账、
「直连也是一个出口、额度照记」的兜底）。直接派生镜像会回退这批工作，
所以先 rebase 到 main 再重做整合。集成后 560 个测试全绿、ruff 通过、OpenAPI 未变。

## 十一、测试环境压测（2026-09-16 夜 → 09-17 凌晨）

测试环境已切到自建实例并**关掉代理池**，部署细节与回滚步骤见
[docs/10 §测试环境自建气象试验](./10-deployment.md#测试环境自建气象试验2026-09-16)。

切换后立刻可用：`/ready` 200、公网首页 200（首次 10.2 秒冷读、随后 1.2 秒）、
目录接口 200；`scheduler started: ['map_prepare', 'weather_upstream']`，
探测任务在 `enable_scheduler=false` 的测试环境里也注册上了，日志报 `healthy: True`。

### 1. 第一轮作废：跨日保护把它拦了

北京 23:41 触发全冷轮次，19 分钟落了 900 个格点文件（约 44 个/分钟），
过零点后每个格点都抛 `ValueError: 统计日期已变化`，轮次以 `partial` 收场，
消息是「新一轮预报准备中，当前使用上一批对应日期的预测」。

这是按设计的跨日保护，**失败模式是优雅的**：没有污染数据、没有报错给用户，
自动退回上一批对应日期的预测。但一轮全冷要一小时左右，
**贴着当地午夜启动就等于白跑**。

### 2. 顺带的发现：`fleet_refresh_hour` 对自建没有意义了

`fleet_refresh_hour=8`（北京）的理由是「Open-Meteo 日额度在 UTC 零点重置，
此前沿用上一日快照，不开当天这一轮整目录拉取」。自建之后全目录根本不碰官方额度
（`background=True` 不退回官方），这个门槛只剩下白等 8 小时的副作用。

但**跨日保护仍然要留**：它防的是「一轮算到一半日期变了」，和额度无关。

建议：切生产前把 `fleet_refresh_hour` 改成随「是否在用官方额度」决定，
而不是写死 8。本次压测在测试环境临时设了 `ENERSIGHT_FLEET_REFRESH_HOUR=0`，
试验结束要删掉。

### 3. 第二轮：完整跑通

北京 00:05 重新触发全冷轮次（距下一个当地午夜有 8 小时余量），
临时设 `ENERSIGHT_FLEET_REFRESH_HOUR=0` 才能立刻开轮，试验后已删除。

| 项 | 结果 |
| --- | --- |
| 耗时 | **40 分钟**（16:05:45Z → 16:45:51Z） |
| 结束状态 | **`ready`**，`failed_count: 0` |
| 覆盖 | **`covered_count: 8218 / eligible_count: 8218`**，100% |
| 落盘 | 2,882 个格点文件；容量 836 GW，日发电 4.85 TWh（光伏 1.60 + 风电 3.25） |
| 预测天数 | `days: 7` |
| 起报 | `basis.resolved_model=ecmwf_ifs`、`issued_at=2026-09-16T14:00+08:00`、`batch_stamp=2026-09-16T06:00Z` —— 正是桶里那一批，说明元数据指数据桶是对的 |
| 429 / 主源失败 / 兜底 | **全部 0**：`fallback_requests: 0`、`breaker_trips: 0`、`consecutive_failures: 0`、`healthy: True` |
| 测试容器内存 | 峰值 514 MB / 1 GiB |
| Buffalo 内存 | 峰值 **1.045 GiB / 2 GiB** —— 限额不要低于 2 GB |
| Buffalo 累计收流量 | 458 MB（含此前所有实验），整轮约 240 MB |

兜底一次都没被用到，这正是期望：全目录走 `background=True`，主源可用时根本不该碰官方。

轮次结束后重启容器，快照没丢（仍 `ready`、8218/8218、7 天）。
公网 `/ready` 0.16 s、首页 2.0 s、`/v1/predictions/fleet` 0.11 s、目录 0.12 s，全部 200。

一条运营观察：压测期间公网首页从 1.2 秒涨到 6.2 秒。测试机只有 0.75 核，
全目录轮次会和页面抢 CPU；生产是 1.5 核，但切之前值得确认这个挤占幅度。

## 十二、正式发布（2026-09-17 08:15–08:45，北京时间）

PR [#1](https://github.com/xiaoguo123456/EnerSight/pull/1) rebase 合入 main（`295725a`）。
CI 与「测试部署」随 main 推送自动跑完，均 success；「生产部署」手动触发
（run `35166320060`，填完整 40 位 SHA），发布前检查与构建发布均 success。

### 1. 生产分两步切，把镜像问题和气象切换分开

先只发镜像：`295725a15700@sha256:88e9f1b1…`，上一版 `17290a7aa432` 写入
`.previous-release.env` 可回滚。此时 `.env` 里还没有 `ENERSIGHT_OPEN_METEO_*`，
所以行为与以前完全一致 —— 日志里 `scheduler started` **没有** `weather_upstream`，
正好印证「没配兜底就不注册探测」这条设计。公网 `/ready`、首页、全目录全部 200。

再切气象源：`.env` 备份为 `.env.before-selfhost-20260917T083056`（权限 600），
追加四行后重建 api 容器。重建后 `weather_upstream` 出现在任务列表里并报 healthy。

### 2. 生产实际效果

| 项 | 结果 |
| --- | --- |
| 公网接口 | `/ready` 0.06 s、首页 0.27 s、全目录 0.12 s、预警 1.79 s、地图 0.21 s、场站详情 0.11 s、趋势 7d 0.19 s、单站预测 0.19 s —— **全部 200** |
| 场站详情 | 天气完整（12.4℃、湿度 91%、云量 6%、辐射 321 W/m²、「晴」，带环比），`index.score=60.9 / fair / 今日发电条件一般` |
| 起报 | `basis.resolved_model=ecmwf_ifs`、`issued_at=2026-09-17T02:00+08:00`（18z 批次），元数据指数据桶取到的是最新一批 |
| 全目录 | 00:15 UTC 定时轮次跨过切换点后继续走自建，00:39 收尾：**`ready`、`covered 8218/8218`、`failed 0`、`days 7`**，4.883 TWh / 836 GW，`batch_stamp 2026-09-16T12:00Z` |
| 上游计数 | **`primary_requests: 59`、`fallback_requests: 0`、`breaker_trips: 0`、`healthy: True`** |
| 429 / 主源失败 / 熔断 | 半小时内**全部 0** |
| 资源 | 容器 328→402 MB / 1.5 GiB，CPU 峰值 87%；主机可用内存 4.5 GB、磁盘 55 GB |

**测试机上担心的 CPU 挤占在生产上不成立**：全目录轮次跑着的时候
生产首页稳定在 **0.22 秒**（测试机 0.75 核是 6.2 秒）。1.5 核够用。

### 3. Buffalo 内存限额从 2g 提到 3g

跑完测试与生产两轮后 `docker stats` 显示 **1.773 GiB / 2 GiB**（89%），贴得太近。
机器总内存 3.9 GB，`.env` 的 `OPEN_METEO_MEM_LIMIT` 已改成 `3g` 并重建，
重建后 17 MB 起步（缓存文件在命名卷里，会自己回填）。
注意这个数含 cgroup 页缓存，未必都是常驻内存，但留余量更便宜。

### 4. 兜底真的触发过一次 —— 在测试环境，原因是冷读超时

生产侧 `fallback_requests` 全程 0。但**测试环境计数是 `primary=1 fallback=1 trips=0`**：
2026-09-17T00:22:27Z，测试容器随发布重建后的第一个气象请求在主源上
`ReadTimeout`，于是转到官方、页面正常返回，随后恢复，没有累计到熔断阈值。

这是兜底路径的真实端到端实证（单元测试之外），但它暴露的问题比验证更重要：

**共用 HTTP 客户端的超时是硬编码的 10 秒**（`app/main.py:53`），
而自建实例的冷读实测：单坐标 15 字段 9 天 **5.58 秒**，容器重启后首页 **12.3 秒**。
也就是说冷读会超时，然后**静默地转去花官方额度** —— 正是上自建要解决的那件事。

风险集中在三处：从未查过的新坐标、容器重启后的首批请求，
以及**每 6 小时新批次落地后各区域重新变冷**。生产 60 次主源请求 0 次兜底，
说明缓存热了之后不漏；但批次切换窗口仍会零星漏过去。

建议的修法（还没做）：给主源那一跳单独一个更长的超时
（例如新增 `weather_primary_timeout`，默认 30 秒），而不是抬高共用客户端的 10 秒 ——
那个超时还管着腾讯位置服务、JMA 瓦片等其他上游。

## 十三、冷读到底慢在哪，以及为什么「打开新电站」变慢了

### 1. 冷读不是「HTTP 干等 10 秒」，但确实是一次 HTTP 在等

我们这边看到的就是**一次**打给自建实例的 HTTP 请求。慢是慢在实例内部：
它要按坐标去 `s3://openmeteo` 读几十次字节区间，Buffalo→us-west-2 的 RTT 是 113 ms，
几十次往返叠起来就是几秒。共用客户端的超时是 10 秒（`app/main.py`），
超了就是 `ReadTimeout`，然后按兜底规则转去打官方 —— **静默花官方额度**。

### 2. 实测：贵在「每个新坐标」，不是「每个区域」

从生产机按生产完全一致的参数（`best_match`、15 字段 `minutely_15`、8+1 天）打 Buffalo：

| 场景 | 耗时 |
| --- | --- |
| 某坐标第一次 | **5.1 / 5.4 / 6.0 / 6.0 秒**（四个坐标） |
| 同一坐标第二次 | 0.9–1.1 秒（这就是北京↔Buffalo 的往返与 76 KB 传输地板） |
| 只要 1 个字段 | 0.68 秒（21 KB） |
| 官方接口同参数 | **0.73 秒** |

关键对照 —— 邻近坐标**沾不到热**：

| 距基准点 | 耗时 |
| --- | --- |
| 基准 24.10,99.30 | 5.83 s |
| +0.1° | 4.61 s |
| +0.2° | 4.24 s |
| +0.5° | 5.08 s |
| +1.0° | 4.37 s |
| +2.0° | 1.74 s |
| 基准重打 | 1.38 s |

而在 Buffalo 本机打一个全目录几乎不覆盖的点（西藏西部）全 15 字段只要 0.79 秒，
说明**区域的块早就热了**，慢的是「这个格点的那些字节区间还没读过」。
所以「预热一张粗网格」救不了：0.25° 的中国区有 36,105 个格点，
按全目录实测 0.87 秒/坐标算要 8.7 小时，而且每批起报都要重来。

### 3. 于是「打开新电站」从约 0.7 秒变成 5–6 秒

单点预报缓存是 `AsyncTTLCache(maxsize=512)`、**进程内**、按站点精确经纬度做键，
而公开目录有 17,836 座。所以：

- **自己的电站不受影响**：`scan_alerts` 每 15 分钟遍历全部 `Station`
  并调 `weather.station_forecast`，一直是热的。
- **浏览公开电站就是冷的**：512 个槽位装不下，打开一座没在缓存里的就要 5–6 秒。
  实测 8 座分散电站，5 座命中（0.09–0.21 秒）、3 座冷读（**4.43 / 5.33 / 10.22 秒**）。

**这是自建带来的真实退化**（官方 0.73 秒 → 自建 5–6 秒），不是小程序端的问题，
也不是「本来应该预加载却没加载」—— 单点预报从来都是请求时拉取 + 缓存，
全目录预热的是网格坐标，和站点坐标不是同一批。

### 4. 三条可选路线（未实施，需要定）

| | 做法 | 代价 |
| --- | --- | --- |
| A 落盘缓存单点预报 | 照全目录 `fleet-weather-cells` 那套按指纹落盘，每座电站每批只慢一次 | 中等改动，要自己管淘汰与磁盘；第一次打开仍 5–6 秒 |
| B 页面走官方、后台走自建 | 把 `background` 的路由反过来：额度大头（一轮 4,120 单位）留给自建，页面要延迟就用官方 | 一行路由改动；但页面量一大又会吃官方额度，要先估量 |
| C 就接受 5–6 秒 | 只把主源超时提到 30 秒，别再静默漏去官方 | 已实施（下节），但页面依旧慢 |

## 十四、本轮修掉的四件事

1. **主源单独的超时** `weather_primary_timeout`（默认 30 秒）。共用客户端 10 秒会在
   冷读上超时并静默转去花官方额度。调用方显式传了 `timeout` 的（全目录 40 秒、
   地图 25 秒）以调用方为准。
2. **自建请求绝不进代理池。** 原先 `background=True` 时 `target` 为 None，
   请求会掉进官方通道 `_official`，若代理池开着就会把**自建**请求塞进公网代理。
   现在按「URL 是否属于自建主源」决定走哪条，与 `allow_fallback` 无关。
3. **`primary_requests` 覆盖后台批量。** 之前只统计可退回那一条，
   整轮全目录跑完计数还是个位数，看不出实际出网量。
4. **新批次沉降窗口** `weather_batch_settle_seconds`（默认 1800 秒）。
   数据桶按变量分别重写，`available_at` 刚过去就换批次会取到混着两批的数据，
   `basis.issued_at` 还会报得比数据新。窗口内继续用手上那批。**只对自建生效**。
5. **`fleet_refresh_hour` 只在用官方额度时才等。** 它的理由是等 UTC 零点额度重置；
   自建不占官方额度就没有干等 8 小时的道理。跨日保护另按 `day_key` 判定，不受影响。

`REMOTE_DATA_DIRECTORY_MINIMUM_AGE` **没有采用**：上游源码里它只有一处用途
（≥24 小时时关掉 forecast 的远程归档访问），不是解决批次错位的工具，语义也不清晰。
批次问题改在我们自己这边收口（第 4 条）。

## 十五、预热：为什么必须放在实例本机（2026-09-17）

第十三节第 4 小节的三条路线里选了 A 的轻量版 —— **只预热实例的块缓存**，
不动应用的缓存设计。定这个方案前补了两组测量，第二组把架构决定改了。

### 1. 批量把每坐标成本摊薄 2–6 倍

在实例本机量冷批次（用 `icon_global` / `gfs_global`，这两个模型的块没被读过，是干净样本）：

| 模型 | 10 坐标一批 | 100 坐标一批 | 同批重打（热） |
| --- | --- | --- | --- |
| `icon_global` | 5.09 s/坐标 | **3.11 s/坐标** | 0.01 s/坐标 |
| `gfs_global` | 1.19 s/坐标 | **0.91 s/坐标** | 0.01 s/坐标 |

对比单点请求 5–6 s/坐标。我们用的 `ecmwf_ifs` 按全目录那轮实测是 0.87 s/坐标，接近 gfs。

**热了之后实例侧基本归零**（0.01 s/坐标），剩下的全是北京↔实例的传输。

### 2. 唯一坐标只有 11,211 个

18,764 座目录电站里多期同址重合掉 40%。所以全量预热按 0.9 s/坐标算约
**2.8 小时**（单线程）、3 并发约 **1 小时**；最悲观按 icon 那个速率 3 并发也是 3.2 小时。
6 小时的批次周期够用。

### 3. 但从北京驱动不行 —— 大响应只有 16–18 KB/s

从生产机打**已经热了**的 100 坐标批次（纯传输，实例侧 0.8 秒就能答完）：

| 次数 | 耗时 | 大小 | 吞吐 |
| --- | --- | --- | --- |
| 第 1 次 | 421.1 s | 7.57 MB | **18 KB/s** |
| 第 2 次 | 450.8 s | 7.57 MB | **16 KB/s** |

而 76 KB 的单站响应约 76 KB/s（同坐标重打 0.9–1.1 秒）。**吞吐随传输量非线性劣化** ——
跨太平洋丢包把拥塞窗口打下去了。11,211 坐标从北京驱动要
11,211 ÷ 100 × 430 s ≈ **13.4 小时**，超预算。

所以：**预热驱动放在实例本机，走 localhost**。北京侧只负责导出坐标清单（280 KB，
传输不是问题）。

### 4. 落地

- 北京侧 `app/jobs/warm_coords.py`：每日导出公开目录的唯一坐标到 `/tiles` 静态挂载下的
  `warm-coords.json`，不新增接口与鉴权面。**只导公开目录** —— 自建站点是私有数据，
  不能落到公开静态目录；它们本来也被 `scan_alerts` 每 15 分钟拉热。
  省级占位坐标跳过（本来就不参与预测，判定与 `catalog_basis` 同源）。
- 实例侧 `deploy/open-meteo/warmup.py`：cron 每 6 小时一次（比 `available_at` 晚半小时，
  避开批次沉降窗口），100 个一批、3 并发打 localhost，**响应体直接丢掉**。
  自带 `flock` 防叠跑；清单取不到就用上一次缓存的那份。
- 两边都**只在自建时生效**：`warm_coords` 只在配了 `open_meteo_fallback_base` 时注册。

预期效果：打开新电站从 5–6 秒降到约 **1 秒**（剩下的是跨太平洋那 76 KB）。
这也让第十三节的方案 B（页面走官方）不再必要 —— 1 s 对 0.73 s 差距可接受，额度全省下来。

**要盯缓存命中，不是「脚本跑完了」**：预热的块在实例的 24 GB LRU 里，
一万多个坐标约占 1 GB，远在上限内；但被挤掉预热就白做。抽查办法见运行手册。

## 十六、仍然没验证的

- **流量还没切。** 暴露已按 IP 白名单做好并验证，但第九节第 8 小节那四件事没做完。
- **`REMOTE_DATA_DIRECTORY_MINIMUM_AGE` 的确切语义没验证。** 这是处理批次错位的
  首选手段，但设错会一直读旧数据，必须先在 Buffalo 那台上实测再用。
- **批次错位的影响面只量了一次。** 测到同 chunk 各变量 `Last-Modified` 跨度约 30 分钟，
  没有按小时采样统计它一天出现多久、命中哪些变量。
- **并发与多模型没压过。** 只测了 `ecmwf_ifs` 单模型、单请求串行。`best_match`
  会触及更多域；全目录 2,747 坐标的真实并发、以及连续几批起报后的缓存稳态都没跑过。
- **2.5 小时的全目录推算是外推。** 由 100 坐标 334 秒线性放大得来，没有真跑过整轮。

## 来源

- [自建入门（硬件要求、Docker、远程数据与缓存、许可）](https://github.com/open-meteo/open-meteo/blob/main/docs/getting-started.md)
- [多节点与 sync 命令](https://github.com/open-meteo/open-meteo/blob/main/docs/sync-command.md)
- [AWS 开放数据分发（桶、目录结构、模型清单）](https://github.com/open-meteo/open-data)
- [官方定价与限流](https://open-meteo.com/en/pricing)
- 上游源码：`Sources/App/EcmwfEcpds/EcmwfEcpdsReader.swift`、
  `Sources/App/EcmwfEcpds/EcmwfEcpdsDomain.swift`、
  `Sources/App/Controllers/ForecastapiController.swift`、
  `Sources/App/Helper/Meteorology.swift`、`Sources/App/Commands/SyncCommand.swift`
- 数据桶直查：`data/{ecmwf_ifs,ecmwf_ifs025,ncep_gfs013,dwd_icon}/` 变量清单与文件大小、
  `static/meta.json` 内容
