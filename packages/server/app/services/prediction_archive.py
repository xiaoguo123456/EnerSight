"""保留请求时生成的预测与输入，不将后来更新的数据冒充原预测。"""

import hashlib
import json
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
        "weather_input": json.loads(forecast.hourly.to_json(orient="split", date_format="iso")),
        "quarter_input": json.loads(forecast.quarter.to_json(orient="split", date_format="iso"))
        if forecast.quarter is not None
        else None,
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
        "weather_input": json.loads(forecast.hourly.to_json(orient="split", date_format="iso")),
        "quarter_input": json.loads(forecast.quarter.to_json(orient="split", date_format="iso"))
        if forecast.quarter is not None
        else None,
    }
    try:
        with (folder / f"{digest}.json").open("x") as f:
            json.dump(content, f, ensure_ascii=False, allow_nan=False)
    except FileExistsError:
        pass


def save_fleet_inputs(snapshot, plants, cells, fetched, coverage) -> str:
    """留存区域计算的精确样本和原始天气；内容相同只存一份。"""
    from app.services.fleet_prediction import cell
    from app.services.prediction_basis import catalog_basis

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
                "province": p.province,
                "cell": cell(p),
                "covered_dates": coverage.get(p.id, []),
            }
            for p in sorted(plants, key=lambda p: p.id)
        ],
        "weather_cells": cells,
        "fetched_at_by_cell": fetched,
    }
    serialized = json.dumps(content, ensure_ascii=False, sort_keys=True, allow_nan=False)
    digest = hashlib.sha256(serialized.encode()).hexdigest()[:24]
    folder = tiles.tile_dir().parent / "prediction-fleet-inputs" / snapshot.date
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with (folder / f"{digest}.json").open("x") as f:
            f.write(serialized)
    except FileExistsError:
        pass
    return f"{snapshot.date}/{digest}"
