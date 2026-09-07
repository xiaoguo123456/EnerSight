import { View, Text } from '@tarojs/components'
import { Icon, type IconName } from '../Icon'
import './index.scss'

interface Props {
  icon: IconName
  iconColor?: string
  title: string
  action?: string
  onAction?: () => void
}

export function SectionHeader({
  icon, iconColor = '#1677ff', title, action, onAction,
}: Props) {
  return (
    <View className="section-header">
      <View className="section-header__left">
        <Icon name={icon} size={16} color={iconColor} />
        <Text className="section-header__title">{title}</Text>
      </View>
      {action && (
        <View className="section-header__action" onClick={onAction}>
          <Text>{action}</Text>
          <Icon name="chevronRight" size={12} color="#9ca3af" />
        </View>
      )}
    </View>
  )
}
