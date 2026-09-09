import { Map, View, Text, Input } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useEffect, useRef, useState } from 'react'
import { formatPower, formatRadiation, formatTemperature, formatWindSpeed } from '@enersight/core/format'
import type { CatalogPlant, GeoPlace } from '@enersight/core/types'
import {
  EmptyState, Icon, MapLayerControl, MapLegend, MetricCard, MetricGrid, Skeleton, StatusBadge,
} from '@/components'
import type { MapLayer } from '@/components'
import { geoApi } from '@/api/geo'
import { homeApi } from '@/api/home'
import { useCatalogMarkers } from '@/hooks/useCatalogMarkers'
import { useMapLayer } from '@/hooks/useMapLayer'
import { getSafeArea } from '@/hooks/useSafeArea'
import { useRequest } from '@/hooks/useRequest'
import { useMapStore, useStationStore } from '@/store'
import './index.scss'

// 底部面板高度，地图浮层的 bottom 要避开它
const SHEET_HEIGHT = 172

const LEVEL_TEXT: Record<string, string> = {
  excellent: '优秀', good: '良好', fair: '一般', poor: '较差',
}

// 地图未加载到站点前的默认中心：华东
const FALLBACK_CENTER = { latitude: 31.3, longitude: 120.62 }
const PLACE_ICON: Record<GeoPlace['type'], 'mapPin' | 'navigation' | 'sun' | 'layers'> = {
  city: 'mapPin', poi: 'mapPin', coordinate: 'navigation', station: 'sun', plant: 'layers',
}

