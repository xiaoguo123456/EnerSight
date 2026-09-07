import { View, Text } from '@tarojs/components'
import { formatDelta } from '@enersight/core/format'
import { Icon } from '../Icon'
import './index.scss'

interface Props {
  /** 较昨日百分比。null 时整个标签不渲染 —— docs/07 §3.3 */
  deltaPercent: number | null
  label?: string
}

// 上升红、下降绿：表示变化方向不是好坏，不要按涨跌习惯反转。docs/02 §二
const COLOR = { up: '#ef4444', down: '#16a34a' } as const

export function TrendDelta({ deltaPercent, label = '较昨日' }: Props) {
  const d = formatDelta(deltaPercent)
  if (!d) return null
  return (
    <View className="trend-delta">
      <Text className="trend-delta__label">{label}</Text>
      <Icon
        name={d.direction === 'up' ? 'arrowUp' : 'arrowDown'}
        size={10}
        strokeWidth={3}
        color={COLOR[d.direction]}
      />
      <Text className={`trend-delta__value trend-delta__value--${d.direction}`}>
        {d.text}
      </Text>
    </View>
  )
}
