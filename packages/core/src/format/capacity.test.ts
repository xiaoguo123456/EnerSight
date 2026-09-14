import { describe, expect, it } from 'vitest'
import { capacityMwInput, mwToKw } from './index'

describe('装机容量 MW 输入', () => {
  it('kW 回显为 MW，不带浮点尾巴', () => {
    expect(capacityMwInput(50000)).toBe('50')
    expect(capacityMwInput(49500)).toBe('49.5')
    expect(capacityMwInput(500)).toBe('0.5')
    expect(capacityMwInput(1234)).toBe('1.234')
  })
  it('MW 提交为整数 kW', () => {
    expect(mwToKw(0.1)).toBe(100)
    expect(mwToKw(49.5)).toBe(49500)
    expect(mwToKw(1.234)).toBe(1234)
    expect(mwToKw(0.001)).toBe(1)
  })
  it('回显再提交保持原值', () => {
    for (const kw of [1, 500, 49500, 123456]) expect(mwToKw(Number(capacityMwInput(kw)))).toBe(kw)
  })
})
