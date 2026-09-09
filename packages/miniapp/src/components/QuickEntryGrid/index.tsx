import { View, Text } from '@tarojs/components'
import { Icon, type IconName } from '../Icon'
import './index.scss'

export interface QuickEntry {
  icon: IconName
  title: string
  subtitle: string
  /** 图标底色，见 docs/02 §二辅助色 */
  tone: 'energy' | 'primary' | 'purple' | 'cyan'
  onTap?: () => void
}

const TONE_COLOR = {
  energy: '#16a34a',
  primary: '#1677ff',
  purple: '#8b5cf6',
  cyan: '#06b6d4',
} as const

export function QuickEntryGrid({ entries }: { entries: QuickEntry[] }) {
  return (
    <View className="quick-entry">
      {entries.map((e) => (
        <View className="quick-entry__item" key={e.title} hoverClass="pressed" hoverStayTime={80} onClick={e.onTap}>
          <View className={`quick-entry__icon quick-entry__icon--${e.tone}`}>
            <Icon name={e.icon} size={22} color={TONE_COLOR[e.tone]} strokeWidth={1.5} />
          </View>
          <Text className="quick-entry__title">{e.title}</Text>
          <Text className="quick-entry__sub">{e.subtitle}</Text>
        </View>
      ))}
    </View>
  )
}
