import { View } from '@tarojs/components'
import type { ReactNode } from 'react'
import './index.scss'

/** 四宫格容器：等宽四栏 + 竖线分隔，固定四项不换行不滚动。docs/03 */
export function MetricGrid({ children }: { children: ReactNode }) {
  return <View className="metric-grid">{children}</View>
}
