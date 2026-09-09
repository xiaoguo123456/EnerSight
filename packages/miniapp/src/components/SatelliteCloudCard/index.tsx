import { formatBeijingTime } from '@enersight/core/format'
import { useState } from 'react'
import { View, Text, Image } from '@tarojs/components'
import type { SatelliteCloudResponse } from '@enersight/core/types'
import { Icon } from '../Icon'
import './index.scss'

interface Props {
  /** null：数据源暂时拿不到（夜间有红外，不再是空态） */
  satellite: SatelliteCloudResponse | null
  onFullscreen?: () => void
  onRetry?: () => void
  onLoaded?: () => void
  onImageError?: () => void
}

const BAND_LABEL: Record<SatelliteCloudResponse['band'], string> = {
  visible: '可见光',
  infrared: '红外',
  vapor: '水汽',
}

/**
 * 卫星云图卡。observed_at 必须展示 —— 它是数据观测时间，不等于当前时间。
 * 图片由服务端重投影成等经纬度，站点标记按 bounds 线性定位即可。
 * 图例来自接口，客户端不硬编码色阶；波段由服务端按昼夜选择。docs/04 §四、docs/06 §7.3
 */
export function SatelliteCloudCard({ satellite, onFullscreen, onRetry, onLoaded, onImageError }: Props) {
  const [imageState, setImageState] = useState<{ url: string; status: 'loaded' | 'error' } | null>(null)
  const url = satellite?.image.url ?? ''
  const loaded = imageState?.url === url && imageState.status === 'loaded'
  const failed = imageState?.url === url && imageState.status === 'error'

  if (!satellite) {
    return (
      <View className="sat-card sat-card--empty">
        <View className="sat-card__canvas">
          <Icon name="cloudOff" size={22} color="rgba(255,255,255,.55)" />
          <Text className="sat-card__placeholder">云图数据源暂时不可用</Text>
          {onRetry && <View className="sat-card__retry" onClick={onRetry}>重新加载</View>}
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
      <Image key={url} className="sat-card__img" src={url} mode="aspectFill"
        onLoad={() => { setImageState({ url, status: 'loaded' }); onLoaded?.() }}
        onError={() => { setImageState({ url, status: 'error' }); onImageError?.() }} />
      {!loaded && <View className="sat-card__canvas sat-card__fallback">
        <Text className="sat-card__placeholder">{failed ? '云图加载失败，暂时无法判断云况' : '正在加载卫星影像'}</Text>
        {failed && onRetry && <View className="sat-card__retry" onClick={onRetry}>重新加载</View>}
      </View>}
      {loaded && <View className="sat-card__marker" style={{ left: `${left}%`, top: `${top}%` }}>
        <View className="sat-card__marker-dot" />
      </View>}

      <View className="sat-card__stamp">
        <Icon name="satellite" size={12} color="#ffffff" />
        <View className="sat-card__stamp-text">
          <Text className="sat-card__stamp-title">卫星云图 · {BAND_LABEL[satellite.band]}</Text>
          <Text className="sat-card__stamp-time">观测 {formatBeijingTime(satellite.observed_at)}（北京时间）</Text>
        </View>
      </View>

      {loaded && onFullscreen && (
        <View className="sat-card__full" onClick={onFullscreen}>
          <Icon name="maximize" size={14} color="#ffffff" />
        </View>
      )}

      {loaded && <View className="sat-card__legend">
        <Text className="sat-card__legend-title">{satellite.legend.title}</Text>
        <View className="sat-card__legend-bar" style={{ background: gradient }} />
        <View className="sat-card__legend-scale">
          <Text>{lo}</Text>
          <Text>{hi}</Text>
        </View>
      </View>}

      {/* 数据出处：JMA 利用规约要求注明来源，不能省 */}
      <Text className="sat-card__credit">Himawari-9 · 日本气象厅</Text>
    </View>
  )
}
