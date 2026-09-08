import { View, Text } from '@tarojs/components'
import './index.scss'

export interface LegendSpec {
  title: string
  /** 色带取色点，从低到高 */
  colors: string[]
  /** 刻度值；云图这类无量纲图层用 labels */
  stops?: number[]
  labels?: [string, string]
}

/**
 * 图层图例。随激活图层同步切换，客户端不硬编码色阶 —— 由接口下发。
 * docs/06 §7.2
 */
export function MapLegend({ spec }: { spec: LegendSpec }) {
  const ticks = spec.stops ?? spec.labels ?? ['低', '高']
  return (
    <View className="map-legend">
      <Text className="map-legend__title">{spec.title}</Text>
      <View
        className="map-legend__bar"
        style={{ background: `linear-gradient(90deg, ${spec.colors.join(', ')})` }}
      />
      <View className="map-legend__scale">
        {ticks.map((t) => (
          <Text className="map-legend__tick" key={String(t)}>{t}</Text>
        ))}
      </View>
    </View>
  )
}
