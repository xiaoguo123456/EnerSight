"""曲线粒度兼容：线上旧版小程序只认逐小时曲线。docs/06 §2.8

新版客户端带 `X-Resolution-Minutes: 15` 取 15 分钟曲线；不带的在响应出口降为逐小时。
换算只用已算好的同一份结果，不额外请求气象上游。旧版正式版停用后删除本模块与客户端请求头。
"""

from contextvars import ContextVar
from datetime import datetime
from typing import cast

from app.schemas.home import (
    HomeResponse,
    StationDetailResponse,
    TrendMetric,
    TrendPoint,
    TrendSeries,
)
from app.schemas.prediction import (
    DailyOutlook,
    FleetDay,
    FleetPrediction,
    GenerationPrediction,
    PowerPoint,
    StationOutlook,
)

HEADER = "X-Resolution-Minutes"
FINE = "15"
legacy_hourly: ContextVar[bool] = ContextVar("legacy_hourly", default=False)


async def middleware(request, call_next):
    token = legacy_hourly.set(request.headers.get(HEADER) != FINE)
    try:
        return await call_next(request)
    finally:
        legacy_hourly.reset(token)


def _mean(values: list[float | None], n: int) -> float | None:
    """凑齐 n 格且无缺测才给均值，缺一格即 null，不拿剩余格子冒充整段。"""
    if len(values) != n or any(v is None for v in values):
        return None
    return round(sum(cast(list[float], values)) / n, 3)


def hourly_power(points: list[PowerPoint]) -> list[PowerPoint]:
    """功率曲线标注区间起点：00:00–00:45 四格的平均功率即 00:00 这一小时，电量不变。"""
    groups: dict[datetime, list[float | None]] = {}
    for p in points:
        hour = datetime.fromisoformat(p.time).replace(minute=0, second=0, microsecond=0)
        groups.setdefault(hour, []).append(p.value)
    return [PowerPoint(time=h.isoformat(), value=_mean(v, 4)) for h, v in groups.items()]


def hourly_trend(series: TrendSeries) -> TrendSeries:
    """瞬时量取整点值；辐射是区间均值标在区间末，取整点及之前三格平均（首点只有自身一格）。"""
    if series.resolution_minutes != 15:
        return series
    pts = series.points
    out: list[TrendPoint] = []
    for i, p in enumerate(pts):
        if datetime.fromisoformat(p.time).minute:
            continue
        if series.metric == TrendMetric.RADIATION:
            window = [q.value for q in pts[max(0, i - 3) : i + 1]]
            value = _mean(window, len(window))
        else:
            value = p.value
        out.append(TrendPoint(time=p.time, value=value))
    return series.model_copy(update={"points": out, "resolution_minutes": 60})


def _curve[C: GenerationPrediction | FleetDay | DailyOutlook](m: C) -> C:
    if m.resolution_minutes != 15:
        return m
    update: dict[str, object] = {"power_kw": hourly_power(m.power_kw), "resolution_minutes": 60}
    grid = getattr(m, "grid_power_kw", None)
    if grid is not None:
        update["grid_power_kw"] = hourly_power(grid)
    return m.model_copy(update=update)


def adapt[T](data: T) -> T:
    """只处理带曲线的响应，其余原样返回。全目录快照等缓存对象用 model_copy，不原地修改。"""
    if not legacy_hourly.get():
        return data
    result: object = data
    match data:
        case TrendSeries():
            result = hourly_trend(data)
        case FleetPrediction():
            result = _curve(data).model_copy(update={"days": [_curve(d) for d in data.days]})
        case GenerationPrediction():
            result = _curve(data)
        case StationOutlook():
            result = data.model_copy(update={"days": [_curve(d) for d in data.days]})
        case HomeResponse():
            result = data.model_copy(
                update={
                    "prediction": data.prediction and _curve(data.prediction),
                    "trends": data.trends and hourly_trend(data.trends),
                }
            )
        case StationDetailResponse():
            result = data.model_copy(update={"trends": data.trends and hourly_trend(data.trends)})
    return cast(T, result)
