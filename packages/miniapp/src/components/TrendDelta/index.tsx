import { View, Text } from '@tarojs/components'
import { formatDelta } from '@enersight/core/format'
import './index.scss'

interface Props {
  /** 较昨日百分比。null 时整个标签不渲染 —— docs/07 §3.3 */
  deltaPercent: number | null
  label?: string
}

export function TrendDelta({ deltaPercent, label = '较昨日' }: Props) {
  const d = formatDelta(deltaPercent)
  if (!d) return null
  return (
    <View className="trend-delta">
      <Text className="trend-delta__label">{label}</Text>
      <Text className={`trend-delta__value trend-delta__value--${d.direction}`}>
        {d.direction === 'up' ? '↑' : '↓'} {d.text}
      </Text>
    </View>
  )
}
