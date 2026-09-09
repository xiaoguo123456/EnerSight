import { useEffect, useState } from 'react'
import { View, Text, ScrollView } from '@tarojs/components'
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
  value, onChange, onOpenChange,
}: { value: MapLayer; onChange: (l: MapLayer) => void; onOpenChange?: (open: boolean) => void }) {
  const [open, setOpen] = useState(false)
  useEffect(() => { onOpenChange?.(open) }, [open, onOpenChange])
  return (
    <>
      <View className="layer-ctrl" onClick={() => setOpen(true)}>
        <Icon name="layers" size={20} color="#334155" />
        <Text className="layer-ctrl__label">图层</Text>
      </View>
      {open && (
        <View className="layer-ctrl__modal" catchMove onClick={() => setOpen(false)}>
          <View className="layer-ctrl__panel" onClick={(e) => e.stopPropagation()}>
            <View className="layer-ctrl__head">
              <Text className="layer-ctrl__title">地图图层</Text>
              <View className="layer-ctrl__close" aria-label="关闭图层选择" onClick={() => setOpen(false)}>
                <Icon name="x" size={20} color="#64748b" />
              </View>
            </View>
            <Text className="layer-ctrl__hint">选择要查看的气象数据，电站位置始终保留</Text>
            <ScrollView scrollY className="layer-ctrl__scroll">
              <View className="layer-ctrl__options">
                {LAYERS.map((l) => {
                  const active = l.value === value
                  return (
                    <View key={l.value} className={`layer-ctrl__option ${active ? 'layer-ctrl__option--active' : ''}`}
                      onClick={() => { onChange(l.value); setOpen(false) }}>
                      <Icon name={l.icon} size={20} color={active ? '#1677ff' : '#64748b'} strokeWidth={1.75} />
                      <Text>{l.value === 'station' ? '仅看电站' : l.label}</Text>
                      {active && <Text className="layer-ctrl__selected">已选</Text>}
                    </View>
                  )
                })}
              </View>
            </ScrollView>
          </View>
        </View>
      )}
    </>
  )
}
