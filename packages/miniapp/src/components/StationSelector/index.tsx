import { View, Text } from '@tarojs/components'
import { formatCoordinate } from '@enersight/core/format'
import type { StationStatus } from '@enersight/core/types'
import { Icon } from '../Icon'
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
      <View className="station-selector__thumb">
        <Icon name="sun" size={22} color="#ffffff" />
      </View>
      <View className="station-selector__body">
        <View className="station-selector__row">
          <Text className="station-selector__name">{name}</Text>
          <Icon name="chevronDown" size={12} color="#9ca3af" />
          <StatusBadge status={status} />
        </View>
        <View className="station-selector__addr">
          <Icon name="mapPin" size={11} color="#9ca3af" />
          <Text className="station-selector__addr-text">
            {address}（{formatCoordinate(latitude, longitude)}）
          </Text>
        </View>
      </View>
      <Icon name="chevronRight" size={16} color="#9ca3af" />
    </View>
  )
}
