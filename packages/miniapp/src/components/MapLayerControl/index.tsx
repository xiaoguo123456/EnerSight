import { CoverView, CoverImage } from '@tarojs/components'
import { ICON_PATHS, type IconName } from '../Icon/paths'
import './index.scss'

export type MapLayer = 'cloud' | 'wind' | 'temperature' | 'radiation' | 'station'

const LAYERS: { value: MapLayer; label: string; icon: IconName }[] = [
  { value: 'cloud', label: '云图', icon: 'cloud' },
  { value: 'wind', label: '风场', icon: 'wind' },
  { value: 'temperature', label: '温度', icon: 'sun' },
  { value: 'radiation', label: '辐射', icon: 'zap' },
  { value: 'station', label: '站点', icon: 'mapPin' },
]

function iconUri(name: IconName, color: string): string {
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" ` +
    `stroke="${color}" stroke-width="2" stroke-linecap="round" ` +
    `stroke-linejoin="round">${ICON_PATHS[name]}</svg>`
  return `data:image/svg+xml,${encodeURIComponent(svg)}`
}

/**
 * 图层切换器。必须用 CoverView —— map 是原生组件，普通 View 盖不住它。
 * controls 属性已废弃，官方推荐 cover-view。
 */
export function MapLayerControl({
  value, onChange,
}: { value: MapLayer; onChange: (l: MapLayer) => void }) {
  return (
    <CoverView className="layer-ctrl">
      {LAYERS.map((l) => {
        const active = l.value === value
        return (
          <CoverView
            key={l.value}
            className={`layer-ctrl__item ${active ? 'layer-ctrl__item--active' : ''}`}
            onClick={() => onChange(l.value)}
          >
            <CoverImage
              className="layer-ctrl__icon"
              src={iconUri(l.icon, active ? '#ffffff' : '#1677ff')}
            />
            <CoverView className="layer-ctrl__label">{l.label}</CoverView>
          </CoverView>
        )
      })}
    </CoverView>
  )
}
