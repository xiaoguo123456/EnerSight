import { View, Text } from '@tarojs/components'
import type { StationStatus } from '@enersight/core/types'
import { getSafeArea } from '@/hooks/useSafeArea'
import { Icon } from '../Icon'
import { StatusBadge } from '../StatusBadge'
import './index.scss'

interface Props {
  name: string
  status: StationStatus
  address: string
  onSwitch?: () => void
}

/**
 * 首页顶栏：站点名就是页面标题。
 *
 * 不放品牌名 —— 用户已经在 app 里了，不需要反复被告知产品叫什么。
 * 这块黄金位置给用户真正关心的：我在看哪个站。
 */
export function StationTitleBar({ name, status, address, onSwitch }: Props) {
  const safe = getSafeArea()
  return (
    <View
      className="station-title"
      style={{
        paddingTop: `${safe.statusBarHeight + 4}px`,
        paddingRight: `${Math.max(safe.menuGuardRight, 16)}px`,
      }}
    >
      <View className="station-title__row" hoverClass="pressed" hoverStayTime={80} onClick={onSwitch}>
        <Text className="station-title__name">{name}</Text>
        <Icon name="chevronDown" size={16} color="#6b7280" strokeWidth={2.5} />
      </View>
      <View className="station-title__sub">
        <StatusBadge status={status} />
        <Text className="station-title__addr">{address}</Text>
      </View>
    </View>
  )
}
