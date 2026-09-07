import { View, Text } from '@tarojs/components'
import type { AlertLevel } from '@enersight/core/types'
import './index.scss'

const LABEL: Record<AlertLevel, string> = {
  minor: '轻度风险',
  moderate: '中度风险',
  severe: '重度风险',
  cleared: '解除预警',
}

export function RiskBadge({ level }: { level: AlertLevel }) {
  return (
    <View className={`risk-badge risk-badge--${level}`}>
      <Text>{LABEL[level]}</Text>
    </View>
  )
}
