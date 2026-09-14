"""服务配置。密钥一律走环境变量，不进代码。"""

from datetime import date
from urllib.parse import urlsplit

from pydantic import SecretStr, model_validator
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
    # 空地址走直连；配置后统一经独立 Worker 获取 JSON API 与模型元数据。
    weather_relay_base: str = ""
    weather_relay_token: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def validate_weather_relay(self):
        if self.weather_relay_base:
            parsed = urlsplit(self.weather_relay_base)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or parsed.path not in ("", "/")
            ):
                raise ValueError("气象转发地址必须是无路径、参数和凭据的 HTTPS 域名")
            if len(self.weather_relay_token.get_secret_value()) < 32:
                raise ValueError("启用气象转发时必须配置至少 32 字符的独立密钥")
        return self

    ttl_model_meta: int = 300

    # 缓存 TTL（秒），见 docs/05 §五
    # 元数据拿不到、无法确认起报时的退化时间片；正常路径按批次指纹缓存，见 services/weather
    ttl_current_weather: int = 600
    # 一批模型输出的缓存寿命。上游 6 小时一批，留足延迟余量；同批次内重复拉毫无意义
    ttl_forecast_batch: int = 8 * 3600

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
