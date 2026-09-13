import Taro from '@tarojs/taro'
import { View } from '@tarojs/components'
import { Icon } from '../Icon'
import './index.scss'

interface Props {
  title: string
  content: string
  size?: number
  color?: string
}

/**
 * ⓘ 说明入口：界面只留结论与数字，必要的口径说明收进弹窗。
 * 视觉 14px，点击区 32px；阻止冒泡，放在可点卡片里不会误触卡片。
 */
export function InfoTip({ title, content, size = 14, color = '#64748b' }: Props) {
  return (
    <View
      className="info-tip"
      role="button"
      aria-label={title}
      onClick={(e) => { e.stopPropagation(); void Taro.showModal({ title, content, showCancel: false, confirmText: '知道了' }) }}
    >
      <Icon name="info" size={size} color={color} strokeWidth={1.75} />
    </View>
  )
}
