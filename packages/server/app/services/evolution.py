"""预报演变：同一目标日历次起报的变化，收敛即高置信。docs/19 §二

气象模式每 6 小时重起报一轮，对同一天会陆续给出二十多份预报。产品此前只展示最新一份，
把历次按起报时刻排开就能回答「这个数该信几分」—— 不需要实测。

**固定单一模型**（`evolution_model`）。混着不同模型比，看到的是模型间差异而不是演变；
三模式的中位成员逐日会变，更不能用来串时间序列。
"""

import json
import logging
from datetime import date, datetime, timedelta

from app.config import settings
from app.render import tiles
from app.schemas.common import ConvergenceLevel
from app.schemas.prediction import ForecastEvolution, Issuance

log = logging.getLogger(__name__)


def _outlook_root():
    return tiles.tile_dir().parent / "prediction-outlook"


def _slot(basis: dict | None, generated_at: str) -> tuple[str, str | None]:
    """归并键与展示用的起报时刻。

    起报拿不到时按拉取时刻所在的 6 小时片归并（上游 6 小时一批），并让 issued_at 保持 null ——
    不拿拉取时间冒充起报。docs/17 §二
    """
    issued = (basis or {}).get("issued_at")
    if issued:
        return issued, issued
    fetched = (basis or {}).get("fetched_at") or generated_at
    try:
        t = datetime.fromisoformat(fetched)
    except ValueError:
        return f"fetched:{fetched}", None
    return f"fetched:{t.date().isoformat()}T{t.hour // 6 * 6:02d}", None


def _lead_days(target: date, issued_at: str | None, generated_at: str) -> int:
    stamp = issued_at or generated_at
    try:
        when = datetime.fromisoformat(stamp).date()
    except ValueError:
        return 0
    return max((target - when).days, 0)


def _convergence(values: list[float]) -> tuple[ConvergenceLevel | None, float | None]:
    """最近几份起报的摆动幅度。不足两份不下结论。"""
    recent = values[-settings.evolution_issuances :]
    if len(recent) < 2:
        return None, None
    mean = sum(recent) / len(recent)
    if not mean:
        return None, None
    percent = round((max(recent) - min(recent)) / mean * 100, 1)
    if percent < settings.evolution_stable_pct:
        return ConvergenceLevel.STABLE, percent
    if percent < settings.evolution_swing_pct:
        return ConvergenceLevel.WOBBLE, percent
    return ConvergenceLevel.SWING, percent


def read(station_id: str, target: date, model: str | None = None) -> ForecastEvolution:
    """扫描时效窗口内的留档索引。只读，不触发计算，也不写留档。"""
    model = model or settings.evolution_model
    root = _outlook_root()
    # 目录按签发的 UTC 日期分；目标日是电站当地日期，两端各多扫一天避免时区错位漏掉
    days = [
        (target - timedelta(days=k)).isoformat()
        for k in range(-1, settings.evolution_lead_days + 2)
    ]
    target_iso = target.isoformat()
    best: dict[str, Issuance] = {}
    newest: dict[str, str] = {}
    for day in days:
        folder = root / day
        if not folder.is_dir():
            continue
        for path in folder.glob("*.meta.json"):
            try:
                with path.open(encoding="utf-8") as f:
                    row = json.load(f)
            except (OSError, ValueError):
                log.debug("evolution: 跳过无法读取的留档索引 %s", path, exc_info=True)
                continue
            if row.get("station_id") != station_id or row.get("model") != model:
                continue
            entry = next((d for d in row.get("days", []) if d.get("date") == target_iso), None)
            if entry is None:
                continue
            generated_at = row.get("generated_at") or ""
            key, issued_at = _slot(row.get("basis"), generated_at)
            # 同一批起报可能签发多次，留最后算的那份
            if key in newest and newest[key] >= generated_at:
                continue
            newest[key] = generated_at
            best[key] = Issuance(
                issued_at=issued_at,
                generated_at=generated_at,
                lead_days=_lead_days(target, issued_at, generated_at),
                model=model,
                energy_kwh=entry.get("energy_kwh"),
                peak_kw=entry.get("peak_kw"),
            )
    issuances = sorted(best.values(), key=lambda i: i.issued_at or i.generated_at)
    level, percent = _convergence([i.energy_kwh for i in issuances if i.energy_kwh is not None])
    return ForecastEvolution(
        station_id=station_id,
        date=target_iso,
        model=model,
        issuances=issuances,
        convergence=level,
        range_percent=percent,
    )


STABILITY_TEXT = {
    ConvergenceLevel.STABLE: "已收敛，近几轮起报基本一致",
    ConvergenceLevel.WOBBLE: "仍在波动，近几轮起报有一定变化",
    ConvergenceLevel.SWING: "来回摇摆，模式对这一天尚未拿准",
}


def stability_text(station_id: str, target: date) -> str | None:
    """给 AI 报告的一行文字。只有档位，不带摆动百分比与历次起报值 ——
    那些数字一旦进输入就进了数值一致性校验集，模型就能合法地引用它们。docs/08 §5.1"""
    level = read(station_id, target).convergence
    return STABILITY_TEXT[level] if level else None


def prune(now: datetime | None = None) -> int:
    """按保留天数清理预测留档。此前只写不清，目录会一直长。返回删除的目录数。"""
    import shutil

    now = now or datetime.now()
    cutoff = (now.date() - timedelta(days=settings.prediction_archive_retention_days)).isoformat()
    removed = 0
    root = tiles.tile_dir().parent
    for name in ("prediction-outlook", "prediction-archive"):
        base = root / name
        if not base.is_dir():
            continue
        for folder in base.iterdir():
            if folder.is_dir() and folder.name < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
    return removed
