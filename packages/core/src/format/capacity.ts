/**
 * 场站表单的装机容量以 MW 输入，接口仍收发 kW 基础单位。docs/17 §一
 * 表单最多 3 位小数，换算后精确到 1 kW。
 */

/** kW → 表单里的 MW 文本，去掉浮点尾巴，如 49500 → "49.5"、500 → "0.5" */
export function capacityMwInput(kw: number): string {
  return String(Number((kw / 1000).toFixed(3)))
}

/** 表单 MW → 接口 kW，按 1 kW 取整，如 0.1 → 100 */
export function mwToKw(mw: number): number {
  return Math.round(mw * 1000)
}
