import { View, Text } from '@tarojs/components'
import { Icon } from '../Icon'
import './index.scss'

interface Props {
  text: string
  onTap?: () => void
}

/** 悬浮主操作按钮，全圆角胶囊。docs/03 */
export function FloatingActionButton({ text, onTap }: Props) {
  return (
    <View className="fab-wrap">
      <View className="fab" onClick={onTap}>
        <Icon name="plus" size={16} color="#ffffff" />
        <Text className="fab__text">{text}</Text>
      </View>
    </View>
  )
}
