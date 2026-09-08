import { View, Text } from '@tarojs/components'
import type { ReactNode } from 'react'
import { getSafeArea } from '@/hooks/useSafeArea'
import './index.scss'

interface Props {
  title: string
  /** 标题右侧的次要信息，如计数、时间 */
  aside?: ReactNode
}

/**
 * Tab 页顶栏：只放页面标题，不放口号。
 * 副标题式的 slogan（「实时监测·精准预警」）不传递任何操作信息，去掉。
 */
export function PageTitleBar({ title, aside }: Props) {
  const safe = getSafeArea()
  return (
    <View
      className="page-title"
      style={{
        paddingTop: `${safe.statusBarHeight + 4}px`,
        paddingRight: `${Math.max(safe.menuGuardRight, 16)}px`,
      }}
    >
      <Text className="page-title__text">{title}</Text>
      {aside && <View className="page-title__aside">{aside}</View>}
    </View>
  )
}
