/**
 * CSV 导出。带 UTF-8 BOM，Excel 直接打开不乱码；缺测留空，不写 0。
 * 数值保留接口的基础单位（kW、W/m²、m/s、%），单位写进表头，不做进位。
 */
export type CsvCell = string | number | null | undefined

interface Point {
  time: string
  value: number | null
}

const BOM = '\uFEFF'

export function toCsv(header: string[], rows: CsvCell[][]): string {
  const cell = (v: CsvCell) => {
    if (v == null || (typeof v === 'number' && !Number.isFinite(v))) return ''
    const s = typeof v === 'number' ? String(Number(v.toFixed(3))) : v
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return `${BOM}${[header, ...rows].map((r) => r.map(cell).join(',')).join('\r\n')}\r\n`
}

/** 带偏移的 ISO 时间 → 「2026-09-14 06:15」，保留电站当地时间，不换算时区。 */
export function csvLocalTime(iso: string): string {
  return iso.slice(0, 16).replace('T', ' ')
}

export interface PowerCsvDay {
  power_kw: Point[]
  grid_power_kw?: Point[] | null
}

/** 逐日预测功率拼成一张表，每个时刻一行；有上网口径时多一列。 */
export function powerCsv(days: PowerCsvDay[], valueLabel = '可发功率'): string {
  const hasGrid = days.some((d) => d.grid_power_kw?.length)
  const header = ['时间（电站当地时间）', `${valueLabel}(kW)`, ...(hasGrid ? ['预计上网功率(kW)'] : [])]
  const rows = days.flatMap((d) => {
    const grid = new Map(d.grid_power_kw?.map((p) => [p.time, p.value]))
    return d.power_kw.map((p): CsvCell[] => [
      csvLocalTime(p.time),
      p.value,
      ...(hasGrid ? [grid.get(p.time) ?? null] : []),
    ])
  })
  return toCsv(header, rows)
}

export interface WeatherCsvSeries {
  label: string
  unit: string
  points: Point[]
}

/** 多个气象指标按时刻对齐成一张表；某指标缺某个时刻时留空。 */
export function weatherCsv(series: WeatherCsvSeries[]): string {
  const times: string[] = []
  const seen = new Set<string>()
  for (const s of series) {
    for (const p of s.points) {
      if (!seen.has(p.time)) {
        seen.add(p.time)
        times.push(p.time)
      }
    }
  }
  const values = series.map((s) => new Map(s.points.map((p) => [p.time, p.value])))
  return toCsv(
    ['时间（电站当地时间）', ...series.map((s) => `${s.label}(${s.unit})`)],
    times.map((t) => [csvLocalTime(t), ...values.map((m) => m.get(t) ?? null)]),
  )
}
