"""Global Energy Monitor 数据下载。docs/04 §七

GEM 官网的下载表单是个网页组件，背后两步：提交联系人信息换 capability_token，
再用 token 换预签名下载地址。这里照做，联系人信息来自配置，每次同步都会在 GEM 那边
留一条提交记录，所以同步周期按月，不要更频繁。
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import settings

log = logging.getLogger(__name__)

TRACKERS = {"solar": "solar-power-tracker", "wind": "wind-power-tracker"}
LICENSE_TEXT = (
    "Creative Commons Attribution 4.0 International (CC BY 4.0) — "
    "https://creativecommons.org/licenses/by/4.0/"
)


@dataclass(frozen=True)
class Downloaded:
    type: str
    filename: str
    path: Path


def _headers(bearer: str) -> dict[str, str]:
    return {
        "content-type": "application/json",
        "apikey": settings.gem_supabase_key,
        "authorization": f"Bearer {bearer}",
    }


async def download(http: httpx.AsyncClient, type_: str, dest_dir: Path) -> Downloaded:
    if not settings.gem_contact_email:
        raise RuntimeError("未配置 ENERSIGHT_GEM_CONTACT_EMAIL，无法提交 GEM 下载表单")
    slug = TRACKERS[type_]
    payload = {
        "name": settings.gem_contact_name,
        "email": settings.gem_contact_email,
        "organization": settings.gem_contact_org,
        "sector": "Industry",
        "country": "China",
        "use_case": settings.gem_use_case,
        "license_text": LICENSE_TEXT,
        "email_optin": False,
        "request_mode": "slugs",
        "useragent": "EnerSight catalog sync",
        "page_url": f"https://globalenergymonitor.org/projects/global-{slug}/download-data/",
        "requested_slugs": [slug],
    }
    res = await http.post(
        settings.gem_mint_url, json=payload, headers=_headers(settings.gem_supabase_key), timeout=60
    )
    res.raise_for_status()
    token = res.json().get("capability_token")
    if not token:
        raise RuntimeError(f"GEM 表单未返回 capability_token: {res.text[:200]}")
    res = await http.post(settings.gem_presign_url, json={}, headers=_headers(token), timeout=60)
    res.raise_for_status()
    urls = res.json().get("urls") or []
    if not urls:
        raise RuntimeError("GEM presign 未返回下载地址")
    url, filename = urls[0]["url"], urls[0].get("filename") or f"{slug}.xlsx"
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / filename
    async with http.stream("GET", url, timeout=600) as r:
        r.raise_for_status()
        tmp = path.with_suffix(".part")
        with tmp.open("wb") as f:
            async for chunk in r.aiter_bytes():
                f.write(chunk)
        tmp.replace(path)
    log.info("gem download: %s → %s (%d bytes)", slug, path, path.stat().st_size)
    return Downloaded(type_, filename, path)
