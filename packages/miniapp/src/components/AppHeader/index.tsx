import { View, Text } from '@tarojs/components'
import { getSafeArea } from '@/hooks/useSafeArea'
import { Icon } from '../Icon'
import './index.scss'

interface Props {
  title: string
  subtitle?: string
  /** 右侧手写体品牌装饰，仅装饰不承载信息。docs/02 §三 */
  slogan?: [string, string]
}

export function AppHeader({ title, subtitle, slogan }: Props) {
  // navigationStyle: 'custom' 下必须自己避开状态栏与右上角胶囊按钮
  const safe = getSafeArea()

  return (
    <View
      className="app-header"
      style={{
        paddingTop: `${safe.statusBarHeight + 6}px`,
        paddingRight: `${Math.max(safe.menuGuardRight, 16)}px`,
      }}
    >
      <View className="app-header__brand">
        <Icon name="leaf" size={26} color="#16a34a" fill strokeWidth={2} />
        <View className="app-header__text">
          <Text className="app-header__title">{title}</Text>
          {subtitle && <Text className="app-header__subtitle">{subtitle}</Text>}
        </View>
      </View>
      {/* 胶囊按钮占住右上角，slogan 只能放在标题下方一行的右侧 */}
      {slogan && (
        <View className="app-header__slogan">
          <Text>{slogan[0]}</Text>
          <Text>{slogan[1]}</Text>
        </View>
      )}
    </View>
  )
}
