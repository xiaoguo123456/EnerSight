"""验证公网子路径下的静态图片路由，避免仅测图片落盘。"""

from fastapi.routing import Mount

from app.main import app


async def test_部署前缀下可读取带符号坐标的图片(client, tmp_path, monkeypatch):
    mount = next(r for r in app.routes if isinstance(r, Mount) and r.name == "tiles")
    monkeypatch.setattr(app, "root_path", "/enersight")
    monkeypatch.setattr(mount.app, "directory", str(tmp_path))
    monkeypatch.setattr(mount.app, "all_directories", [str(tmp_path)])
    folder = tmp_path / "satellite" / "+037.5_+0106.5"
    folder.mkdir(parents=True)
    png = b"\x89PNG\r\n\x1a\n"
    (folder / "frame.png").write_bytes(png)
    response = await client.get("/enersight/tiles/satellite/+037.5_+0106.5/frame.png")
    assert response.status_code == 200
    assert response.content == png
    assert response.headers["content-type"] == "image/png"
