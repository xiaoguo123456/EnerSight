import { View, Text } from '@tarojs/components'
import {
  formatCo2, formatCoordinate, formatEnergy, formatPower,
} from '@enersight/core/format'
import type { StationStatus, StationType } from '@enersight/core/types'
import { Icon } from '../Icon'
import { StatusBadge } from '../StatusBadge'
import './index.scss'

interface Props {
  name: string
  type: StationType
  status: StationStatus
  capacity: number
  address: string
  latitude: number
  longitude: number
  metrics: {
    daily_generation: number | null
    current_power: number | null
    total_generation: number | null
    co2_reduction: number | null
  }
  onTap?: () => void
  onMore?: () => void
}

const TYPE_ICON = { solar: 'sun', wind: 'wind' } as const
const TYPE_TONE = { solar: '#f59e0b', wind: '#1677ff' } as const

export function StationCard({
  name, type, status, capacity, address, latitude, longitude, metrics,
  onTap, onMore,
}: Props) {
  const cap = formatPower(capacity)
  // 光伏与风电共用一套指标。储能 V1 不实现，见 docs/01 §六
  // 今日发电是主指标（primary），其余三项次级
  const cells = [
    { icon: 'zap' as const, label: '今日发电', primary: true,
      f: metrics.daily_generation === null ? null : formatEnergy(metrics.daily_generation) },
    { icon: 'barChart' as const, label: '实时功率', primary: false,
      f: metrics.current_power === null ? null : formatPower(metrics.current_power) },
    { icon: 'trendingUp' as const, label: '累计发电', primary: false,
      f: metrics.total_generation === null ? null : formatEnergy(metrics.total_generation) },
    { icon: 'leaf' as const, label: '减排量', primary: false,
      f: metrics.co2_reduction === null ? null : formatCo2(metrics.co2_reduction) },
  ]

  return (
    <View className="station-card" hoverClass="pressed" hoverStayTime={80} onClick={onTap}>
      <View className="station-card__head">
        <View className={`station-card__thumb station-card__thumb--${type}`}>
          <Icon name={TYPE_ICON[type]} size={20} color="#ffffff" />
        </View>
        <View className="station-card__info">
          <View className="station-card__row">
            <Text className="station-card__name">{name}</Text>
            <Icon name="chevronRight" size={13} color="#9ca3af" />
            <View className="station-card__spacer" />
            <StatusBadge status={status} />
            <View className="station-card__more" onClick={onMore}>
              <Icon name="moreHorizontal" size={14} color="#9ca3af" />
            </View>
          </View>
          <Text className="station-card__cap">
            {cap.value} {cap.unit}
          </Text>
          <View className="station-card__addr">
            <Icon name="mapPin" size={11} color="#9ca3af" />
            <Text className="station-card__addr-text">
              {address}（{formatCoordinate(latitude, longitude)}）
            </Text>
          </View>
        </View>
      </View>

      <View className="station-card__grid">
        {cells.map((c) => (
          <View
            className={`station-card__cell ${c.primary ? 'station-card__cell--primary' : ''}`}
            key={c.label}
          >
            <View className="station-card__cell-head">
              <Icon name={c.icon} size={12} color={c.primary ? TYPE_TONE[type] : '#94a3b8'} fill={c.primary} />
              <Text className="station-card__cell-label">{c.label}</Text>
            </View>
            {c.f ? (
              <View className="station-card__cell-value">
                <Text className="station-card__cell-num">{c.f.value}</Text>
                <Text className="station-card__cell-unit">{c.f.unit}</Text>
              </View>
            ) : (
              <Text className="station-card__cell-empty">—</Text>
            )}
          </View>
        ))}
      </View>
    </View>
  )
}
