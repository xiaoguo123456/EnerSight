import { View, Text } from '@tarojs/components'
import { Icon } from '../Icon'
import './index.scss'

interface Props {
  title: string
  description: string
  onMore?: () => void
}

/** 首页预警条：仅展示当日最高等级一条，无预警时调用方不渲染。docs/03 */
export function AlertBanner({ title, description, onMore }: Props) {
  return (
    <View className="alert-banner" hoverClass="pressed" hoverStayTime={80} onClick={onMore}>
      <View className="alert-banner__head">
        <Icon name="alertTriangle" size={14} color="#f59e0b" fill strokeWidth={2.4} />
        <Text className="alert-banner__label">今日预警</Text>
        <View className="alert-banner__more">
          <Text>查看更多</Text>
          <Icon name="chevronRight" size={12} color="#9ca3af" />
        </View>
      </View>
      <Text className="alert-banner__title">{title}</Text>
      <Text className="alert-banner__desc">{description}</Text>
    </View>
  )
}
