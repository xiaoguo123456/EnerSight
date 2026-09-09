import { View, Text } from '@tarojs/components'
import { Icon, type IconName } from '../Icon'
import './index.scss'

interface Props {
  distanceKm: number
  direction: string
  directionDetail: string
  impactInMinutes: number
  impactStartTime: string
  referenceStation: string
}

/** 云团短临外推三宫格。字段与计算见 docs/07 §四 */
export function CloudMotionStats({
  distanceKm, direction, directionDetail,
  impactInMinutes, impactStartTime, referenceStation,
}: Props) {
  const cells: { icon: IconName; tone: string; label: string; value: string; unit?: string; sub: string }[] = [
    { icon: 'cloud', tone: '#60a5fa', label: '云团距离',
      value: String(distanceKm), unit: 'km', sub: referenceStation ? '距所选电站' : '参考位置待确认' },
    { icon: 'navigation', tone: '#1677ff', label: '移动方向',
      value: direction, sub: directionDetail },
    { icon: 'clock', tone: '#f59e0b', label: '预计影响时间',
      value: String(impactInMinutes), unit: '分钟', sub: `约 ${impactStartTime} 开始影响` },
  ]

  return (
    <View className="cloud-motion">
      {cells.map((c) => (
        <View className="cloud-motion__cell" key={c.label}>
          <View className="cloud-motion__icon">
            <Icon name={c.icon} size={16} color={c.tone} strokeWidth={1.75} />
          </View>
          <Text className="cloud-motion__label">{c.label}</Text>
          <View className="cloud-motion__value">
            <Text className="cloud-motion__num">{c.value}</Text>
            {c.unit && <Text className="cloud-motion__unit">{c.unit}</Text>}
          </View>
          <Text className="cloud-motion__sub">{c.sub}</Text>
        </View>
      ))}
    </View>
  )
}
