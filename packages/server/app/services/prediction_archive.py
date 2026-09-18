"""保留请求时生成的预测与输入，不将后来更新的数据冒充原预测。"""

import hashlib
import json
import os
import tempfile
from contextlib import suppress
from datetime import UTC, datetime

from app.render import tiles
from app.services.prediction_basis import (
    calculation_parameters,
    calculation_version,
    station_parameters,
)


def save(station, forecast, result):
    content = {
        "calculation_version": calculation_version(result.date),
        "calculation_parameters": calculation_parameters(),
        "elevation": forecast.elevation,
        "station_parameters": station_parameters(station),
        "station_id": station.id,
        "issued_at": result.generated_at,
        # 模型起报与拉取时刻：将来接实测后按预报时效回测要用。docs/17 §二
        "basis": result.basis.model_dump() if result.basis else None,
        "target_date": result.date,
        "model": result.model,
        "kind": "滚动预测留档，非实测",
        "capacity_kw": station.capacity_kw,
        "capacity_basis": getattr(station, "_pv_capacity", None),
        "prediction": result.model_dump(),
        "weather_input": json.loads(forecast.data.to_json(orient="split", date_format="iso")),
        "weather_resolution_minutes": forecast.step_minutes,
    }
    key = hashlib.sha256(f"{station.id}:{result.model}".encode()).hexdigest()[:24]
    folder = tiles.tile_dir().parent / "prediction-archive" / datetime.now(UTC).strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    # 同站同模型每小时最多一份；独占创建，不覆盖早先签发版本。
    identity = {
        "station": station_parameters(station),
        "version": calculation_version(result.date),
        "basis": content["basis"],
        "resolution": result.resolution_minutes,
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    name = f"{key}-{result.date}-{datetime.now(UTC):%H}-{digest}.json"
    path = folder / name
    try:
        with path.open("x") as f:
            json.dump(content, f, ensure_ascii=False, allow_nan=False)
    except FileExistsError:
        pass


def purge_station_archives(station_ids: set[str]) -> int:
    """删除自建场站时一并删除它的预测留档，返回删除的文件数。

    滚动预测留档的文件名以 sha256(站点:模型) 前 24 位开头，按名匹配不读文件；
    七天预测留档首个键就是 station_id，只读文件头。
    """
    from app.weather_model import MODELS

    if not station_ids:
        return 0
    root = tiles.tile_dir().parent
    keys = {
        hashlib.sha256(f"{sid}:{model}".encode()).hexdigest()[:24]
        for sid in station_ids
        for model in MODELS
    }
    removed = 0
    for path in (root / "prediction-archive").glob("*/*.json"):
        if path.name[:24] in keys:
            path.unlink(missing_ok=True)
            removed += 1
    # 留档是 ensure_ascii=False 写的，比对用的前缀也必须是，否则非 ASCII 的 id 永远匹配不上
    heads = tuple(f'{{"station_id": {json.dumps(sid, ensure_ascii=False)}' for sid in station_ids)
    for path in (root / "prediction-outlook").glob("*/*.json"):
        try:
            with path.open(encoding="utf-8") as f:
                head = f.read(96)
        except OSError:
            continue
        if head.startswith(heads):
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def save_outlook(station, forecast, outlook):
    """按签发批次留存完整七天输出和全部计算输入，同批次同配置不重复写。"""
    parameters = station_parameters(station)
    identity = {
        "station_id": station.id,
        "station": parameters,
        "model": outlook.model,
        "basis": outlook.basis.model_dump() if outlook.basis else None,
        "version": outlook.calculation_version,
        "dates": [d.date for d in outlook.days],
        "steps": [d.resolution_minutes for d in outlook.days],
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    folder = tiles.tile_dir().parent / "prediction-outlook" / outlook.generated_at[:10]
    folder.mkdir(parents=True, exist_ok=True)
    content = {
        "station_id": station.id,
        "station_parameters": parameters,
        "calculation_parameters": calculation_parameters(),
        "elevation": forecast.elevation,
        "prediction": outlook.model_dump(),
        "weather_input": json.loads(forecast.data.to_json(orient="split", date_format="iso")),
        "weather_resolution_minutes": forecast.step_minutes,
    }
    try:
        with (folder / f"{digest}.json").open("x") as f:
            json.dump(content, f, ensure_ascii=False, allow_nan=False)
    except FileExistsError:
        pass
    _save_outlook_index(folder / f"{digest}.meta.json", station.id, outlook)


def _save_outlook_index(path, station_id: str, outlook) -> None:
    """预报演变只要每天的电量与峰值，完整留档带着整份气象输入，扫一遍太重。

    首个键仍是 station_id，删站时 purge_station_archives 按文件头就能一并清掉。
    """
    index = {
        "station_id": station_id,
        "model": outlook.model,
        "generated_at": outlook.generated_at,
        "basis": outlook.basis.model_dump() if outlook.basis else None,
        "days": [
            {"date": d.date, "energy_kwh": d.energy_kwh, "peak_kw": d.peak_kw} for d in outlook.days
        ],
    }
    try:
        with path.open("x") as f:
            json.dump(index, f, ensure_ascii=False, allow_nan=False)
    except FileExistsError:
        pass


def save_fleet_inputs(snapshot, plants, cells, fetched, coverage) -> str:
    """留存区域计算的精确样本和原始天气；内容相同只存一份。"""
    from app.services.fleet_prediction import cell
    from app.services.prediction_basis import catalog_basis, catalog_turbine_class

    content = {
        "model": snapshot.model,
        "date": snapshot.date,
        "batch_stamp": snapshot.batch_stamp,
        "basis": snapshot.basis.model_dump() if snapshot.basis else None,
        "calculation_version": snapshot.calculation_version,
        "calculation_parameters": calculation_parameters(),
        "catalog_revision": snapshot.catalog_revision,
        "plants": [
            {
                "id": p.id,
                "parameters": station_parameters(p),
                "capacity_basis": catalog_basis(p)[0],
                "turbine_class": catalog_turbine_class(p),
                "province": p.province,
                "cell": cell(p),
                "covered_dates": coverage.get(p.id, []),
            }
            for p in sorted(plants, key=lambda p: p.id)
        ],
        "weather_cells": cells,
        "fetched_at_by_cell": fetched,
    }
    # 网格逐个写入并同步算指纹，避免把完整 15 分钟天气复制成巨大的 JSON 字符串。
    folder = tiles.tile_dir().parent / "prediction-fleet-inputs" / snapshot.date
    folder.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, allow_nan=False)
    fd, temporary = tempfile.mkstemp(prefix=".input-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w") as f:

            def emit(value):
                digest.update(value.encode())
                f.write(value)

            emit("{")
            for i, key in enumerate(sorted(content)):
                if i:
                    emit(",")
                emit(json.dumps(key) + ":")
                if key == "weather_cells":
                    emit("{")
                    for j, cell_key in enumerate(sorted(cells)):
                        if j:
                            emit(",")
                        emit(json.dumps(cell_key) + ":")
                        for chunk in encoder.iterencode(cells[cell_key]):
                            emit(chunk)
                    emit("}")
                else:
                    for chunk in encoder.iterencode(content[key]):
                        emit(chunk)
            emit("}")
        identity = digest.hexdigest()[:24]
        path = folder / f"{identity}.json"
        # 硬链接独占发布，已有同指纹输入不覆盖；临时文件始终清理。
        with suppress(FileExistsError):
            os.link(temporary, path)
        return f"{snapshot.date}/{identity}"
    finally:
        os.unlink(temporary)
