import { describe, expect, it } from 'vitest'
import { csvLocalTime, powerCsv, toCsv, weatherCsv } from './index'

const lines = (csv: string) => csv.replace(/^\uFEFF/, '').trimEnd().split('\r\n')

describe('toCsv', () => {
  it('带 BOM、CRLF，缺测留空不写 0', () => {
    const csv = toCsv(['a', 'b'], [[1, null], [undefined, Number.NaN]])
    expect(csv.charCodeAt(0)).toBe(0xfeff)
    expect(lines(csv)).toEqual(['a,b', '1,', ','])
  })
  it('逗号、引号、换行按 RFC 4180 转义', () => {
    expect(lines(toCsv(['名称'], [['苏州,一期'], ['"A"站'], ['第一行\n第二行']]))).toEqual([
      '名称', '"苏州,一期"', '"""A""站"', '"第一行\n第二行"',
    ])
  })
  it('数值去掉浮点噪声，保留三位小数', () => {
    expect(lines(toCsv(['v'], [[0.1 + 0.2], [1234.56789]]))).toEqual(['v', '0.3', '1234.568'])
  })
})

describe('csvLocalTime', () => {
  it('保留电站当地时间，不换算时区', () => {
    expect(csvLocalTime('2026-09-14T06:15:00+08:00')).toBe('2026-09-14 06:15')
    expect(csvLocalTime('2026-09-14T06:15:00+05:00')).toBe('2026-09-14 06:15')
  })
})

describe('powerCsv', () => {
  it('逐日拼接，有上网口径时多一列且按时刻对齐', () => {
    const csv = powerCsv([
      { power_kw: [{ time: '2026-09-14T00:00:00+08:00', value: 10 }, { time: '2026-09-14T00:15:00+08:00', value: null }], grid_power_kw: [{ time: '2026-09-14T00:15:00+08:00', value: 5 }] },
      { power_kw: [{ time: '2026-09-15T00:00:00+08:00', value: 12.5 }], grid_power_kw: null },
    ])
    expect(lines(csv)).toEqual([
      '时间（电站当地时间）,可发功率(kW),预计上网功率(kW)',
      '2026-09-14 00:00,10,',
      '2026-09-14 00:15,,5',
      '2026-09-15 00:00,12.5,',
    ])
  })
  it('没有上网口径时只有一列功率，可改列名', () => {
    expect(lines(powerCsv([{ power_kw: [{ time: '2026-09-14T00:00:00+08:00', value: 1 }] }], '预测功率合计'))[0])
      .toBe('时间（电站当地时间）,预测功率合计(kW)')
  })
})

describe('weatherCsv', () => {
  it('多个指标按时刻对齐，缺的时刻留空', () => {
    const csv = weatherCsv([
      { label: '辐射', unit: 'W/m²', points: [{ time: '2026-09-14T06:00:00+08:00', value: 120 }, { time: '2026-09-14T06:15:00+08:00', value: 150 }] },
      { label: '云量', unit: '%', points: [{ time: '2026-09-14T06:15:00+08:00', value: 40 }] },
    ])
    expect(lines(csv)).toEqual([
      '时间（电站当地时间）,辐射(W/m²),云量(%)',
      '2026-09-14 06:00,120,',
      '2026-09-14 06:15,150,40',
    ])
  })
})
