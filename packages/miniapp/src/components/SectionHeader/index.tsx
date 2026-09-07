import { View, Text } from '@tarojs/components'
import './index.scss'

interface Props {
  icon: string
  title: string
  action?: string
  onAction?: () => void
}

export function SectionHeader({ icon, title, action, onAction }: Props) {
  return (
    <View className="section-header">
      <View className="section-header__left">
        <Text className="section-header__icon">{icon}</Text>
        <Text className="section-header__title">{title}</Text>
      </View>
      {action && (
        <Text className="section-header__action" onClick={onAction}>
          {action} ›
        </Text>
      )}
    </View>
  )
}
