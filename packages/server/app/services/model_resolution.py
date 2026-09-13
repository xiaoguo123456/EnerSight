"""自动选择模型（best_match）在境内落到哪个模型。docs/17 §二

2026-09-12 在青海、北京、广州三点上把 best_match 与 ecmwf_ifs 逐小时比对，六个变量三天内
全部一致。起报时间按 ecmwf_ifs 的元数据显示；每天复核一次，不一致就退回「无法确认起报」，
只显示拉取时间，不猜。
"""

import logging
from datetime import UTC, datetime

import httpx

from app.config import settings
from app.providers.budget import shared
from app.providers.open_meteo import META_SLUGS

log = logging.getLogger(__name__)

ASSUMED = "ecmwf_ifs"
# 青海、北京、广州：覆盖西北、华北、华南
CHECK_POINTS = ((36.6, 101.8), (40.0, 116.4), (23.1, 113.3))
CHECK_FIELDS = ("temperature_2m", "wind_speed_100m", "shortwave_radiation", "cloud_cover")

_state: dict[str, object] = {
    "resolved": ASSUMED,
    "checked_at": None,
    "reason": "未复核，沿用 2026-09-12 实测结论",
}


def resolve(model: str) -> str | None:
    """请求模型 → 元数据 slug；无法确认返回 None。"""
    if model in META_SLUGS:
        return META_SLUGS[model]
    if model == "best_match":
        resolved = _state["resolved"]
        return resolved if isinstance(resolved, str) else None
    return None


def status() -> dict[str, object]:
    return dict(_state)


def _set(resolved: str | None, reason: str) -> None:
    _state.update(resolved=resolved, checked_at=datetime.now(UTC).isoformat(), reason=reason)


async def check(http: httpx.AsyncClient) -> bool:
    """三点比对 best_match 与 ASSUMED。任一点、任一变量不一致即判定不等价。"""
    for lat, lon in CHECK_POINTS:
        series: dict[str, dict] = {}
        for model in ("best_match", ASSUMED):
            try:
                await shared.take(1)
                res = await http.get(
                    f"{settings.open_meteo_base}/forecast",
                    params={
                        "latitude": lat,
                        "longitude": lon,
                        "minutely_15": ",".join(CHECK_FIELDS),
                        "timezone": "UTC",
                        "forecast_days": 2,
                        "wind_speed_unit": "ms",
                        "models": model,
                    },
                )
                if res.status_code == 429:
                    shared.retry_after(res.headers.get("Retry-After"))
                res.raise_for_status()
                series[model] = res.json()["minutely_15"]
            except Exception:  # noqa: BLE001
                log.warning("model_resolution: 复核请求失败 %s %s", model, (lat, lon))
                return isinstance(_state["resolved"], str)  # 拿不到就维持现状
        a, b = series["best_match"], series[ASSUMED]
        for field in CHECK_FIELDS:
            if a.get(field) != b.get(field):
                _set(None, f"{(lat, lon)} 的 {field} 与 {ASSUMED} 不一致")
                log.warning("model_resolution: best_match 不再等于 %s（%s）", ASSUMED, field)
                return False
    _set(ASSUMED, "三点复核一致")
    return True
