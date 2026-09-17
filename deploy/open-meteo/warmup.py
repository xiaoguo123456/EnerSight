#!/usr/bin/env python3
"""在自建气象实例本机预热公开目录的站点坐标。

**为什么需要**：实例的冷读成本是「每个新坐标」的 —— 实测单点全字段首次 5–6 秒、
同坐标重打 0.9–1.1 秒，邻近坐标沾不到热（0.1° 外仍 4.6 秒）。公开目录有一万多个唯一
坐标，应用侧单点预报缓存只有进程内 512 槽，浏览公开电站几乎必冷。

**为什么必须在本机跑**：北京→本机的大响应实测只有 16–18 KB/s（7.57 MB 的已热批次
要 421 秒），从北京驱动一万多个坐标要 13 小时，超过 6 小时的批次周期。本机走 localhost
实测 0.9 秒/坐标（100 个一批），一万多个约 2.8 小时，3 并发约 1 小时。

**只读不写**：响应体直接丢掉，目的只是让实例把这些格点的字节区间读进本地 LRU 缓存。
预热之后北京打同一坐标就只剩跨太平洋传输那 ~1 秒。

数据与推导见仓库 docs/2026-09-16-open-meteo-self-host.md。

用法（cron 见 README）：
    ./warmup.py                      # 正常一轮
    ./warmup.py --limit 300          # 只跑前 300 个坐标，验证用
    ./warmup.py --concurrency 1      # 降并发
"""

import argparse
import fcntl
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# 站点详情用的 15 个字段，必须和应用一致 —— 少一个字段就有一批变量文件没被读进缓存，
# 页面请求照样冷。清单见 packages/server/app/providers/open_meteo.py 的 HOURLY_FIELDS
FIELDS = (
    "temperature_2m,wind_speed_10m,wind_speed_80m,wind_speed_100m,wind_speed_120m,"
    "wind_speed_200m,shortwave_radiation,diffuse_radiation,direct_normal_irradiance,"
    "surface_pressure,cloud_cover,weather_code,apparent_temperature,"
    "relative_humidity_2m,wind_direction_10m"
)
# 与应用的单点预报请求同形：forecast_days=8 含第七天末边界，past_days=1 供昨日环比
FORECAST_DAYS = 8
PAST_DAYS = 1
# best_match 在境内解析为 ecmwf_ifs，和页面默认一致；显式指定模型的请求很少，不预热
MODEL = "best_match"

DEFAULT_LIST_URL = "https://platform.qhzhiyin.com/enersight/tiles/warm-coords.json"
DEFAULT_BASE = "http://127.0.0.1:8090/v1/forecast"
STATE_DIR = os.environ.get("WARMUP_STATE_DIR", "/opt/open-meteo")
CACHED_LIST = os.path.join(STATE_DIR, "warm-coords.cached.json")
LOCK_FILE = os.path.join(STATE_DIR, "warmup.lock")


def log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {message}", flush=True)


def fetch_coords(url: str, timeout: float) -> list[list[float]]:
    """取坐标清单；取不到就用上一次缓存的那份。

    宁可用旧清单也不要空跑：目录按月才变一次，旧清单覆盖率几乎一样；
    而清单取不到就跳过整轮预热，页面会整整 6 小时都是冷的。
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read()
        payload = json.loads(raw)
        coords = payload["coords"]
        if not isinstance(coords, list) or not coords:
            raise ValueError("清单为空")
        tmp = CACHED_LIST + ".tmp"
        with open(tmp, "wb") as handle:
            handle.write(raw)
        os.replace(tmp, CACHED_LIST)
        log(f"清单已更新：{len(coords)} 个坐标，导出于 {payload.get('generated_at')}")
        return coords
    except Exception as exc:  # noqa: BLE001  取不到清单不能让整轮预热停掉
        log(f"清单拉取失败（{type(exc).__name__}: {exc}），改用本地缓存")
        try:
            with open(CACHED_LIST, "rb") as handle:
                payload = json.load(handle)
            coords = payload["coords"]
            log(f"本地缓存：{len(coords)} 个坐标，导出于 {payload.get('generated_at')}")
            return coords
        except Exception as inner:  # noqa: BLE001
            log(f"本地缓存也不可用（{type(inner).__name__}: {inner}），本轮跳过")
            return []


def warm_batch(base: str, batch: list[list[float]], timeout: float) -> tuple[int, float]:
    """打一批坐标，丢掉响应体。返回 (坐标数, 耗时秒)；失败返回 (0, 耗时)。"""
    query = urllib.parse.urlencode(
        {
            "latitude": ",".join(f"{lat:.5f}" for lat, _ in batch),
            "longitude": ",".join(f"{lon:.5f}" for _, lon in batch),
            "models": MODEL,
            "timezone": "auto",
            "wind_speed_unit": "ms",
            "forecast_days": FORECAST_DAYS,
            "past_days": PAST_DAYS,
            "minutely_15": FIELDS,
        }
    )
    started = time.time()
    try:
        with urllib.request.urlopen(f"{base}?{query}", timeout=timeout) as response:
            # 不解析、不留存：目的只是让实例把这些格点读进缓存
            while response.read(1 << 20):
                pass
        return len(batch), time.time() - started
    except Exception as exc:  # noqa: BLE001  单批失败不影响其余批次
        log(f"  批次失败（{len(batch)} 个坐标，{type(exc).__name__}: {exc}）")
        return 0, time.time() - started


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-url", default=DEFAULT_LIST_URL)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--batch", type=int, default=100, help="每批坐标数，默认 100")
    parser.add_argument("--concurrency", type=int, default=3, help="并发批次数，默认 3")
    parser.add_argument("--timeout", type=float, default=1800, help="单批超时秒，默认 1800")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 个坐标，0 为全部")
    args = parser.parse_args()

    os.makedirs(STATE_DIR, exist_ok=True)
    lock = open(LOCK_FILE, "w")  # noqa: SIM115  要在整个进程生命周期持有
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("上一轮预热还在跑，本轮跳过")
        return 0

    coords = fetch_coords(args.list_url, timeout=60)
    if not coords:
        return 1
    if args.limit:
        coords = coords[: args.limit]

    batches = [coords[i : i + args.batch] for i in range(0, len(coords), args.batch)]
    log(f"开始预热：{len(coords)} 个坐标 / {len(batches)} 批 / 并发 {args.concurrency}")
    started = time.time()
    done = failed = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(warm_batch, args.base, b, args.timeout) for b in batches]
        for index, future in enumerate(futures, 1):
            count, seconds = future.result()
            done += count
            failed += 0 if count else 1
            if index % 10 == 0 or index == len(futures):
                elapsed = time.time() - started
                rate = done / elapsed if elapsed else 0
                remaining = (len(coords) - done) / rate if rate else 0
                log(
                    f"  {index}/{len(batches)} 批：已热 {done} 个坐标，"
                    f"失败 {failed} 批，{elapsed / 60:.1f} 分钟，"
                    f"{rate:.2f} 坐标/秒，预计还需 {remaining / 60:.0f} 分钟"
                )
    elapsed = time.time() - started
    log(
        f"预热结束：{done}/{len(coords)} 个坐标，失败 {failed} 批，"
        f"{elapsed / 60:.1f} 分钟，{done / elapsed if elapsed else 0:.2f} 坐标/秒"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
