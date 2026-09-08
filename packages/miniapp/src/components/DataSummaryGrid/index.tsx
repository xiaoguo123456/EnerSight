import { View, Text } from '@tarojs/components'
import type { Formatted } from '@enersight/core/format'
import { Icon, type IconName } from '../Icon'
import { TrendDelta } from '../TrendDelta'
import './index.scss'

export interface SummaryCell {
  icon: IconName
  tone: 'energy' | 'primary' | 'purple' | 'warning'
  label: string
  metric: Formatted
  deltaPercent: number | null
}

const TONE_COLOR = {
  energy: '#16a34a',
  primary: '#1677ff',
  purple: '#8b5cf6',
  warning: '#f59e0b',
} as const

export function DataSummaryGrid({ cells }: { cells: SummaryCell[] }) {
  return (
    <View className="summary-grid">
      {cells.map((c) => (
        <View className="summary-grid__cell" key={c.label}>
          <View className={`summary-grid__icon summary-grid__icon--${c.tone}`}>
            <Icon name={c.icon} size={15} color={TONE_COLOR[c.tone]} fill strokeWidth={2.4} />
          </View>
          <Text className="summary-grid__label">{c.label}</Text>
          <View className="summary-grid__value">
            <Text className="summary-grid__num">{c.metric.value}</Text>
            <Text className="summary-grid__unit">{c.metric.unit}</Text>
          </View>
          <TrendDelta deltaPercent={c.deltaPercent} />
        </View>
      ))}
    </View>
  )
}
