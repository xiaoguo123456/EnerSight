import { describe, expect, it } from 'vitest'
import { powerChartUnit } from './power-chart'

describe('功率曲线单位', () => {
  it('户用小站非零功率不因单位放大而显示为零', () => {
    const unit = powerChartUnit([null, 0, 3])
    expect(unit.label).toBe('kW')
    expect(Number((3 / unit.divisor).toFixed(2))).toBe(3)
  })
  it('整组曲线使用同一单位并正确跨越千进位', () => {
    expect(powerChartUnit([999, 1000])).toEqual({ divisor: 1000, label: 'MW' })
    expect(powerChartUnit([1e6])).toEqual({ divisor: 1e6, label: 'GW' })
    expect(powerChartUnit([null, 0]).label).toBe('kW')
  })
})
