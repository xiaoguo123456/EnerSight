"""站点能量指标：环境指数 + 发电估算 + 逐时出力。

把 Forecast 喂给 app/metrics 的物理模型。pvlib 是 CPU 密集同步代码，
调用方必须放进 executor，不要在 async 路由里直接调。docs/05 §6.6

缺测处理（docs/07 §六）在这里统一做，metrics 层只负责算：
- 次要因子（光伏的气温、10 m 风速）：短缺口按前后时刻插值，长缺口用昨日同时刻，标 estimated
- 主要因子（辐射、风电各层风速）：只补 2–3 小时内的短缺口，标 estimated；夜间辐射缺测归 0
- 仍有必要输入缺失 → 不可算：指数 null、日发电 null，不用 0 冒充
"""

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from app.metrics import pv, solar, wind
from app.metrics.index import IndexResult, PvInputs, pv_index, wind_index
from app.models import Station
from app.schemas.common import IndexLevel
from app.services import curtailment
from app.services.prediction_basis import version_for_day
from app.services.weather import Forecast

_SUMMARY = {
    IndexLevel.EXCELLENT: "今日发电条件优秀",
    IndexLevel.GOOD: "今日适宜发电",
    IndexLevel.FAIR: "今日发电条件一般",
    IndexLevel.POOR: "今日发电条件较差，建议关注",
}

RADIATION_COLUMNS = ["shortwave_radiation", "direct_normal_irradiance", "diffuse_radiation"]
# 主要因子只补短缺口：辐射 2 小时、风速 3 小时；再长就是不可算，不用昨日冒充
RADIATION_GAP_HOURS = 2
WIND_GAP_HOURS = 3
# 次要因子（光伏的气温与散热风速，对出力只有几个百分点的影响）允许更长的插值与昨日同时刻回填
SECONDARY_GAP_HOURS = 6


@dataclass(frozen=True)
class Prepared:
    """逐日输入（光伏区间末、风电瞬时整点），缺测已按规则处理。"""

    frame: pd.DataFrame
    complete: bool  # 必要输入齐全，可算
    estimated: bool  # 有输入由插值 / 降级得到
    v_hub: pd.Series | None  # 风电：轮毂高度风速
    # 目录容量口径待核验（prediction_basis）：指数照算（与容量无关），绝对电量不对外给
    blocked: str | None = None


@dataclass(frozen=True)
class EnergySnapshot:
    index: IndexResult | None  # 不可算时 None
    daily_kwh: float | None  # 可发电量（气象潜在值）
    current_kw: float | None
    hourly_kw: pd.Series  # 今日逐时出力，不可算时全 NaN
    estimated: bool
    blocked: str | None = (
        None  # 容量口径待核验：daily / current / hourly 按申报容量算出，仅供报告参考
    )
    # 计入场站出力约束后的上网口径；站点没有规则时全部 None。指数不受影响。docs/17 §四
    grid_hourly_kw: pd.Series | None = None
    grid_kwh: float | None = None
    grid_current_kw: float | None = None
    curtailment_note: str | None = None


def _hours_for(fc: Forecast, day: date, station_type: str, version: str) -> pd.DatetimeIndex:
    """光伏 v4 取自然日的区间末标签；风电保持起点瞬时样本。"""
    start = pd.Timestamp(day, tz=fc.tz)
    if station_type == "solar" and version == "model-v4":
        start += pd.Timedelta(hours=1)
    return pd.date_range(start, periods=24, freq="h")


def current_label(fc: Forecast, station_type: str) -> pd.Timestamp:
    """当前出力对应的逐时标签。

    光伏出力由小时均值辐射算出，取包含当前时刻的区间末标签；
    风电出力由瞬时风速算出，取当前整点。两者口径不同，不能统一。
    """
    return fc.current_interval() if station_type == "solar" else fc.current_hour()


def _at_label(hourly_kw: pd.Series, label: pd.Timestamp) -> float | None:
    # 不以相邻时刻替代目标区间，跨日计算由调用方处理。
    if label not in hourly_kw.index:
        return None
    v = float(hourly_kw.loc[label])
    return None if not np.isfinite(v) else v


def _interp_short_gaps(s: pd.Series, limit_hours: int) -> pd.Series:
    """只对长度 ≤ limit 且两端有值的缺口做时间插值；长缺口整段保留 NaN，不做半截填补。"""
    isna = s.isna()
    if not isna.any():
        return s
    runs = (isna != isna.shift()).cumsum()
    run_len = isna.groupby(runs).transform("size")
    fillable = isna & (run_len <= limit_hours)
    interp = s.interpolate(method="time", limit_area="inside")
    return s.where(~fillable, interp)


