import { View, Text } from '@tarojs/components'
import type { StationStatus } from '@enersight/core/types'
import './index.scss'

const LABEL: Record<StationStatus, string> = {
  normal: '公开资料：已投运',
  standby: '状态待确认',
  fault: '状态待核实',
}

/** 目录电站按公开资料表述；自建场站没有公开资料，标「自建场站」。docs/17 §一 */
export function StatusBadge({ status, own = false }: { status: StationStatus; own?: boolean }) {
  return (
    <View className={`status-badge status-badge--${status}`}>
      <View className="status-badge__dot" />
      <Text>{own && status === 'normal' ? '自建场站' : LABEL[status]}</Text>
    </View>
  )
}
