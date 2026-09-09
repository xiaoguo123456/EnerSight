/**
 * 展示层格式化。
 *
 * API 返回基础单位的裸数值（kW / kWh / kg / ℃ / m/s / W/m² / 元），
 * 进位、千分位、小数位一律在这里处理。见 docs/04 §十、docs/06 §2.3。
 *
 * 每个函数返回 { value, unit } 而非拼好的字符串 —— 设计规范要求
 * 单位与数值分开排版（单位字号约为数值的 40%，颜色降一级）。见 docs/02 §三。
 */

export interface Formatted {
  /** 数值部分，已含千分位 */
  value: string
  /** 单位部分，可能为空字符串 */
  unit: string
}

/** 数值 ≥ 1000 时加千分位分隔符。docs/04 §十 */
export function thousands(n: number, fractionDigits = 0): string {
  // 部分小程序真机忽略 toLocaleString 的小数位参数，显式舍入后再加分隔符。
  const [integer = '', fraction] = n.toFixed(fractionDigits).split('.')
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return fraction === undefined ? grouped : `${grouped}.${fraction}`
}

/** 功率：≥ 1000 kW 进位为 MW。320 kW / 1.8 MW */
export function formatPower(kw: number | null | undefined): Formatted {
  if (kw == null || !Number.isFinite(kw)) return { value: '—', unit: 'kW' }
  if (Math.abs(kw) >= 1000) {
    return { value: thousands(kw / 1000, 1), unit: 'MW' }
  }
  return { value: thousands(kw), unit: 'kW' }
}

/**
 * 电量：≥ 1000 MWh（即 1e6 kWh）进位为 GWh，否则保持 kWh。
 * 2,400 kWh / 1.26 GWh —— 设计中不出现 MWh。docs/04 §十
 */
export function formatEnergy(kwh: number | null | undefined): Formatted {
  if (kwh == null || !Number.isFinite(kwh)) return { value: '—', unit: 'kWh' }
  if (Math.abs(kwh) >= 1_000_000) {
    return { value: thousands(kwh / 1_000_000, 2), unit: 'GWh' }
  }
  return { value: thousands(kwh), unit: 'kWh' }
}

/** 减排量：API 传 kg，展示为吨。< 100 吨保留 1 位小数，否则取整 */
export function formatCo2(kg: number | null | undefined): Formatted {
  if (kg == null || !Number.isFinite(kg)) return { value: '—', unit: '吨' }
  const t = kg / 1000
  return Math.abs(t) < 100
    ? { value: thousands(t, 1), unit: '吨' }
    : { value: thousands(t), unit: '吨' }
}

/** 金额：整数元 */
export function formatCurrency(yuan: number | null | undefined): Formatted {
  if (yuan == null || !Number.isFinite(yuan)) return { value: '—', unit: '元' }
  return { value: thousands(yuan), unit: '元' }
}

/** 温度：整数 */
export function formatTemperature(celsius: number | null | undefined): Formatted {
  if (celsius == null || !Number.isFinite(celsius)) return { value: '—', unit: '℃' }
  return { value: thousands(Math.round(celsius)), unit: '℃' }
}

/** 风速：1 位小数 */
export function formatWindSpeed(ms: number | null | undefined): Formatted {
  if (ms == null || !Number.isFinite(ms)) return { value: '—', unit: 'm/s' }
  return { value: thousands(ms, 1), unit: 'm/s' }
}

/** 辐射：整数 */
export function formatRadiation(wm2: number | null | undefined): Formatted {
  if (wm2 == null || !Number.isFinite(wm2)) return { value: '—', unit: 'W/m²' }
  return { value: thousands(Math.round(wm2)), unit: 'W/m²' }
}

/** 百分比（云量、SOC 等）：整数 */
export function formatPercent(v: number | null | undefined): Formatted {
  if (v == null || !Number.isFinite(v)) return { value: '—', unit: '%' }
  return { value: thousands(Math.round(v)), unit: '%' }
}

/** 等效利用小时：1 位小数 */
export function formatHours(h: number | null | undefined): Formatted {
  if (h == null || !Number.isFinite(h)) return { value: '—', unit: 'h' }
  if (h > 0 && h < 0.1) return { value: '<0.1', unit: 'h' }
  return { value: thousands(h, 1), unit: 'h' }
}

/** 经纬度：2 位小数带方位。31.30°N，120.62°E */
export function formatCoordinate(latitude: number, longitude: number): string {
  const lat = `${Math.abs(latitude).toFixed(2)}°${latitude >= 0 ? 'N' : 'S'}`
  const lng = `${Math.abs(longitude).toFixed(2)}°${longitude >= 0 ? 'E' : 'W'}`
  return `${lat}，${lng}`
}

export interface FormattedDelta {
  text: string
  /** 变化方向。注意：上升红、下降绿，表示方向而非好坏。docs/02 §二 */
  direction: 'up' | 'down'
}

/**
 * 环比标签。`null` 表示无昨日数据 —— 调用方必须隐藏整个标签，
 * 不要显示 0%。docs/07 §3.3
 */
export function formatDelta(deltaPercent: number | null): FormattedDelta | null {
  if (deltaPercent === null || !Number.isFinite(deltaPercent)) return null
  const rounded = Math.round(deltaPercent)
  if (rounded === 0) return null
  return {
    text: `${Math.abs(rounded)}%`,
    direction: rounded > 0 ? 'up' : 'down',
  }
}

/** 所有界面时间统一为北京时间，不依赖设备时区。输入必须包含时区。 */
export function formatBeijingTime(value: string | null | undefined): string {
  if (!value || !/(Z|[+-]\d{2}:\d{2})$/.test(value)) return '暂无时间'
  const timestamp = Date.parse(value)
  if (!Number.isFinite(timestamp)) return '暂无时间'
  return new Date(timestamp + 8 * 3600_000).toISOString().slice(5, 16).replace('T', ' ')
}
export function isDataStale(value: string | null | undefined, minutes = 120, now = Date.now()): boolean {
  if (!value) return true
  const timestamp = Date.parse(value)
  return !Number.isFinite(timestamp) || now - timestamp > minutes * 60_000
}
