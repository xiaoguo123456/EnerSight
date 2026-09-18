"""服务配置。密钥一律走环境变量，不进代码。"""

from datetime import date

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ENERSIGHT_")

    debug: bool = False
    # 共用域名时由网关剥离此前缀；图片 URL 和 OpenAPI 保留公网前缀。
    root_path: str = ""
    # 共用 RDS 的连接预算：单实例最多 3 个业务连接。
    db_pool_size: int = 2
    db_max_overflow: int = 1
    # 定时任务开关。多实例部署时只在一个实例上开
    enable_scheduler: bool = True
    # 地图预处理可独立开启；未设置时沿用总开关，兼容既有部署。
    enable_map_scheduler: bool | None = None

    @property
    def map_scheduler_enabled(self) -> bool:
        return (
            self.enable_scheduler
            if self.enable_map_scheduler is None
            else self.enable_map_scheduler
        )

    # 数据库。本地 SQLite 零依赖起步，线上换 PostgreSQL：
    #   postgresql+asyncpg://user:pass@host/enersight
    # 时间一律存 UTC，两端方言对时区的处理不同，不要依赖数据库时区
    database_url: str = "sqlite+aiosqlite:///./enersight.db"

    # JWT。生产必须通过环境变量覆盖，启动时会校验（见 main.py）
    jwt_secret: str = "dev-only-secret-do-not-use-in-production-0000"
    jwt_expires_seconds: int = 7 * 24 * 3600

    # 微信登录。未配置时仅 debug 模式可用开发态登录
    wx_appid: str = ""
    wx_secret: str = ""

    # 腾讯位置服务。未配置时逆地理编码返回 None、搜索只匹配站点与坐标
    tencent_lbs_key: str = ""
    tencent_lbs_base: str = "https://apis.map.qq.com/ws"

    # Himawari-9 瓦片：日本气象厅官网的 Web Mercator 瓦片，红外/可见光/真彩都有。
    # 注明出处可商用（公共データ利用規約），但是网页自用接口无 SLA。见 docs/04、docs/09
    himawari_base: str = "https://www.jma.go.jp/bosai/himawari/data/satimg"
    himawari_zoom: int = 5  # 最高级别，256px/瓦片，约 4–5 km/px
    # 云图帧归档：JMA 只留 35 小时，光流精度校准要自己攒历史。每站每帧几十 KB
    enable_archive: bool = True
    # 每轮回补最近几帧：最新帧瓦片常未就绪，只归档 latest 会永久漏帧（JMA 只留 35 小时）
    archive_backfill_frames: int = 6
    archive_dir: str = "data/archive/himawari"
    archive_retention_days: int = 60

    # 上游数据源
    open_meteo_base: str = "https://api.open-meteo.com/v1"
    open_meteo_archive_base: str = "https://archive-api.open-meteo.com/v1"
    # 各模型元数据（起报时刻、可用时刻）：{meta_base}/{slug}/static/meta.json。docs/17 §二
    open_meteo_meta_base: str = "https://api.open-meteo.com/data"
    # 默认关闭：开启后统一出网入口经代理池请求，按实测出口分账。docs/10「气象代理池」
    weather_proxy_pool_enabled: bool = False
    # 代理池全部参数可调，默认值取自 docs/2026-09-15-weather-proxy-pool-strategy.md §三、§四。
    # 免费列表与出口探测地址；留空用代码里的默认源。
    weather_proxy_source: str = ""
    weather_proxy_exit_probe: str = ""
    # 候选节点上限。真正决定额度宽度的是**实测独立出口数**，不是候选数 ——
    # 免费列表里能连通并测出独立出口的通常只有一到三成，所以候选要远多于目标出口。
    # 实测通过率约 5%（100 个候选实测出 5 个独立出口），要 20 个出口就得按这个比例备候选。
    weather_proxy_candidates: int = 500
    weather_proxy_target_exits: int = 20
    weather_proxy_min_exits: int = 5
    # 同一出口最多留几个节点作连接备用；多出来的不再算独立额度，见代理池 _observe_exit
    weather_proxy_nodes_per_exit: int = 2
    # 后台检测：每轮候选数、并发、列表刷新间隔、低水位补充的最小间隔。
    # 候选数决定这两个值：500 个候选按每轮 20 个、并发 2 探，探完一遍要两个多小时，
    # 掉线的出口补不回来。死候选的代价只是两次 4 秒超时，不花气象额度（拿不到出口就
    # 不会去打 meta.json），所以这里可以放开。
    weather_proxy_probe_per_round: int = 100
    weather_proxy_probe_concurrency: int = 8
    weather_proxy_refresh_seconds: int = 900
    weather_proxy_topup_seconds: int = 120
    weather_proxy_drop_streak: int = 5
    # 种子文件多久算过期。实测 31 小时前的种子仍有四成能连上游，候选反正都要实测，
    # 24 小时一刀切只会让升级后无从引导。
    weather_proxy_seed_max_age: int = 7 * 86400
    # 出口观测有效期与气象健康有效期：过期的节点先复核再承接业务
    weather_proxy_exit_ttl: int = 900
    weather_proxy_health_ttl: int = 1800
    # 单次传输超时与整个用户请求的截止时间（含排队与换出口），以及最多几次实际访问
    weather_proxy_exit_timeout: float = 4
    weather_proxy_probe_timeout: float = 4
    weather_proxy_request_timeout: float = 6
    weather_proxy_deadline: float = 14
    weather_proxy_attempts: int = 2
    # 并发：全局名额与后台任务名额，后台不能把出口占满让页面排不上
    weather_proxy_slots: int = 4
    weather_proxy_background_slots: int = 2
    # 每出口的自设保守阈值，单位与 upstream_units_per_minute 一致（按坐标数计）。
    # 这不是上游承诺的额度，免费出口还可能被其他人占用，所以要留足余量。
    weather_proxy_exit_units_per_minute: float = 300
    weather_proxy_exit_units_per_hour: float = 2000
    weather_proxy_exit_units_per_day: float = 5000
    # 出口账本读不回来时的暂停时长：不能假定小时与日额度为零使用
    weather_proxy_ledger_recover_seconds: int = 3600
    # 代理池给不出出口时退回直连。直连在池的账本里也是一个出口，额度与 429 归属照记；
    # 关掉则池子一空整个气象层就不可用。docs/10「气象代理池」
    weather_proxy_direct_fallback: bool = True
    # 直连出口的额度。服务器 IP 走免费层，上限 600/分钟、5,000/小时、10,000/天，
    # 这里各留两成余量 —— 兜底把日额度打满，就回到了当初上代理池要解决的问题。
    weather_proxy_direct_units_per_minute: float = 480
    weather_proxy_direct_units_per_hour: float = 4000
    weather_proxy_direct_units_per_day: float = 8000

    # 自建实例故障时退回的官方地址。**留空表示不退回**（直连官方时本就没有意义）。
    # 只在 open_meteo_base 指向自建实例时配。兜底要花官方额度，因此默认只用于页面请求：
    # background=True 的后台批量路径不退回。兜底也不经代理池，两者是互替策略。
    # 见 providers/weather_transport
    open_meteo_fallback_base: str = ""
    # 归档兜底是**另一个域名**：自建把 ERA5 挂在同一个 /v1 下，官方是独立的 archive-api
    open_meteo_archive_fallback_base: str = "https://archive-api.open-meteo.com/v1"
    # 主源连续失败几次后熔断。熔断期间直接走兜底，不让每个请求先白等一次超时
    weather_primary_trip_after: int = 3
    weather_primary_probe_seconds: float = 120.0
    # 自建主源的冷读远慢于官方：实测单坐标 15 字段 9 天首次 5–6 秒（每个新坐标的固定开销，
    # 是 Buffalo→S3 的往返 × 几十次区间读），同坐标重复 0.9–1.1 秒。共用客户端只有 10 秒，
    # 冷读一超时就**静默转去花官方额度** —— 正是自建要解决的事。所以主源单独一个超时。
    # 调用方显式传了 timeout 的（全目录 40 秒、地图 25 秒）以调用方为准。
    weather_primary_timeout: float = 30.0
    # 新起报批次的沉降窗口。数据桶按变量分别重写，实测同 chunk 各变量 Last-Modified
    # 跨度约 30 分钟；`available_at` 刚过去不久就换批次，会取到混着两批的数据，
    # 且 basis.issued_at 报得比实际数据新。窗口内继续用手上那批，整批换过去再走。
    weather_batch_settle_seconds: float = 1800.0

    @property
    def weather_self_hosted(self) -> bool:
        """主源是自建实例。配了官方兜底就说明主源不是官方。"""
        return bool(self.open_meteo_fallback_base)

    ttl_model_meta: int = 300

    # 缓存 TTL（秒），见 docs/05 §五
    # 元数据拿不到、无法确认起报时的批次标识时间片，见 services/weather.batch_stamp
    ttl_current_weather: int = 600
    # 单点预报每个坐标每天最多回源几次，按当地时段均分；时段内不追新批次。docs/04 §二
    forecast_refreshes_per_day: int = 4

    # 上游拉取：一律带退避重试、限并发。CLAUDE.md「已知的环境坑」
    # 只重试传输错误与 5xx；429（配额）与 400（坐标越界）重试无用，只会烧得更快
    upstream_retries: int = 3
    upstream_backoff_seconds: float = 0.5
    upstream_max_connections: int = 32
    upstream_units_per_minute: float = 480

    # 逐日累积：算并发、写串行，分批提交。见 services/accumulate
    accumulate_concurrency: int = 8
    accumulate_batch_size: int = 200
    # 预警扫描：按站点串行（共用一个会话），只分批取站点
    scan_batch_size: int = 200
    # 报告预生成：一任务一会话，并发上限还会被 db_pool_size 夹一次
    reports_batch_size: int = 100
    reports_concurrency: int = 2
    # 全目录汇总：一次请求带几个坐标（Open-Meteo 支持逗号分隔的多坐标）。
    # 只省 HTTP 往返，不省额度 —— 实测限流按坐标数计，429 精确落在第 600 个坐标
    fleet_coords_per_request: int = 100
    # 因此要按坐标限速。上限 600/分钟，留两成余量给站点预报与元数据（同一个 IP 共享）
    fleet_coords_per_minute: int = 480
    # 全目录每个模型每天只整轮拉一次气象。Open-Meteo 日额度按 UTC 零点重置，北京时间此整点前
    # 沿用上一日快照；同日缺失坐标续拉的轮数有上限。docs/04 §二
    fleet_refresh_hour: int = 8
    fleet_fetch_rounds_per_day: int = 4
    # 汇总取气象的网格步长，按能源类型分开。辐射场在百公里尺度上平滑，风速不是 ——
    # 实测 1° 下光伏容量加权汇总偏差 +0.75%、风电 -8.46%，0.25° 分别为 +0.15% / +0.09%。
    # 见 docs/04 §七。改小要同步看坐标预算：风电占可算场站的六成
    fleet_grid_step_solar: float = 1.0
    fleet_grid_step_wind: float = 0.25
    # 自建站点每用户上限。docs/17 §一
    max_stations_per_user: int = 10
    # 单站与全目录预测天数：四个模型的公共上限是 7 天（ICON 全球 7.5 天）。docs/17 §二
    forecast_outlook_days: int = 7
    ttl_hourly_forecast: int = 3600  # 图层网格块

    # 三模式区间。只作用于 /v1/predictions/station，其余链路仍是 best_match。docs/19 §一
    ensemble_members: str = "ecmwf_ifs,icon_global,gfs_global"
    # 日电量分歧度 (max−min)/median 的分档，百分比
    ensemble_spread_moderate: float = 15.0
    ensemble_spread_high: float = 40.0
    # 预报演变：固定单一模型串时间序列，混模型看到的是模型间差异而不是演变。docs/19 §二
    evolution_model: str = "ecmwf_ifs"
    evolution_issuances: int = 3  # 收敛度看最近几份不同起报
    evolution_stable_pct: float = 10.0
    evolution_swing_pct: float = 25.0
    evolution_lead_days: int = 7  # 时效窗口：目标日往前几天的留档参与演变
    # 预测留档保留天数；此前只写不清理
    prediction_archive_retention_days: int = 45
    # 我的电站每日签发时刻（UTC+8）：全目录轮次之后、单点缓存新时段内
    issue_outlooks_hour: int = 8
    issue_outlooks_concurrency: int = 4

    # 指标模型参数，见 docs/07 §七。可配置，不硬编码
    model_v4_start_date: date = date(2026, 9, 10)

    pv_gamma_pdc: float = -0.004
    pv_losses: float = 0.14  # 含逆变器损耗，与 PVGIS 口径一致
    # 站点容量是交流侧（docs/04 §七），直流侧 pdc0 = 容量 × 容配比；交流出力按容量限幅
    pv_dc_ac_ratio: float = 1.2
    pv_temperature_model: str = "open_rack_glass_glass"
    pv_sky_diffuse_model: str = "perez"  # 校准结论，见 docs/07 §八
    wind_v_in: float = 3.0
    wind_v_rated: float = 12.0
    wind_v_out: float = 25.0
    wind_losses: float = 0.10  # 尾流 + 可利用率 + 电气损耗，场站级
    wind_hub_height_default: float = 100.0  # 未填轮毂高度时的默认值
    # 只有 10 m 风速时的幂律外推指数（降级路径）；正常用 80/100/120 m 各层按对数廓线插值
    wind_shear_alpha: float = 0.18
    wind_air_density_ref: float = 1.225  # 功率曲线标称空气密度，IEC 61400-12-1 密度修正的基准
    # 目录风电没有机型字段：陆上此年份起投运（或年份未知）按该机型档，更早的用通用曲线；
    # 海上一律通用曲线。依据 REIT 场站电量对账，docs/07 §2.2、§8.1
    wind_catalog_modern_from_year: int = 2015
    wind_catalog_modern_class: str = "low_wind"
    # 光伏跟踪与双面：pvlib singleaxis / infinite_sheds 的默认几何。docs/07 §2.1
    pv_tracking_gcr: float = 0.35
    pv_tracking_max_angle: float = 60.0
    pv_bifaciality: float = 0.7
    pv_albedo: float = 0.2

    # 指数分档阈值，上线前需按 docs/07 §八 校准
    # 公开电站目录同步：GEM 下载要提交联系人信息（表单），按月一次。docs/04 §七
    catalog_sync_days: int = 30
    catalog_dir: str = "data/catalog"
    gem_contact_name: str = ""
    gem_contact_email: str = ""  # 空则不同步
    gem_contact_org: str = "EnerSight"
    gem_use_case: str = (
        "Seeding the public plant catalog of EnerSight, a WeChat mini program for solar and wind "
        "plant operators in China with weather-based generation forecasts and satellite cloud "
        "alerts. Users pick their plant from the tracker instead of entering coordinates. "
        "Attribution to Global Energy Monitor is shown in the app."
    )
    gem_supabase_key: str = "sb_publishable_8mQAV8B2HhveNc5T8VGqPQ_1lgsFAvz"  # 官网表单里的公开 key
    gem_mint_url: str = "https://auxunjnrktkmeqyoyngm.supabase.co/rest/v1/rpc/mint_submission"
    gem_presign_url: str = "https://auxunjnrktkmeqyoyngm.supabase.co/functions/v1/presign"

    # 限流：每个 token / IP 每分钟请求数，0 关闭。小程序一页最多十几个请求
    rate_limit_per_minute: int = 120
    trust_forwarded_for: bool = False  # 仅在 ALB/反代前置且直连已被安全组挡住时开启
    index_excellent: float = 85.0
    index_good: float = 70.0
    index_fair: float = 55.0

    # AI。docs/08 §二：默认规则模板；claude 需 ANTHROPIC_API_KEY；
    # 境内公开发布应换成已备案模型的 provider（同一接口，另写实现）
    ai_provider: str = "rule"  # rule | claude
    ai_model: str = "claude-opus-5"
    ai_timeout_seconds: float = 20.0
    report_generate_hour: int = 8  # 每日预生成时刻（UTC+8）

    # 预警阈值，docs/07 §五
    alert_drop_moderate: float = 20.0
    alert_drop_severe: float = 40.0

    # 衍生指标
    co2_factor_kg_per_kwh: float = 0.8
    tariff_yuan_per_kwh: float = 0.4


settings = Settings()
