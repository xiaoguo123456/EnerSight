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
  return n.toLocaleString('en-US', {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  })
}

/** 功率：≥ 1000 kW 进位为 MW。320 kW / 1.8 MW */
export function formatPower(kw: number): Formatted {
  if (Math.abs(kw) >= 1000) {
    return { value: thousands(kw / 1000, 1), unit: 'MW' }
  }
  return { value: thousands(kw), unit: 'kW' }
}

/**
 * 电量：≥ 1000 MWh（即 1e6 kWh）进位为 GWh，否则保持 kWh。
 * 2,400 kWh / 1.26 GWh —— 设计中不出现 MWh。docs/04 §十
 */
export function formatEnergy(kwh: number): Formatted {
  if (Math.abs(kwh) >= 1_000_000) {
    return { value: thousands(kwh / 1_000_000, 2), unit: 'GWh' }
  }
  return { value: thousands(kwh), unit: 'kWh' }
}

/** 减排量：API 传 kg，展示为吨。< 100 吨保留 1 位小数，否则取整 */
export function formatCo2(kg: number): Formatted {
  const t = kg / 1000
  return Math.abs(t) < 100
    ? { value: thousands(t, 1), unit: '吨' }
    : { value: thousands(t), unit: '吨' }
}

/** 金额：整数元 */
export function formatCurrency(yuan: number): Formatted {
  return { value: thousands(yuan), unit: '元' }
}

/** 温度：整数 */
export function formatTemperature(celsius: number): Formatted {
  return { value: thousands(Math.round(celsius)), unit: '℃' }
}

/** 风速：1 位小数 */
export function formatWindSpeed(ms: number): Formatted {
  return { value: thousands(ms, 1), unit: 'm/s' }
}

/** 辐射：整数 */
export function formatRadiation(wm2: number): Formatted {
  return { value: thousands(Math.round(wm2)), unit: 'W/m²' }
}

/** 百分比（云量、SOC 等）：整数 */
export function formatPercent(v: number): Formatted {
  return { value: thousands(Math.round(v)), unit: '%' }
}

/** 等效利用小时：1 位小数 */
export function formatHours(h: number): Formatted {
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
