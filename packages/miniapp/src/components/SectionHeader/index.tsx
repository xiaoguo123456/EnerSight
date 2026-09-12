import { View, Text } from '@tarojs/components'
import { Icon, type IconName } from '../Icon'
import { InfoTip } from '../InfoTip'
import './index.scss'

interface Props {
  icon: IconName
  iconColor?: string
  title: string
  /** 口径说明，收进 ⓘ 弹窗，不在界面上铺小字 */
  info?: { title: string; content: string }
  action?: string
  onAction?: () => void
}

export function SectionHeader({
  icon, iconColor = '#64748b', title, info, action, onAction,
}: Props) {
  return (
    <View className="section-header">
      <View className="section-header__left">
        <Icon name={icon} size={15} color={iconColor} strokeWidth={1.75} />
        <Text className="section-header__title">{title}</Text>
        {info && <InfoTip title={info.title} content={info.content} />}
      </View>
      {action && onAction && (
        <View className="section-header__action" onClick={onAction}>
          <Text>{action}</Text>
          <Icon name="chevronRight" size={12} color="#9ca3af" />
        </View>
      )}
    </View>
  )
}
