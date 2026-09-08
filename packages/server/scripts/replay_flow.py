"""光流外推历史回放。docs/07 §八

用法：
  uv run python scripts/replay_flow.py --lat 31.30 --lon 120.62 --start 2026-09-10 --end 2026-10-10

需要 data/archive/himawari 里有归档帧（定时任务 archive_cloud 每 10 分钟攒一帧）。
白天用可见光、夜间用红外，与线上分析一致；两段分别统计。
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.satellite import archive, replay  # noqa: E402
from app.services import satellite as svc  # noqa: E402

REPORT_DIR = Path(__file__).resolve().parents[3] / "docs" / "reports"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument("--end", type=date.fromisoformat, required=True)
    args = ap.parse_args()

    bbox = svc.station_bbox(args.lat, args.lon)
    times: dict[str, list[datetime]] = {"visible": [], "infrared": []}
    day = args.start
    while day <= args.end:
        d = datetime(day.year, day.month, day.day, tzinfo=UTC)
        for band in times:
            for t in archive.archived_times(band, d):
                if svc.analysis_band(args.lat, args.lon, t) == band:
                    times[band].append(t)
        day += timedelta(days=1)

    lines = [
        f"# 光流外推回放 {date.today()}\n",
        f"站点 ({args.lat}, {args.lon})，{args.start} ~ {args.end}，"
        f"容差 ±{replay.TOLERANCE_MIN} 分钟，虚警宽限 {replay.FALSE_ALARM_GRACE_MIN} 分钟。\n",
        "| 波段 | 帧数 | 预测数 | 命中率 | 平均绝对误差 min | 偏差 | 虚警 | 漏报 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for band, ts in times.items():
        st = replay.evaluate(ts, band, bbox, args.lat, args.lon, svc.CLOUD_THRESHOLD[band])
        hr = f"{st.hit_rate:.0%}" if st.hit_rate is not None else "—"
        mae = f"{st.mae_min:.0f}" if st.mae_min is not None else "—"
        lines.append(
            f"| {band} | {st.frames} | {st.predictions} | {hr} | {mae} | {st.off_by} | "
            f"{st.false_alarms} | {st.misses} |"
        )
        print(
            f"{band}: frames={st.frames} predictions={st.predictions} hit={hr} mae={mae} "
            f"off={st.off_by} fa={st.false_alarms} miss={st.misses}"
        )
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"flow-replay-{date.today()}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"报告：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
