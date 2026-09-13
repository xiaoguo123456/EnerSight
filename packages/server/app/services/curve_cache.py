"""全目录单位容量曲线缓存；原始气象、算法、日期或设备口径变化即失效。"""

import hashlib
import json
import os
import tempfile
from pathlib import Path

import numpy as np

from app.services.prediction_basis import calculation_version


class CurveCache:
    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    def key(self, *, weather_digest, model, day, latitude, longitude, parameters, days):
        basis = {
            "schema": 1,
            "weather": weather_digest,
            "model": model,
            "day": day,
            "latitude": latitude,
            "longitude": longitude,
            "parameters": parameters,
            "days": days,
            "resolution_minutes": 15,
            "calculation_version": calculation_version(day),
        }
        return hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()

    def get(self, key: str, days: int) -> list[np.ndarray | None] | None:
        try:
            raw = json.loads((self.folder / f"{key}.json").read_text())
            if raw.get("key") != key or len(raw["curves"]) != days:
                raise ValueError("曲线缓存标识或天数不一致")
            curves = []
            for row in raw["curves"]:
                curve = None if row is None else np.asarray(row, dtype=float)
                if curve is not None and (curve.shape != (96,) or not np.isfinite(curve).all()):
                    raise ValueError("曲线缓存长度或数值无效")
                curves.append(curve)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.misses += 1
            return None
        self.hits += 1
        return curves

    def put(self, key: str, curves: list[np.ndarray | None]):
        raw = {"key": key, "curves": [None if c is None else c.tolist() for c in curves]}
        fd, temporary = tempfile.mkstemp(prefix=".curve-", suffix=".tmp", dir=self.folder)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(raw, stream, allow_nan=False, separators=(",", ":"))
            os.replace(temporary, self.folder / f"{key}.json")
        finally:
            Path(temporary).unlink(missing_ok=True)
