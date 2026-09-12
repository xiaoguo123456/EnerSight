import { View, Text } from '@tarojs/components'
import type { IndexLevel } from '@enersight/core/types'
import './index.scss'

export interface StripDay {
  date: string
  weekday: number
  lead_days: number
  energy_kwh: number | null
  /** 一行短说明：天气，或光伏 / 风电占比 */
  caption?: string | null
  level?: IndexLevel | null
  score?: number | null
}

const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日']
const LEVEL_COLOR: Record<IndexLevel, string> = { excellent: '#16a34a', good: '#1677ff', fair: '#f59e0b', poor: '#ef4444' }

export function dayLabel(d: { date: string; weekday: number; lead_days: number }) {
  return d.lead_days === 0 ? '今天' : d.lead_days === 1 ? '明天' : `周${WEEKDAYS[d.weekday - 1]}`
}

/** 按最大值选一个共同单位，7 列共用，单位写在表头。 */
export function energyUnit(maxKwh: number) {
  if (maxKwh >= 1e6) return { divisor: 1e6, label: 'GWh' }
  if (maxKwh >= 1000) return { divisor: 1000, label: 'MWh' }
  return { divisor: 1, label: 'kWh' }
}

/**
 * 未来 7 天横向条：周几 / 日期 / 说明 / 电量竖条 / 数值 / 适宜度。
 * 第 5 天起为中期预报（1 小时曲线），用浅色竖条与虚线分隔表示，不在列里写字；前 4 天为 15 分钟曲线。
 */
export function OutlookStrip({ days, selected, onSelect }: { days: StripDay[]; selected: number; onSelect: (i: number) => void }) {
  const max = Math.max(...days.map((d) => d.energy_kwh ?? 0), 0)
  const unit = energyUnit(max)
  const decimals = max / unit.divisor < 10 ? 1 : 0
  return (
    <View className="strip">
      <View className="strip__unit"><Text>日电量 {unit.label}</Text></View>
      <View className="strip__cols">
        {days.map((d, i) => (
          <View
            key={d.date}
            className={`strip__col ${selected === i ? 'strip__col--active' : ''} ${d.lead_days >= 4 ? 'strip__col--mid' : ''} ${d.lead_days === 4 ? 'strip__col--mid-first' : ''}`}
            hoverClass="pressed"
            onClick={() => onSelect(i)}
          >
            <Text className="strip__day">{dayLabel(d)}</Text>
            <Text className="strip__date">{d.date.slice(5).replace('-', '/')}</Text>
            <Text className="strip__caption">{d.caption ?? ' '}</Text>
            <View className="strip__bar-wrap">
              <View className="strip__bar" style={{ height: `${d.energy_kwh == null || max <= 0 ? 0 : Math.max(4, d.energy_kwh / max * 100)}%` }} />
            </View>
            <Text className="strip__value">{d.energy_kwh == null ? '—' : (d.energy_kwh / unit.divisor).toFixed(decimals)}</Text>
            <View className="strip__level">
              {d.level && <View className="strip__dot" style={{ background: LEVEL_COLOR[d.level] }} />}
              <Text>{d.level && d.score != null ? Math.round(d.score) : ' '}</Text>
            </View>
          </View>
        ))}
      </View>
    </View>
  )
}
