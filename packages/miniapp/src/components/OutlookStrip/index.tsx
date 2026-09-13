import { Button, View, Text } from '@tarojs/components'
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
 * 七列只放日期和电量，天气及适宜度展示在选中日下方。
 * 第 5 天起用浅色竖条与虚线分隔，并在表头注明参考属性。
 */
export function OutlookStrip({ days, selected, onSelect }: { days: StripDay[]; selected: number; onSelect: (i: number) => void }) {
  const max = Math.max(...days.map((d) => d.energy_kwh ?? 0), 0)
  const unit = energyUnit(max)
  const decimals = max / unit.divisor < 10 ? 1 : 0
  const hasEnergy = days.some(d => d.energy_kwh != null)
  const active = days[selected]
  const detail = active ? [active.caption, active.score != null ? `发电适宜度 ${Math.round(active.score)} 分` : null].filter(Boolean).join(' · ') : ''
  return (
    <View className="strip">
      <View className="strip__unit"><Text>{hasEnergy ? `日电量 ${unit.label}` : "未来天气与发电适宜度"}</Text><Text>后 3 天参考</Text></View>
      <View className="strip__cols">
        {days.map((d, i) => (
          <Button
            ariaLabel={`${dayLabel(d)} ${d.date}，${d.energy_kwh == null ? "电量暂缺" : `${(d.energy_kwh / unit.divisor).toFixed(decimals)} ${unit.label}`}，${selected === i ? "已选中" : "点击查看"}`}
            key={d.date}
            className={`strip__col ${selected === i ? 'strip__col--active' : ''} ${d.lead_days >= 4 ? 'strip__col--mid' : ''} ${d.lead_days === 4 ? 'strip__col--mid-first' : ''}`}
            hoverClass="pressed"
            onClick={() => onSelect(i)}
          >
            <Text className="strip__day">{dayLabel(d)}</Text>
            <Text className="strip__date">{`${Number(d.date.slice(5, 7))}/${d.date.slice(8)}`}</Text>
            {hasEnergy && <View className="strip__bar-wrap">
              <View className="strip__bar" style={{ height: `${d.energy_kwh == null || max <= 0 ? 0 : Math.max(4, d.energy_kwh / max * 100)}%` }} />
            </View>}
            <Text className="strip__value">{d.energy_kwh == null ? '—' : (d.energy_kwh / unit.divisor).toFixed(decimals)}</Text>
          </Button>
        ))}
      </View>
      {!!detail && <Text className="strip__detail">{detail}</Text>}
    </View>
  )
}
