import { View, Text } from '@tarojs/components'
import { Icon, type IconName } from '../Icon'
import './index.scss'

export type MapLayer = 'cloud' | 'wind' | 'temperature' | 'radiation' | 'station'

// fill: 闭合形状可填充；wind 是三条开放线，填充无意义
const LAYERS: { value: MapLayer; label: string; icon: IconName; fill: boolean }[] = [
  { value: 'cloud', label: '云图', icon: 'cloud', fill: true },
  { value: 'wind', label: '风场', icon: 'wind', fill: false },
  { value: 'temperature', label: '温度', icon: 'thermometer', fill: true },
  { value: 'radiation', label: '辐射', icon: 'sun', fill: true },
  { value: 'station', label: '站点', icon: 'mapPin', fill: true },
]

/**
 * 图层切换器。
 *
 * 基础库 3.16.2 起 <map> 支持同层渲染，浮层可用普通 View
 * （控制台会提示「建议使用 view 代替 cover-view」）。
 * 上线前在 mp 后台把最低基础库设到支持同层渲染的版本。
 */
export function MapLayerControl({
  value, onChange,
}: { value: MapLayer; onChange: (l: MapLayer) => void }) {
  return (
    <View className="layer-ctrl">
      {LAYERS.map((l) => {
        const active = l.value === value
        return (
          <View
            key={l.value}
            className={`layer-ctrl__item ${active ? 'layer-ctrl__item--active' : ''}`}
            onClick={() => onChange(l.value)}
          >
            <Icon
              name={l.icon}
              size={20}
              color={active ? '#ffffff' : '#1677ff'}
              fill={l.fill && (active ? 'rgba(255,255,255,0.35)' : true)}
              strokeWidth={2.4}
            />
            <Text className="layer-ctrl__label">{l.label}</Text>
          </View>
        )
      })}
    </View>
  )
}
