"""保留请求时生成的预测与输入，不将后来更新的数据冒充原预测。"""

import hashlib
import json
from datetime import UTC, datetime

from app.render import tiles
from app.services.prediction_basis import version_for_day


def save(station, forecast, result):
    content = {
        "calculation_version": version_for_day(result.date),
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
        "weather_input": json.loads(
            forecast.hourly.to_json(orient="split", date_format="iso")
        ),
    }
    key = hashlib.sha256(f"{station.id}:{result.model}".encode()).hexdigest()[:24]
    folder = tiles.tile_dir().parent / "prediction-archive" / datetime.now(UTC).strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    # 同站同模型每小时最多一份；独占创建，不覆盖早先签发版本。
    name = f"{key}-{result.date}-{datetime.now(UTC):%H}-{version_for_day(result.date)}.json"
    path = folder / name
    try:
        with path.open("x") as f:
            json.dump(content, f, ensure_ascii=False, allow_nan=False)
    except FileExistsError:
        pass
