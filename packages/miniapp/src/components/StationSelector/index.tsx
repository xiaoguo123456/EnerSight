import { View, Text } from '@tarojs/components'
import { formatCoordinate } from '@enersight/core/format'
import type { StationStatus } from '@enersight/core/types'
import { StatusBadge } from '../StatusBadge'
import './index.scss'

interface Props {
  name: string
  status: StationStatus
  address: string
  latitude: number
  longitude: number
  onTap?: () => void
}

export function StationSelector({
  name, status, address, latitude, longitude, onTap,
}: Props) {
  return (
    <View className="station-selector" onClick={onTap}>
      <View className="station-selector__thumb">☀️</View>
      <View className="station-selector__body">
        <View className="station-selector__row">
          <Text className="station-selector__name">{name}</Text>
          <Text className="station-selector__caret">⌄</Text>
          <StatusBadge status={status} />
        </View>
        <Text className="station-selector__addr">
          📍 {address}（{formatCoordinate(latitude, longitude)}）
        </Text>
      </View>
      <Text className="station-selector__arrow">›</Text>
    </View>
  )
}
