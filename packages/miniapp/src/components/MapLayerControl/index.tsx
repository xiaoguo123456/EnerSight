import { View, Text } from '@tarojs/components'
import { Icon, type IconName } from '../Icon'
import './index.scss'

export type MapLayer = 'cloud' | 'wind' | 'temperature' | 'radiation' | 'station'

const LAYERS: { value: MapLayer; label: string; icon: IconName }[] = [
  { value: 'cloud', label: '云图', icon: 'cloud' },
  { value: 'wind', label: '风场', icon: 'wind' },
  { value: 'temperature', label: '温度', icon: 'thermometer' },
  { value: 'radiation', label: '辐射', icon: 'sun' },
  { value: 'station', label: '站点', icon: 'mapPin' },
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
            <Icon name={l.icon} size={18} color={active ? '#ffffff' : '#1677ff'} />
            <Text className="layer-ctrl__label">{l.label}</Text>
          </View>
        )
      })}
    </View>
  )
}