def _fill_from_forecast(
    fc: Forecast, frame: pd.DataFrame, col: str, *, limit_hours: int, persistence: bool
) -> tuple[pd.Series, bool]:
    """用整份预报（昨日 + 7 天）填补今日缺口。返回 (列, 是否填补过)。

    persistence=True 时长缺口再用昨日同时刻回填（只给次要因子用）。
    """
    today = frame[col].astype(float) if col in frame else pd.Series(np.nan, index=frame.index)
    missing = today.isna()
    if not missing.any() or col not in fc.hourly:
        return today, False
    full = fc.hourly[col].astype(float)
    filled = today.fillna(_interp_short_gaps(full, limit_hours).reindex(frame.index))
    if persistence:
        filled = filled.fillna(full.shift(24, freq="h").reindex(frame.index))
    return filled, bool(filled.notna().to_numpy()[missing.to_numpy()].any())


def prepare(
    station: Station,
    fc: Forecast,
    *,
    day_offset: int = 0,
    day: date | None = None,
    version: str | None = None,
    hours: pd.DatetimeIndex | None = None,
) -> Prepared:
    """day_offset=1 为明日（留档用）；day 直接指定日期，供历史校准复用同一套缺测处理。"""
    target = day or (fc.current_hour() + pd.Timedelta(days=day_offset)).date()
    version = version or version_for_day(target)
    if hours is None:
        hours = _hours_for(fc, target, station.type, version)
    frame = fc.hourly.loc[hours[0] : hours[-1]].reindex(hours).copy()
    estimated = False

    if station.type == "wind":
        levels: dict[float, pd.Series] = {}
        for height, col in wind.LEVEL_COLUMNS.items():
            if col not in frame and col not in fc.hourly:
                continue
            series, filled = _fill_from_forecast(
                fc, frame, col, limit_hours=WIND_GAP_HOURS, persistence=False
            )
            estimated |= filled
            frame[col] = series
            levels[height] = series
        hub = station.hub_height if station.hub_height is not None else wind.default_hub_height()
        if levels:
            v_hub, fallback = wind.hub_wind_speed(levels, hub)
            estimated |= bool(fallback.any())
        else:
            v_hub = pd.Series(np.nan, index=hours)
        complete = _capacity_ok(station) and bool(np.isfinite(v_hub.to_numpy()).all())
        return Prepared(frame, complete, estimated, v_hub, _blocked(station))

    # 光伏
    for col in ("temperature_2m", "wind_speed_10m"):
        series, filled = _fill_from_forecast(
            fc, frame, col, limit_hours=SECONDARY_GAP_HOURS, persistence=True
        )
        estimated |= filled
        frame[col] = series

    night = (
        solar.clearsky_hourly_mean(station.latitude, station.longitude, fc.tz, hours)["ghi"] < 1.0
    )
    for col in RADIATION_COLUMNS:
        s = frame[col].astype(float) if col in frame else pd.Series(np.nan, index=hours)
        s = s.where(~(s.isna() & night), 0.0)  # 夜间缺测就是 0，不算估计
        gap = s.isna()
        if gap.any():
            s2 = _interp_short_gaps(s, RADIATION_GAP_HOURS)
            estimated |= bool(s2.notna().to_numpy()[gap.to_numpy()].any())
            s = s2
        frame[col] = s

    required = [*RADIATION_COLUMNS, "temperature_2m", "wind_speed_10m"]
    complete = _capacity_ok(station) and all(
        np.isfinite(frame[c].to_numpy(dtype=float)).all() for c in required
    )
    return Prepared(frame, complete, estimated, None, _blocked(station))


def _capacity_ok(station: Station) -> bool:
    return bool(np.isfinite(station.capacity_kw)) and station.capacity_kw > 0


def _blocked(station: Station) -> str | None:
    return getattr(station, "_prediction_blocked", None) or None


def pv_inputs(station: Station, frame: pd.DataFrame, tz: str) -> PvInputs:
    # 目录站点带分期核验过的 (直流, 交流) 容量；否则容量按交流侧、直流按容配比换算
    basis = getattr(station, "_pv_capacity", None)
    dc_kw, ac_kw = basis if basis else (None, station.capacity_kw)
    return PvInputs(
        latitude=station.latitude,
        longitude=station.longitude,
        tz=tz,
        capacity_kw=ac_kw,
        dc_capacity_kw=dc_kw,
        tilt=station.tilt if station.tilt is not None else pv.default_tilt(station.latitude),
        azimuth=station.azimuth if station.azimuth is not None else 180.0,
        times=pd.DatetimeIndex(frame.index),
        ghi=frame["shortwave_radiation"].astype(float),
        dni=frame["direct_normal_irradiance"].astype(float),
        dhi=frame["diffuse_radiation"].astype(float),
        temp_air=frame["temperature_2m"].astype(float),
        wind_speed=frame["wind_speed_10m"].astype(float),
    )


