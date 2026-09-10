"""原生高斯网格及时间口径回归，不依赖在线气象数据。"""

import io
from datetime import UTC, datetime

import numpy as np
import pytest
import respx
from PIL import Image

from app.errors import DataUnavailable
from app.render import hres
from app.services.hres_layer import render


def test_O1280点数及南北对称():
    lat, counts, offsets = hres.geometry()
    assert len(lat) == 2560 and offsets[-1] == 6599680
    assert counts[0] == counts[-1] == 20
    assert np.allclose(lat, -lat[::-1])
    assert np.all(np.diff(lat) < 0)


def test_原生纬带插值保留线性温度场及缺测():
    lat, counts, _ = hres.geometry()
    first, last = 800, 804
    data = np.concatenate([np.full(counts[y], lat[y]) for y in range(first, last + 1)])
    targets = np.linspace(lat[last], lat[first], 10)
    result = hres.regrid(data, first, last, targets, np.array([110.0, 120.0, -10.0]))
    assert np.allclose(result, targets[:, None], atol=1e-5)
    data[:] = np.nan
    assert np.isnan(hres.regrid(data, first, last, targets, np.array([120.0]))).all()


def test_温度风取瞬时辐射取区间末且拒绝缺帧():
    m = {
        "reference_time": "2026-09-09T18:00Z",
        "valid_times": ["2026-09-10T00:00Z", "2026-09-10T01:00Z"],
    }
    now = datetime(2026, 9, 10, 0, 30, tzinfo=UTC)
    assert hres.valid_time(m, "temperature", now).hour == 0
    assert hres.valid_time(m, "wind", now).hour == 0
    assert hres.valid_time(m, "radiation", now).hour == 1
    m["valid_times"].pop()
    with pytest.raises(DataUnavailable):
        hres.valid_time(m, "radiation", now)


def test_缺测图片透明不填零():
    image = np.asarray(Image.open(io.BytesIO(render(np.full((10, 10), np.nan), "temperature"))))
    assert image.shape == (512, 512, 4)
    assert not image[:, :, 3].any()


def test_HTTP分块合并且拒绝服务器忽略Range():
    url = "https://example.com/data.om"
    with respx.mock as mock:
        mock.head(url).respond(200, headers={"content-length": "300000"})
        route = mock.get(url).respond(
            206, content=b"x" * 262144, headers={"content-range": "bytes 0-262143/300000"}
        )
        fs = hres.RangeFS()
        try:
            assert fs.cat_file(url, 10, 20) == b"x" * 10
            assert fs.cat_file(url, 30, 40) == b"x" * 10
            assert route.call_count == 1
            route.respond(200, content=b"x" * 37856)
            with pytest.raises(ValueError):
                fs.cat_file(url, 280000, 280010)
            route.respond(
                206, content=b"x" * 37856, headers={"content-range": "bytes 0-37855/300000"}
            )
            with pytest.raises(ValueError):
                fs.cat_file(url, 280000, 280010)
        finally:
            fs.close()
