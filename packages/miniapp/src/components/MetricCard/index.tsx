import { View, Text } from '@tarojs/components'
import type { Formatted } from '@enersight/core/format'
import { Icon, type IconName } from '../Icon'
import { TrendDelta } from '../TrendDelta'
import './index.scss'

interface Props {
  icon: IconName
  iconColor: string
  /** 闭合形状开填充更有分量；wind 这类开放路径不要开 */
  iconFill?: boolean
  label: string
  metric: Formatted
  /** 辅助行二选一：环比 或 说明文案（如「晴转多云」） */
  deltaPercent?: number | null
  caption?: string
}

export function MetricCard({
  icon, iconColor, iconFill = true, label, metric, deltaPercent, caption,
}: Props) {
  return (
    <View className="metric-card">
      <View className="metric-card__head">
        <Icon name={icon} size={16} color={iconColor} fill={iconFill} strokeWidth={2.4} />
        <Text className="metric-card__label">{label}</Text>
      </View>
      <View className="metric-card__value">
        <Text className="metric-card__num">{metric.value}</Text>
        <Text className="metric-card__unit">{metric.unit}</Text>
      </View>
      {caption ? (
        <Text className="metric-card__caption">{caption}</Text>
      ) : (
        <TrendDelta deltaPercent={deltaPercent ?? null} />
      )}
    </View>
  )
}