def _hourly(station: Station, prep: Prepared, tz: str) -> pd.Series:
    if station.type == "wind":
        assert prep.v_hub is not None
        return wind.plant_power(prep.v_hub, station.capacity_kw)
    return pv.hourly_power(pv_inputs(station, prep.frame, tz))


def hourly_power(station: Station, prep: Prepared, tz: str) -> pd.Series:
    """逐时出力（kW）。不可算或容量口径待核验时全 NaN。发电预测与区域汇总都走这里。"""
    if not prep.complete or prep.blocked:
        return pd.Series(np.nan, index=prep.frame.index)
    return _hourly(station, prep, tz)


def target_day(fc: Forecast, day_offset: int) -> date:
    return (fc.current_hour() + pd.Timedelta(days=day_offset)).date()


def interval_end_labels(station: Station, day: date) -> bool:
    """该日逐时序列是否为区间末标签（光伏 v4）。出力约束按墙钟起点匹配时要减一小时。"""
    return station.type == "solar" and version_for_day(day) == "model-v4"


def grid_power(station: Station, hourly_kw: pd.Series, day: date) -> pd.Series | None:
    """可发出力 → 计入出力约束后的上网出力；站点没有规则返回 None。docs/17 §四"""
    rule = curtailment.parse(getattr(station, "curtailment", None))
    if rule is None:
        return None
    return curtailment.apply(
        hourly_kw, rule, station.capacity_kw, interval_end=interval_end_labels(station, day)
    )


def curtailment_note(station: Station) -> str | None:
    return curtailment.describe(curtailment.parse(getattr(station, "curtailment", None)))


def compute(station: Station, fc: Forecast, *, day_offset: int = 0) -> EnergySnapshot:
    """同步、CPU 密集。day_offset 为 0 时给当前功率，其余日期只有日曲线与指数。"""
    prep = prepare(station, fc, day_offset=day_offset)
    day = target_day(fc, day_offset)
    note = curtailment_note(station)
    if not prep.complete:
        nan = pd.Series(np.nan, index=prep.frame.index)
        return EnergySnapshot(
            index=None,
            daily_kwh=None,
            current_kw=None,
            hourly_kw=nan,
            estimated=prep.estimated,
            grid_hourly_kw=nan if note else None,
            curtailment_note=note,
        )

    if station.type == "solar":
        result = pv_index(pv_inputs(station, prep.frame, fc.tz))
        assert result.hourly_kw is not None
        hourly_kw = result.hourly_kw
    else:
        # 风电：全天 24 小时，不分昼夜
        hourly_kw = _hourly(station, prep, fc.tz)
        result = wind_index(float(hourly_kw.sum()), station.capacity_kw)
    grid_hourly = grid_power(station, hourly_kw, day)

    current = grid_current = None
    if day_offset == 0:
        label = current_label(fc, station.type)
        series_now, grid_now = hourly_kw, grid_hourly
        if label not in hourly_kw.index:
            # 单独读取真实目标区间，仍经过统一的缺测预处理。
            current_prep = prepare(station, fc, hours=pd.DatetimeIndex([label]))
            if current_prep.complete:
                series_now = _hourly(station, current_prep, fc.tz)
                grid_now = grid_power(station, series_now, day)
        current = _at_label(series_now, label)
        grid_current = _at_label(grid_now, label) if grid_now is not None else None
    return EnergySnapshot(
        index=result,
        daily_kwh=result.actual_kwh,
        current_kw=current,
        hourly_kw=hourly_kw,
        estimated=prep.estimated,
        blocked=prep.blocked,
        grid_hourly_kw=grid_hourly,
        grid_kwh=float(grid_hourly.sum()) if grid_hourly is not None else None,
        grid_current_kw=grid_current,
        curtailment_note=note,
    )


def summary_text(result: IndexResult | None, station_type: str) -> str:
    """规则模板的一句话结论。AI 接入前的兜底，接入后也是降级路径。docs/08 §六"""
    if result is None:
        return "气象数据获取中，指数暂不可算"
    # 文案跟着 classify 的分档走，不另写一套阈值 —— 否则改配置会出现「82 分 · 条件较差」
    base = _SUMMARY[result.level]

    if station_type == "solar":
        rad = next((a for a in result.attribution if a.factor.value == "radiation"), None)
        if rad and rad.delta <= -25:
            return f"{base}，云层影响明显"
        if rad and rad.delta <= -10:
            return f"{base}，存在轻度云层影响"
    return base
