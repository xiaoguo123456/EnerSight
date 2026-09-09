import { View, Text } from '@tarojs/components'
import type { AlertLevel } from '@enersight/core/types'
import { Icon } from '../Icon'
import { RiskBadge } from '../RiskBadge'
import './index.scss'

export interface AlertRecord {
  id: string
  level: AlertLevel
  title: string
  description: string
  published_at: string
}

const ICON_COLOR: Record<AlertLevel, string> = {
  minor: '#f59e0b',
  moderate: '#f97316',
  severe: '#ef4444',
  cleared: '#16a34a',
}

export function AlertRecordList({
  records, onTap,
}: { records: AlertRecord[]; onTap?: (id: string) => void }) {
  return (
    <View className="alert-records">
      {records.map((r) => (
        <View className="alert-records__item" key={r.id} onClick={() => onTap?.(r.id)}>
          <View className={`alert-records__icon alert-records__icon--${r.level}`}>
            <Icon
              name={r.level === 'cleared' ? 'checkCircle' : 'alertTriangle'}
              size={15}
              color={ICON_COLOR[r.level]}
            />
          </View>
          <View className="alert-records__body">
            <View className="alert-records__row">
              <Text className="alert-records__title">{r.title}</Text>
            </View>
            <View className="alert-records__meta">
              <RiskBadge level={r.level} />
              <Text className="alert-records__time">{r.published_at}</Text>
            </View>
            <Text className="alert-records__desc">{r.description}</Text>
          </View>
          {onTap && <Icon name="chevronRight" size={13} color="#9ca3af" />}
        </View>
      ))}
    </View>
  )
}
