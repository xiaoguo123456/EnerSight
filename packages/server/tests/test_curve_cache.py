"""网格曲线缓存回归：命中跳过模型，失效与损坏回到真实计算。"""

import hashlib
import json
from unittest.mock import patch

import numpy as np
import pytest

from app.config import settings
from app.services import fleet_prediction as fleet
from app.services.curve_cache import CurveCache
from tests.test_prediction import forecast, plant


def digest(raw):
    return hashlib.sha256(json.dumps(raw).encode()).hexdigest()


def run(cache, plants, raw, model="gfs_global", lat=31.3):
    return fleet.calculate_cell(plants, raw, model, fleet.day_key(), lat, 120.6, cache, digest(raw))


@pytest.mark.parametrize("kind", ["solar", "wind"])
def test_重启后缓存命中跳过模型且按新装机容量换算(tmp_path, kind):
    raw = forecast()
    cache = CurveCache(tmp_path)
    first = run(cache, [plant("原站", kind=kind)], raw)
    assert cache.misses == 1 and cache.hits == 0
    restarted = CurveCache(tmp_path)
    with patch.object(fleet.energy, "prepare", side_effect=AssertionError("缓存命中不应计算")):
        second = run(restarted, [plant("新站", capacity=2000, kind=kind)], raw)
    assert restarted.hits == 1
    for a, b in zip(first[0][1], second[0][1], strict=True):
        np.testing.assert_allclose(a * 2, b, rtol=1e-12)
        assert len(b) == 96


@pytest.mark.parametrize(
    "change", ["weather", "model", "location", "parameters", "horizon", "basis"]
)
def test_输入或算法口径变化必须重新计算(tmp_path, monkeypatch, change):
    raw = forecast()
    cache = CurveCache(tmp_path)
    plants = [plant("测试站", kind="solar")]
    run(cache, plants, raw)
    kwargs = {}
    if change == "weather":
        raw["minutely_15"]["temperature_2m"][100] += 1
    elif change == "model":
        kwargs["model"] = "icon_global"
    elif change == "location":
        kwargs["lat"] = 32.3
    elif change == "parameters":
        monkeypatch.setattr(settings, "pv_losses", settings.pv_losses + 0.01)
    elif change == "horizon":
        monkeypatch.setattr(settings, "forecast_outlook_days", 6)
    elif change == "basis":
        plants[0].provenance["phases"][0]["capacity_rating"] = "dc"
    with patch.object(fleet.energy, "prepare", wraps=fleet.energy.prepare) as compute:
        run(cache, plants, raw, **kwargs)
        assert compute.call_count == fleet.days_count()
    assert cache.misses == 2


@pytest.mark.parametrize("bad", ["损坏文件", '{"key":"错误","curves":[]}', "null"])
def test_坏文件重算修复(tmp_path, bad):
    raw = forecast()
    cache = CurveCache(tmp_path)
    plants = [plant("测试站")]
    expected = run(cache, plants, raw)
    path = next(tmp_path.glob("*.json"))
    path.write_text(bad)
    actual = run(cache, plants, raw)
    np.testing.assert_allclose(actual[0][1], expected[0][1])
    with patch.object(fleet.energy, "prepare", side_effect=AssertionError("修复后应命中")):
        run(CurveCache(tmp_path), plants, raw)
    assert not list(tmp_path.glob("*.tmp"))


def test_不可算与真实零值保持区别(tmp_path):
    cache = CurveCache(tmp_path)
    cache.put("测试键", [None, np.zeros(96)])
    result = cache.get("测试键", 2)
    assert result[0] is None
    assert result[1].shape == (96,) and not result[1].any()
    (tmp_path / "测试键.json").write_text(json.dumps({"key": "测试键", "curves": [[0] * 24]}))
    assert cache.get("测试键", 1) is None


def test_缓存写入失败不丢弃本次有效曲线(tmp_path):
    cache = CurveCache(tmp_path)
    with patch.object(cache, "put", side_effect=OSError("磁盘缓存不可写")):
        result = run(cache, [plant("测试站")], forecast())
    assert len(result) == 1 and all(len(day) == 96 for day in result[0][1])
