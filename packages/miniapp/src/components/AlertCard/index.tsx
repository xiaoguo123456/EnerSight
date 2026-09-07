import { View, Text } from '@tarojs/components'
import type { AlertLevel } from '@enersight/core/types'
import { Icon } from '../Icon'
import { RiskBadge } from '../RiskBadge'
import './index.scss'

interface Props {
  level: AlertLevel
  title: string
  description: string
  publishedAt: string
}

const ICON_COLOR: Record<AlertLevel, string> = {
  minor: '#f59e0b',
  moderate: '#f97316',
  severe: '#ef4444',
  cleared: '#16a34a',
}

export function AlertCard({ level, title, description, publishedAt }: Props) {
  return (
    <View className={`alert-card alert-card--${level}`}>
      <View className="alert-card__head">
        <View className={`alert-card__icon alert-card__icon--${level}`}>
          <Icon
            name={level === 'cleared' ? 'checkCircle' : 'alertTriangle'}
            size={18}
            color={ICON_COLOR[level]}
          />
        </View>
        <RiskBadge level={level} />
        <View className="alert-card__spacer" />
        <Text className="alert-card__time">{publishedAt} 发布</Text>
      </View>
      <Text className="alert-card__title">{title}</Text>
      <Text className="alert-card__desc">{description}</Text>
    </View>
  )
}
