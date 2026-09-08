"""腾讯位置服务 WebService API。

⚠️ 腾讯用 GCJ-02：调用前把 WGS84 转过去，拿到的结果转回来。
存储与计算一律 WGS84。docs/06 §2.2
"""

import httpx

from app.config import settings
from app.errors import UpstreamUnavailable
from app.geo import gcj02_to_wgs84, wgs84_to_gcj02


class TencentLBS:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    @property
    def enabled(self) -> bool:
        return bool(settings.tencent_lbs_key)

    async def reverse(self, latitude: float, longitude: float) -> dict | None:
        """逆地理编码。返回 {address, province, city, district}，失败返回 None。"""
        if not self.enabled:
            return None
        g_lng, g_lat = wgs84_to_gcj02(longitude, latitude)
        data = await self._get(
            "/geocoder/v1/", {"location": f"{g_lat},{g_lng}", "key": settings.tencent_lbs_key}
        )
        if not data:
            return None
        comp = data.get("address_component", {})
        return {
            "address": "".join(comp.get(k, "") for k in ("province", "city", "district")),
            "province": comp.get("province", ""),
            "city": comp.get("city", ""),
            "district": comp.get("district", ""),
        }

    async def suggest(self, keyword: str, region: str | None = None) -> list[dict]:
        """地点联想。返回 WGS84 坐标。"""
        if not self.enabled:
            return []
        params = {"keyword": keyword, "key": settings.tencent_lbs_key, "page_size": 8}
        if region:
            params["region"] = region
        data = await self._get("/place/v1/suggestion", params, root="data")
        out = []
        for item in data or []:
            loc = item.get("location") or {}
            if "lat" not in loc:
                continue
            lng, lat = gcj02_to_wgs84(float(loc["lng"]), float(loc["lat"]))
            out.append(
                {
                    "name": item.get("title", ""),
                    "address": item.get("address", ""),
                    "latitude": lat,
                    "longitude": lng,
                }
            )
        return out

    async def _get(self, path: str, params: dict, root: str = "result"):
        try:
            res = await self._http.get(settings.tencent_lbs_base + path, params=params)
            res.raise_for_status()
            body = res.json()
        except httpx.HTTPError as exc:
            raise UpstreamUnavailable("地图服务暂时不可用") from exc
        # 腾讯用 status 字段表示业务错误，HTTP 仍是 200
        if body.get("status") != 0:
            return None
        return body.get(root)
