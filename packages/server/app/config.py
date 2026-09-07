"""服务配置。密钥一律走环境变量，不进代码。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ENERSIGHT_")

    debug: bool = False

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

    # 衍生指标
    co2_factor_kg_per_kwh: float = 0.8
    tariff_yuan_per_kwh: float = 0.4


settings = Settings()
