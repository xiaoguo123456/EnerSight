import { View, Text } from '@tarojs/components'
import './index.scss'

interface Props {
  title: string
  description: string
  onMore?: () => void
}

/** 首页预警条：仅展示当日最高等级一条，无预警时调用方不渲染。docs/03 */
export function AlertBanner({ title, description, onMore }: Props) {
  return (
    <View className="alert-banner">
      <View className="alert-banner__head">
        <Text className="alert-banner__icon">⚠️</Text>
        <Text className="alert-banner__label">今日预警</Text>
        <Text className="alert-banner__more" onClick={onMore}>查看更多 ›</Text>
      </View>
      <Text className="alert-banner__title">{title}</Text>
      <Text className="alert-banner__desc">{description}</Text>
    </View>
  )
}
