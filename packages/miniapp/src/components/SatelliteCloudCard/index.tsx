import { View, Text, Image } from '@tarojs/components'
import type { SatelliteCloudResponse } from '@enersight/core/types'
import { Icon } from '../Icon'
import './index.scss'

interface Props {
  /** null 时按 status 区分空态文案：夜间无可见光 / 数据源暂时拿不到 */
  satellite: SatelliteCloudResponse | null
  status?: 'ok' | 'night' | 'unavailable'
  onFullscreen?: () => void
}

const EMPTY_TEXT = {
  night: { icon: 'moon', text: '夜间无可见光云图，天亮后自动恢复' },
  unavailable: { icon: 'cloudOff', text: '云图数据源暂时不可用，稍后自动重试' },
} as const

/** "2026-09-08T14:20+08:00" → "09-08 14:20" */
function formatObserved(iso: string): string {
  return `${iso.slice(5, 10)} ${iso.slice(11, 16)}`
}

/**
 * 卫星云图卡。observed_at 必须展示 —— 它是数据观测时间，不等于当前时间。
 * 图片由服务端重投影成等经纬度，站点标记按 bounds 线性定位即可。
 * 图例来自接口，客户端不硬编码色阶。docs/04 §四、docs/06 §7.3
 */
export function SatelliteCloudCard({ satellite, status = 'night', onFullscreen }: Props) {
  if (!satellite) {
    const empty = EMPTY_TEXT[status === 'unavailable' ? 'unavailable' : 'night']
    return (
      <View className="sat-card sat-card--empty">
        <View className="sat-card__canvas">
          <Icon name={empty.icon} size={22} color="rgba(255,255,255,.55)" />
          <Text className="sat-card__placeholder">{empty.text}</Text>
        </View>
        <View className="sat-card__stamp">
          <Icon name="satellite" size={12} color="#ffffff" />
          <View className="sat-card__stamp-text">
            <Text className="sat-card__stamp-title">卫星云图</Text>
            <Text className="sat-card__stamp-time">暂无观测</Text>
          </View>
        </View>
      </View>
    )
  }

  const { sw, ne } = satellite.image.bounds
  const m = satellite.station_marker
  const left = ((m.longitude - sw.longitude) / (ne.longitude - sw.longitude)) * 100
  const top = ((ne.latitude - m.latitude) / (ne.latitude - sw.latitude)) * 100
  const gradient = `linear-gradient(90deg, ${satellite.legend.colors.join(', ')})`
  const [lo, hi] = satellite.legend.labels ?? ['低', '高']

  return (
    <View className="sat-card">
      <Image className="sat-card__img" src={satellite.image.url} mode="aspectFill" />
      <View className="sat-card__marker" style={{ left: `${left}%`, top: `${top}%` }}>
        <View className="sat-card__marker-dot" />
      </View>

      <View className="sat-card__stamp">
        <Icon name="satellite" size={12} color="#ffffff" />
        <View className="sat-card__stamp-text">
          <Text className="sat-card__stamp-title">卫星云图 · 可见光</Text>
          <Text className="sat-card__stamp-time">观测 {formatObserved(satellite.observed_at)}</Text>
        </View>
      </View>

      {onFullscreen && (
        <View className="sat-card__full" onClick={onFullscreen}>
          <Icon name="maximize" size={14} color="#ffffff" />
        </View>
      )}

      <View className="sat-card__legend">
        <Text className="sat-card__legend-title">{satellite.legend.title}</Text>
        <View className="sat-card__legend-bar" style={{ background: gradient }} />
        <View className="sat-card__legend-scale">
          <Text>{lo}</Text>
          <Text>{hi}</Text>
        </View>
      </View>
    </View>
  )
}
