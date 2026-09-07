import { View, Text } from '@tarojs/components'
import type { StationStatus } from '@enersight/core/types'
import './index.scss'

const LABEL: Record<StationStatus, string> = {
  normal: '运行正常',
  standby: '设备待机',
  fault: '设备异常',
}

export function StatusBadge({ status }: { status: StationStatus }) {
  return (
    <View className={`status-badge status-badge--${status}`}>
      <View className="status-badge__dot" />
      <Text>{LABEL[status]}</Text>
    </View>
  )
}
