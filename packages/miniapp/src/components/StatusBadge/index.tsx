import { View, Text } from '@tarojs/components'
import type { StationStatus } from '@enersight/core/types'
import './index.scss'

const LABEL: Record<StationStatus, string> = {
  normal: '目录运营中',
  standby: '状态待确认',
  fault: '状态待核实',
}

export function StatusBadge({ status }: { status: StationStatus }) {
  return (
    <View className={`status-badge status-badge--${status}`}>
      <View className="status-badge__dot" />
      <Text>{LABEL[status]}</Text>
    </View>
  )
}
