import { View, Text } from '@tarojs/components'
import './index.scss'

/** 运营建议。每条必须是可执行动作，不写「注意天气」这类空话。docs/08 §5.2 */
export function SuggestionList({ items }: { items: string[] }) {
  return (
    <View className="suggestions">
      {items.map((s, i) => (
        <View className="suggestions__item" key={s}>
          <View className="suggestions__no">
            <Text>{i + 1}</Text>
          </View>
          <Text className="suggestions__text">{s}</Text>
        </View>
      ))}
    </View>
  )
}
