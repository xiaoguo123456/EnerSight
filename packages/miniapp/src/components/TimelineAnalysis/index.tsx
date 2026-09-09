import { View, Text } from '@tarojs/components'
import { Icon, type IconName } from '../Icon'
import './index.scss'

export type PeriodLevel = 'good' | 'warning' | 'risk'

export interface ReportPeriod {
  period: 'morning' | 'afternoon' | 'evening'
  time_range: string
  weather_summary: string
  generation_impact: string
  level: PeriodLevel
}

const META: Record<ReportPeriod['period'], { label: string; icon: IconName }> = {
  morning: { label: '上午', icon: 'sun' },
  afternoon: { label: '下午', icon: 'cloudSun' },
  evening: { label: '晚间', icon: 'moon' },
}

const TONE: Record<PeriodLevel, string> = {
  good: '#1677ff',
  warning: '#f59e0b',
  risk: '#ef4444',
}

/**
 * 今日情况时间轴。三段固定为 06–12 / 12–18 / 18–24，
 * 是面向用户的展示分段，不随日出日落变化。docs/07 §1.7
 */
export function TimelineAnalysis({ periods }: { periods: ReportPeriod[] }) {
  return (
    <View className="timeline">
      {periods.map((p) => (
        <View className="timeline__row" key={p.period}>
          <View className="timeline__time">
            <Text className="timeline__label">{META[p.period].label}</Text>
            <Text className="timeline__range">{p.time_range}</Text>
          </View>
          <View className="timeline__content">
            <View className="timeline__weather-row">
              <Icon name={META[p.period].icon} size={16} color={TONE[p.level]} />
              <Text className="timeline__weather">{p.weather_summary}</Text>
            </View>
            <Text className="timeline__impact">{p.generation_impact}</Text>
          </View>
        </View>
      ))}
    </View>
  )
}
