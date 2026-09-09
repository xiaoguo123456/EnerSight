"""保留请求时生成的预测与输入，不将后来更新的数据冒充原预测。"""

import hashlib
import json
from datetime import UTC, datetime

from app.render import tiles
from app.services.prediction_basis import VERSION


def save(station, forecast, result):
    content = {
        "calculation_version": VERSION,
        "station_id": station.id,
        "issued_at": result.generated_at,
        "target_date": result.date,
        "model": result.model,
        "kind": "滚动预测留档，非实测",
        "capacity_kw": station.capacity_kw,
        "capacity_basis": getattr(station, "_pv_capacity", None),
        "prediction": result.model_dump(),
        "weather_input": json.loads(
            forecast.hourly.loc[result.date].to_json(orient="split", date_format="iso")
        ),
    }
    key = hashlib.sha256(f"{station.id}:{result.model}".encode()).hexdigest()[:24]
    folder = tiles.tile_dir().parent / "prediction-archive" / datetime.now(UTC).strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    # 同站同模型每小时最多一份；独占创建，不覆盖早先签发版本。
    path = folder / f"{key}-{result.date}-{datetime.now(UTC):%H}.json"
    try:
        with path.open("x") as f:
            json.dump(content, f, ensure_ascii=False, allow_nan=False)
    except FileExistsError:
        pass
