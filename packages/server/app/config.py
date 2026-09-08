"""服务配置。密钥一律走环境变量，不进代码。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ENERSIGHT_")

    debug: bool = False
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

    # Himawari（NICT 实时真彩图）。商用授权待法务核实，见 docs/09
    himawari_base: str = "https://himawari8.nict.go.jp/img/D531106"
    himawari_latest: str = "https://himawari8-dl.nict.go.jp/himawari8/img/D531106/latest.json"
    himawari_level: int = 4  # 4d = 2200px 全圆盘，约 5km/px

    # 上游数据源
    open_meteo_base: str = "https://api.open-meteo.com/v1"
    open_meteo_archive_base: str = "https://archive-api.open-meteo.com/v1"

    # 缓存 TTL（秒），见 docs/05 §五
    ttl_current_weather: int = 600
    ttl_hourly_forecast: int = 3600
    ttl_energy_index: int = 3600

    # 指标模型参数，见 docs/07 §七。可配置，不硬编码
    pv_gamma_pdc: float = -0.004
    pv_losses: float = 0.14
    pv_temperature_model: str = "open_rack_glass_glass"
    wind_v_in: float = 3.0
    wind_v_rated: float = 12.0
    wind_v_out: float = 25.0
    wind_shear_alpha: float = 0.14

    # 指数分档阈值，上线前需按 docs/07 §八 校准
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
