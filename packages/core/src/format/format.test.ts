import { describe, expect, it, vi } from 'vitest'
import {
  formatBeijingTime, isDataStale, formatCo2, formatCoordinate, formatCurrency, formatDelta, formatEnergy,
  formatHours, formatPercent, formatPower, formatRadiation,
  formatTemperature, formatWindSpeed, thousands,
} from './index'

describe('thousands', () => {
  it('≥1000 加分隔符', () => {
    expect(thousands(999)).toBe('999')
    expect(thousands(1000)).toBe('1,000')
    expect(thousands(1008)).toBe('1,008')
  })
})

describe('formatPower', () => {
  it('<1000 保持 kW', () => {
    expect(formatPower(320)).toEqual({ value: '320', unit: 'kW' })
  })
  it('≥1000 进位 MW', () => {
    expect(formatPower(1800)).toEqual({ value: '1.8', unit: 'MW' })
    expect(formatPower(2000)).toEqual({ value: '2.0', unit: 'MW' })
  })
  it('边界值', () => {
    expect(formatPower(999)).toEqual({ value: '999', unit: 'kW' })
    expect(formatPower(1000)).toEqual({ value: '1.0', unit: 'MW' })
  })
})

describe('formatEnergy', () => {
  it('小于 1 GWh 保持 kWh，不经过 MWh', () => {
    expect(formatEnergy(2400)).toEqual({ value: '2,400', unit: 'kWh' })
    expect(formatEnergy(9600)).toEqual({ value: '9,600', unit: 'kWh' })
    expect(formatEnergy(50_000)).toEqual({ value: '50,000', unit: 'kWh' })
  })
  it('≥1 GWh 进位', () => {
    expect(formatEnergy(1_260_000)).toEqual({ value: '1.26', unit: 'GWh' })
    expect(formatEnergy(12_800_000)).toEqual({ value: '12.80', unit: 'GWh' })
  })
})

describe('formatCo2', () => {
  it('API 传 kg，展示吨', () => {
    expect(formatCo2(4800)).toEqual({ value: '4.8', unit: '吨' })
    expect(formatCo2(1_008_000)).toEqual({ value: '1,008', unit: '吨' })
  })
})

describe('标量格式化', () => {
  it('温度取整', () => {
    expect(formatTemperature(32.4)).toEqual({ value: '32', unit: '℃' })
  })
  it('风速 1 位小数', () => {
    expect(formatWindSpeed(4.5)).toEqual({ value: '4.5', unit: 'm/s' })
    expect(formatWindSpeed(4)).toEqual({ value: '4.0', unit: 'm/s' })
  })
  it('辐射取整', () => {
    expect(formatRadiation(620.3)).toEqual({ value: '620', unit: 'W/m²' })
  })
  it('百分比取整', () => {
    expect(formatPercent(35.2)).toEqual({ value: '35', unit: '%' })
  })
  it('等效小时 1 位小数', () => {
    expect(formatHours(4.5)).toEqual({ value: '4.5', unit: 'h' })
  })
  it('金额取整', () => {
    expect(formatCurrency(2528)).toEqual({ value: '2,528', unit: '元' })
  })
})

describe('formatCoordinate', () => {
  it('2 位小数带方位', () => {
    expect(formatCoordinate(31.3, 120.62)).toBe('31.30°N，120.62°E')
  })
  it('南半球西半球', () => {
    expect(formatCoordinate(-33.87, -70.67)).toBe('33.87°S，70.67°W')
  })
})

describe('formatDelta', () => {
  it('上升为 up，下降为 down —— 表示方向不是好坏', () => {
    expect(formatDelta(12)).toEqual({ text: '12%', direction: 'up' })
    expect(formatDelta(-8)).toEqual({ text: '8%', direction: 'down' })
  })
  it('null 时返回 null，调用方隐藏整个标签', () => {
    expect(formatDelta(null)).toBeNull()
  })
  it('0% 不展示', () => {
    expect(formatDelta(0)).toBeNull()
    expect(formatDelta(0.2)).toBeNull()
  })
  it('非有限值不展示', () => {
    expect(formatDelta(Infinity)).toBeNull()
    expect(formatDelta(NaN)).toBeNull()
  })
})

describe('缺测与微小值', () => {
  it('缺测不伪装成零，真实零值保留', () => {
    for (const format of [formatPower, formatEnergy, formatCo2, formatCurrency, formatTemperature, formatWindSpeed, formatRadiation, formatPercent, formatHours]) {
      expect(format(null).value).toBe('—')
      expect(format(undefined).value).toBe('—')
      expect(format(NaN).value).toBe('—')
      expect(format(0).value).not.toBe('—')
    }
  })
  it('少量等效小时不舍入成零', () => {
    expect(formatHours(0.015).value).toBe('<0.1')
    expect(formatHours(0).value).toBe('0.0')
  })
})


describe('数据时间与时效', () => {
  it('同一时刻的 UTC 和当地时间统一为北京时间', () => {
    expect(formatBeijingTime('2026-09-09T05:00:00Z')).toBe('09-09 13:00')
    expect(formatBeijingTime('2026-09-09T13:00:00+08:00')).toBe('09-09 13:00')
    expect(formatBeijingTime('2026-09-09T00:00:00-05:00')).toBe('09-09 13:00')
  })
  it('无时区或错误时间不能伪装成当前时间', () => {
    expect(formatBeijingTime('2026-09-09T05:00:00')).toBe('暂无时间')
    expect(formatBeijingTime(null)).toBe('暂无时间')
    expect(formatBeijingTime('错误')).toBe('暂无时间')
  })
  it('按实际时间判断过期而非显示字符串', () => {
    const now = Date.parse('2026-09-09T06:00:00Z')
    expect(isDataStale('2026-09-09T05:00:00Z', 30, now)).toBe(true)
    expect(isDataStale('2026-09-09T13:45:00+08:00', 30, now)).toBe(false)
  })
})

// 模拟部分真机忽略本地化选项，防止浮点尾数直接进入界面。
describe('真机数字格式兼容', () => {
  it('不依赖 toLocaleString，统一保留指定小数位', () => {
    const locale = vi.spyOn(Number.prototype, 'toLocaleString').mockImplementation(function (this: number) { return String(this) })
    try {
      expect(thousands(5534.783027370007, 1)).toBe('5,534.8')
      expect(thousands(2768.20850272, 1)).toBe('2,768.2')
      expect(thousands(2766.57452465, 1)).toBe('2,766.6')
      expect(thousands(18661)).toBe('18,661')
      expect(thousands(0, 1)).toBe('0.0')
      expect(thousands(-1234.56, 1)).toBe('-1,234.6')
      expect(formatWindSpeed(2.45).value).toBe('2.5')
      expect(locale).not.toHaveBeenCalled()
    } finally { locale.mockRestore() }
  })
})
