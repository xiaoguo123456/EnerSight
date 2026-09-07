import { View, Text } from '@tarojs/components'
import './index.scss'

export interface QuickEntry {
  icon: string
  title: string
  subtitle: string
  /** 图标底色 token 名，见 docs/02 §二辅助色 */
  tone: 'energy' | 'primary' | 'purple' | 'cyan'
  onTap?: () => void
}

export function QuickEntryGrid({ entries }: { entries: QuickEntry[] }) {
  return (
    <View className="quick-entry">
      {entries.map((e) => (
        <View className="quick-entry__item" key={e.title} onClick={e.onTap}>
          <View className={`quick-entry__icon quick-entry__icon--${e.tone}`}>
            <Text>{e.icon}</Text>
          </View>
          <Text className="quick-entry__title">{e.title}</Text>
          <Text className="quick-entry__sub">{e.subtitle}</Text>
        </View>
      ))}
    </View>
  )
}
