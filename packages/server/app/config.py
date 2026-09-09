"""服务配置。密钥一律走环境变量，不进代码。"""

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
    archive_dir: str = "data/archive/himawari"
    archive_retention_days: int = 60

    # 上游数据源
    open_meteo_base: str = "https://api.open-meteo.com/v1"
    open_meteo_archive_base: str = "https://archive-api.open-meteo.com/v1"

    # 缓存 TTL（秒），见 docs/05 §五
    ttl_current_weather: int = 600  # 站点整份 Forecast（实时天气、指数、趋势共用）
    ttl_hourly_forecast: int = 3600  # 图层网格块

    # 指标模型参数，见 docs/07 §七。可配置，不硬编码
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
    trend_7d_step_hours: int = 3  # 7 天趋势采样粒度，逐日还是逐 3 小时待产品确认
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
