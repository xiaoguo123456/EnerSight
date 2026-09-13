/** 根据整组数据统一选功率单位，避免小站非零功率显示成 0 MW。 */
export function powerChartUnit(values: (number | null)[]) {
  const max = Math.max(0, ...values.filter((v): v is number => v != null && Number.isFinite(v)))
  return max >= 1e6 ? { divisor: 1e6, label: 'GW' }
    : max >= 1000 ? { divisor: 1000, label: 'MW' } : { divisor: 1, label: 'kW' }
}
