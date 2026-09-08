import { View, Text } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { getSafeArea } from '@/hooks/useSafeArea'
import { Icon } from '../Icon'
import './index.scss'

interface Props {
  title: string
  subtitle?: string
}

/** 二级页面顶部。底部导航保留，父级 Tab 维持激活态。docs/03 */
export function PageHeader({ title, subtitle }: Props) {
  const safe = getSafeArea()

  return (
    <View
      className="page-header"
      style={{
        paddingTop: `${safe.statusBarHeight + 6}px`,
        paddingRight: `${Math.max(safe.menuGuardRight, 16)}px`,
      }}
    >
      <View className="page-header__back" onClick={() => Taro.navigateBack()}>
        <Icon name="chevronLeft" size={20} color="#1f2937" />
      </View>
      <View className="page-header__text">
        <Text className="page-header__title">{title}</Text>
        {subtitle && <Text className="page-header__subtitle">{subtitle}</Text>}
      </View>
    </View>
  )
}
