import { View, Text } from '@tarojs/components'
import { Icon } from '../Icon'
import './index.scss'

interface Props {
  title: string
  subtitle?: string
  /** 右侧手写体品牌装饰，仅装饰不承载信息。docs/02 §三 */
  slogan?: [string, string]
}

export function AppHeader({ title, subtitle, slogan }: Props) {
  return (
    <View className="app-header">
      <View className="app-header__brand">
        <Icon name="leaf" size={26} color="#16a34a" strokeWidth={1.8} />
        <View className="app-header__text">
          <Text className="app-header__title">{title}</Text>
          {subtitle && <Text className="app-header__subtitle">{subtitle}</Text>}
        </View>
      </View>
      {slogan && (
        <View className="app-header__slogan">
          <Text>{slogan[0]}</Text>
          <Text>{slogan[1]}</Text>
        </View>
      )}
    </View>
  )
}
