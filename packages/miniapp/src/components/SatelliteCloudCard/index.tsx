import { View, Text } from '@tarojs/components'
import { Icon } from '../Icon'
import './index.scss'

interface Props {
  observedAt: string
  onFullscreen?: () => void
}

/**
 * 卫星云图卡。observed_at 必须展示 —— 它是数据观测时间，不等于当前时间。
 * docs/04 §四图层通用规则
 */
export function SatelliteCloudCard({ observedAt, onFullscreen }: Props) {
  return (
    <View className="sat-card">
      <View className="sat-card__canvas">
        {/* 云图瓦片由服务端重投影后下发，见 docs/05 §6.3。此处为占位 */}
        <Text className="sat-card__placeholder">卫星云图</Text>
      </View>

      <View className="sat-card__stamp">
        <Icon name="satellite" size={12} color="#ffffff" />
        <View className="sat-card__stamp-text">
          <Text className="sat-card__stamp-title">卫星云图</Text>
          <Text className="sat-card__stamp-time">{observedAt}</Text>
        </View>
      </View>

      <View className="sat-card__full" onClick={onFullscreen}>
        <Icon name="maximize" size={14} color="#ffffff" />
      </View>

      <View className="sat-card__legend">
        <Text className="sat-card__legend-title">云量强度</Text>
        <View className="sat-card__legend-bar" />
        <View className="sat-card__legend-scale">
          <Text>低</Text>
          <Text>高</Text>
        </View>
      </View>
    </View>
  )
}