export default function MapPage() {
  const activeLayer = useMapStore((s) => s.activeLayer)
  const saveLayer = useMapStore((s) => s.setActiveLayer)
  const [layer, setLayer] = useState<MapLayer>(activeLayer)
  const [collapsed, setCollapsed] = useState(false)
  const sheetHeight = collapsed ? 68 : SHEET_HEIGHT
  useEffect(() => setLayer(activeLayer), [activeLayer])
  // 卫星影像底图。docs/01 §四：全球地图 / 行政地图 / 卫星影像底图
  const [satellite, setSatellite] = useState(false)
  const [scale, setScale] = useState(9)
  // 地图中心：跟随站点；搜索选中后改为选中点
  const [focus, setFocus] = useState<{ latitude: number; longitude: number } | null>(null)
  const [keyword, setKeyword] = useState('')
  const [results, setResults] = useState<GeoPlace[] | null>(null)
  // 选中的公开电站（搜索结果或点 marker），底部提供直接查看入口
  const [picked, setPicked] = useState<CatalogPlant | GeoPlace | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout>>()
  const safe = getSafeArea()
  const currentId = useStationStore((s) => s.currentId)
  const req = useRequest(() => homeApi.mapOverview(currentId ?? undefined), [currentId])

  const station = req.data?.station
  const index = req.data?.index
  const weather = req.data?.weather
  const center = focus ?? station ?? FALLBACK_CENTER

  // 「站点」图层只显示 marker，不贴图
  const dataLayer = layer === 'station' ? null : layer
  const overlay = useMapLayer('main-map', dataLayer, req.status === 'success')
  // 公开电站 marker：任何图层下都显示，视野内最多 100 个
  const catalog = useCatalogMarkers('main-map', true, scale)
  useEffect(() => { if (req.status !== 'loading') void catalog.refresh() }, [req.status])  // eslint-disable-line react-hooks/exhaustive-deps

  // 搜索：300ms 防抖，空串清空
  useEffect(() => {
    clearTimeout(timer.current)
    const kw = keyword.trim()
    if (!kw) { setResults(null); return }
    timer.current = setTimeout(async () => {
      try { setResults((await geoApi.search(kw)).results) } catch { setResults([]) }
    }, 300)
    return () => clearTimeout(timer.current)
  }, [keyword])

  const choose = (r: GeoPlace) => {
    setResults(null)
    setKeyword('')
    setFocus({ latitude: r.latitude, longitude: r.longitude })
    setPicked(r.type === 'plant' ? r : null)
    setTimeout(() => { void overlay.refresh(); void catalog.refresh() }, 400)
  }

  const onMarkerTap = (e: any) => {
    const id = Number(e?.detail?.markerId ?? e?.markerId)
    const cluster = catalog.clusterByMarkerId(id)
    if (cluster) {
      setFocus({ latitude: cluster.latitude, longitude: cluster.longitude })
      setScale((v) => Math.min(18, v + 2))
      setTimeout(() => { void catalog.refresh() }, 400)
      return
    }
    const p = catalog.byMarkerId(id)
    if (p) setPicked(p)
  }

  const setCurrent = useStationStore((s) => s.setCurrent)
  const viewPicked = () => {
    const id = picked && ('capacity' in picked ? picked.id : picked.catalog_id)
    if (!id) return
    setCurrent(id)
    setPicked(null)
    void Taro.navigateTo({ url: `/pages/station/detail?id=${encodeURIComponent(id)}` })
  }

  const locate = () => {
    if (!station) return
    setFocus(null)
    Taro.createMapContext('main-map').moveToLocation({ latitude: station.latitude, longitude: station.longitude })
  }
  const zoom = (delta: number) => setScale((v) => Math.min(18, Math.max(3, v + delta)))

  return (
    <View className="map-page">
      {/* 胶囊按钮那一行：左侧放站点选择器，右侧留给胶囊 */}
      <View
        className="map-page__top"
        style={{
          paddingTop: `${safe.statusBarHeight}px`,
          height: `${safe.navBarHeight}px`,
          paddingRight: `${Math.max(safe.menuGuardRight, 16)}px`,
        }}
      >
        <View className="map-page__station" onClick={() => Taro.switchTab({ url: '/pages/station/index' })}>
          <View className="map-page__station-thumb">
            <Icon name={station?.type === 'wind' ? 'wind' : 'sun'} size={14} color="#fff" />
          </View>
          <Text className="map-page__station-name">{station?.name ?? '选择站点'}</Text>
          <Icon name="chevronDown" size={13} color="#6b7280" />
        </View>
      </View>

      {/*
        基础库 3.16.2 起 map 支持同层渲染，浮层与底部面板都用普通 View，
        可以叠在地图上。上线前在 mp 后台把最低基础库设到支持同层渲染的版本。
      */}
      <View className="map-page__canvas">
        <Map
          id="main-map"
          className="map-page__map"
          latitude={center.latitude}
          longitude={center.longitude}
          scale={scale}
          showLocation
          showScale
          enableSatellite={satellite}
          markers={[
            ...(station ? [{
              id: 1,
              latitude: station.latitude,
              longitude: station.longitude,
              width: 24, height: 24,
              callout: {
                content: station.name, color: '#ffffff', bgColor: '#1677FF',
                padding: 6, borderRadius: 6, display: 'ALWAYS',
                fontSize: 12, textAlign: 'center',
              },
            }] : []),
            ...catalog.markers,
          ] as any}
          onMarkerTap={onMarkerTap}
          onError={(e) => console.error('[map] 加载失败', e)}
          onRegionChange={(e: any) => {
            // 只响应用户手势结束；贴图本身也会触发 regionchange，不过滤会形成请求循环
            const d = e?.detail ?? e
            const isEnd = d?.type === 'end'
            const byUser = d?.causedBy === 'drag' || d?.causedBy === 'scale'
            if (isEnd && byUser) { void overlay.refresh(); void catalog.refresh() }
          }}
        />

        {/* 搜索框浮在地图顶部，拉满宽度；结果合并了城市、坐标、站点与公开电站 */}
        <View className="map-page__search">
          <Icon name="search" size={15} color="#9ca3af" />
          <Input
            className="map-page__search-input"
            placeholder="搜索城市 / 坐标 / 站点 / 公开电站"
            placeholderClass="map-page__ph"
            value={keyword}
            onInput={(e) => setKeyword(e.detail.value)}
          />
          {keyword && (
            <View className="map-page__search-clear" onClick={() => { setKeyword(''); setResults(null) }}>
              <Icon name="minus" size={12} color="#9ca3af" />
            </View>
          )}
        </View>
        {results && (
          <View className="map-page__results">
            {results.length === 0 && <Text className="map-page__result-empty">没有匹配结果</Text>}
            {results.map((r, i) => (
              <View className="map-page__result" key={`${r.type}-${i}`} hoverClass="pressed" onClick={() => choose(r)}>
                <Icon name={PLACE_ICON[r.type]} size={14} color={r.type === 'plant' ? '#7c3aed' : '#6b7280'} />
                <View className="map-page__result-text">
                  <Text className="map-page__result-name">{r.name}</Text>
                  {r.address && <Text className="map-page__result-addr">{r.address}</Text>}
                </View>
                {r.type === 'plant' && <Text className="map-page__result-tag">公开电站</Text>}
                {r.type === 'station' && <Text className="map-page__result-tag map-page__result-tag--mine">站点</Text>}
              </View>
            ))}
          </View>
        )}

        {picked && (
          <View className="map-page__picked" style={{ bottom: `${sheetHeight + 12}px` }}>
            <View className="map-page__picked-text">
              <Text className="map-page__picked-name">{picked.name}</Text>
              <Text className="map-page__picked-meta">
                {'capacity' in picked
                  ? `${picked.type === 'solar' ? '光伏' : '风电'} · ${formatPower(picked.capacity).value} ${formatPower(picked.capacity).unit}${picked.address ? ` · ${picked.address}` : ''}`
                  : picked.address}
              </Text>
            </View>
            <View className="map-page__picked-add" hoverClass="pressed" onClick={viewPicked}>
              <Icon name="chevronRight" size={13} color="#fff" />
              <Text className="map-page__picked-add-text">查看电站</Text>
            </View>
            <View className="map-page__picked-close" onClick={() => setPicked(null)}>
              <Icon name="minus" size={12} color="#9ca3af" />
            </View>
          </View>
        )}

        <MapLayerControl value={layer} onChange={(value) => { setLayer(value); if (value !== 'station') saveLayer(value) }} />
        {dataLayer && <View className="map-page__data-state" onClick={() => void overlay.refresh()}><Text>{overlay.loading ? '图层加载中' : overlay.error ? '图层暂不可用 · 点击重试' : overlay.observedAt ? `图层数据 ${overlay.observedAt.slice(5, 16).replace('T', ' ')}` : '等待图层数据'}</Text></View>}

        {overlay.legend && !picked && (
          <View className="map-page__legend" style={{ bottom: `${sheetHeight + 12}px` }}>
            <MapLegend
              spec={{
                title: overlay.legend.title,
                colors: overlay.legend.colors,
                stops: overlay.legend.stops ?? undefined,
                labels: overlay.legend.labels ? [overlay.legend.labels[0]!, overlay.legend.labels[1]!] : undefined,
              }}
            />
          </View>
        )}

        <View className="map-page__tools" style={{ bottom: `${sheetHeight + 12}px` }}>
          <View
            className={`map-page__tool ${satellite ? 'map-page__tool--on' : ''}`}
            onClick={() => setSatellite((v) => !v)}
          >
            <Icon
              name="globe"
              size={17}
              color={satellite ? '#ffffff' : '#1f2937'}
              fill={satellite ? 'rgba(255,255,255,0.3)' : false}
            />
          </View>
          <View className="map-page__tool" hoverClass="pressed" onClick={locate}>
            <Icon name="crosshair" size={17} color="#1f2937" />
          </View>
          <View className="map-page__tool" hoverClass="pressed" onClick={() => zoom(1)}>
            <Icon name="plus" size={17} color="#1f2937" />
          </View>
          <View className="map-page__tool" hoverClass="pressed" onClick={() => zoom(-1)}>
            <Icon name="minus" size={17} color="#1f2937" />
          </View>
        </View>

        {/* 底部面板叠在地图上 */}
        <View className="map-page__sheet" style={{ height: `${sheetHeight}px` }}>
          <View className="map-page__collapse" onClick={() => setCollapsed((v) => !v)}><Text>{collapsed ? '展开气象详情' : '收起详情'}</Text><Icon name={collapsed ? 'chevronUp' : 'chevronDown'} size={14} color="#64748b" /></View>
          {req.status === 'loading' && <Skeleton height={120} lines={2} />}
          {req.status === 'error' && (
            <EmptyState
              icon={req.error.status === 404 ? 'mapPin' : 'alertTriangle'}
              title={req.error.status === 404 ? '目录暂无电站' : '加载失败'}
              actionText={req.error.status === 404 ? '查看目录' : '重试'}
              onAction={req.error.status === 404
                ? () => Taro.switchTab({ url: '/pages/station/index' })
                : req.reload}
            />
          )}
          {req.status === 'success' && station && (
            <>
              <View
                className="map-page__sheet-head"
                onClick={() => Taro.navigateTo({ url: `/pages/station/detail?id=${station.id}` })}
              >
                <Text className="map-page__sheet-name">{station.name}</Text>
                <StatusBadge status={station.status} />
                <View className="map-page__sheet-spacer" />
                <Icon name="chevronRight" size={15} color="#9ca3af" />
              </View>

              {!collapsed && <MetricGrid>
                <MetricCard icon="leaf" iconColor="#16a34a" label="环境指数"
                  metric={{ value: index?.score != null ? String(Math.round(index.score)) : '—', unit: '分' }}
                  caption={LEVEL_TEXT[index?.level ?? ''] ?? '—'} />
                <MetricCard icon="cloudSun" label="天气"
                  metric={formatTemperature(weather?.temperature.value)}
                  caption={weather?.weather_text ?? undefined} />
                <MetricCard icon="wind" iconFill={false} label="风速"
                  metric={formatWindSpeed(weather?.wind_speed.value)}
                  deltaPercent={weather?.wind_speed.delta_percent ?? null} />
                <MetricCard icon="sun" label="辐射"
                  metric={formatRadiation(weather?.radiation.value)}
                  deltaPercent={weather?.radiation.delta_percent ?? null} />
              </MetricGrid>}

              {!collapsed && req.data.ai_hint && (
                <View
                  className="map-page__hint"
                  onClick={() => Taro.navigateTo({ url: '/pages/report/index' })}
                >
                  <Icon name="barChart" size={14} color="#1677ff" />
                  <Text className="map-page__hint-text">{req.data.ai_hint}</Text>
                  <Icon name="chevronRight" size={13} color="#9ca3af" />
                </View>
              )}
            </>
          )}
        </View>
      </View>
    </View>
  )
}
