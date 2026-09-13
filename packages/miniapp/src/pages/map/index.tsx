import { useAppShare } from '@/hooks/useAppShare'
import { Map, View, Text, Input, Image } from '@tarojs/components'
import Taro, { useDidHide, useDidShow } from '@tarojs/taro'
import { useEffect, useRef, useState } from 'react'
import { formatBeijingTime, formatPower, formatRadiation, formatTemperature, formatWindSpeed } from '@enersight/core/format'
import { mapRegionPhase } from '@enersight/core/map'
import type { CatalogPlant, GeoPlace } from '@enersight/core/types'
import {
  EmptyState, Icon, MapLayerControl, MapLegend, MetricCard, MetricGrid, Skeleton, StatusBadge,
} from '@/components'
import type { MapLayer } from '@/components'
import { geoApi } from '@/api/geo'
import { homeApi } from '@/api/home'
import { useCatalogMarkers } from '@/hooks/useCatalogMarkers'
import { useMapLayer } from '@/hooks/useMapLayer'
import { useMapViewport } from '@/hooks/useMapViewport'
import { getSafeArea } from '@/hooks/useSafeArea'
import { useRequest } from '@/hooks/useRequest'
import { useMapStore, useStationStore } from '@/store'
import { useWeatherModel } from '@/store/weatherModel'
import './index.scss'
import { WindParticles } from '@/components/WindParticles'

// 底部面板高度，地图浮层的 bottom 要避开它
const SHEET_HEIGHT = 172

const LEVEL_TEXT: Record<string, string> = {
  excellent: '优秀', good: '良好', fair: '一般', poor: '较差',
}

const PLACE_ICON: Record<GeoPlace['type'], 'mapPin' | 'navigation' | 'sun' | 'layers'> = {
  city: 'mapPin', poi: 'mapPin', coordinate: 'navigation', station: 'sun', plant: 'layers',
}

export default function MapPage() {
  useAppShare()
  const model = useWeatherModel(s => s.model)
  const activeLayer = useMapStore((s) => s.activeLayer)
  const saveLayer = useMapStore((s) => s.setActiveLayer)
  const [layer, setLayer] = useState<MapLayer>(activeLayer)
  const [layerPanelOpen, setLayerPanelOpen] = useState(false)
  const [pageVisible, setPageVisible] = useState(true)
  useDidHide(() => setPageVisible(false))
  const [collapsed, setCollapsed] = useState(false)
  const sheetHeight = collapsed ? 68 : SHEET_HEIGHT
  useEffect(() => setLayer(activeLayer), [activeLayer])
  // 卫星影像底图。docs/01 §四：全球地图 / 行政地图 / 卫星影像底图
  const [satellite, setSatellite] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [results, setResults] = useState<GeoPlace[] | null>(null)
  // 选中的公开电站（搜索结果或点 marker），底部提供直接查看入口
  const [picked, setPicked] = useState<CatalogPlant | GeoPlace | null>(null)
  const [searchStatus, setSearchStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [searchRetry, setSearchRetry] = useState(0)
  const searchSeq = useRef(0)
  const safe = getSafeArea()
  const currentId = useStationStore((s) => s.currentId)
  const req = useRequest(() => homeApi.mapOverview(currentId ?? undefined), [currentId])

  useEffect(() => { setPicked(null) }, [currentId])
  useDidShow(() => { setPageVisible(true); setPicked(null) })
  const station = req.data?.station
  const index = req.data?.index
  const weather = req.data?.weather
  const viewport = useMapViewport('main-map', station, currentId)
  const center = viewport.camera
  const scale = center.scale

  // 「站点」图层只显示 marker，不贴图
  const dataLayer = layer === 'station' ? null : layer
  const overlay = useMapLayer('main-map', dataLayer, pageVisible && req.status === 'success')
  // 公开电站 marker：任何图层下都显示，视野内最多 100 个
  useEffect(() => { const timer = setTimeout(() => void overlay.viewportChanged(), 250); return () => clearTimeout(timer) }, [center.latitude, center.longitude, scale, sheetHeight])
  const catalog = useCatalogMarkers('main-map', true, scale)
  useEffect(() => { if (req.status !== 'loading') void catalog.refresh() }, [req.status])  // eslint-disable-line react-hooks/exhaustive-deps

  // 输入变化立即使旧请求失效；失败与空结果分别展示。
  useEffect(() => {
    const mine = ++searchSeq.current
    const kw = keyword.trim()
    if (!kw) { setResults(null); setSearchStatus('idle'); return }
    setSearchStatus('loading'); setResults(null)
    const timer = setTimeout(async () => {
      try {
        const data = await geoApi.search(kw)
        if (mine === searchSeq.current) { setResults(data.results); setSearchStatus('success') }
      } catch { if (mine === searchSeq.current) setSearchStatus('error') }
    }, 300)
    return () => { clearTimeout(timer); ++searchSeq.current }
  }, [keyword, searchRetry])

  const choose = (r: GeoPlace) => {
    setResults(null)
    setKeyword('')
    viewport.moveTo(r)
    setPicked(r.type === 'plant' ? r : null)
    setTimeout(() => { void overlay.refresh(); void catalog.refresh() }, 400)
  }

  const onMarkerTap = (e: any) => {
    const id = Number(e?.detail?.markerId ?? e?.markerId)
    const cluster = catalog.clusterByMarkerId(id)
    if (cluster) {
      viewport.moveTo(cluster, Math.min(18, scale + 2))
      setTimeout(() => { void catalog.refresh() }, 400)
      return
    }
    const p = catalog.byMarkerId(id)
    if (p) setPicked(p)
  }

  const viewPicked = () => {
    const id = picked && ('capacity' in picked ? picked.id : picked.catalog_id)
    if (!id) return
    setPicked(null)
    void Taro.navigateTo({ url: `/pages/station/detail?id=${encodeURIComponent(id)}` })
  }

  const locate = () => {
    if (!station) return
    viewport.moveTo(station)
  }
  const zoom = (delta: number) => {
    if (overlay.isPreview) overlay.invalidate()
    void viewport.zoom(delta)
  }

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
          enableRotate={false}
          enableOverlooking={false}
          showScale
          enableSatellite={satellite}
          markers={[
            ...(station ? [{
              id: 1,
              latitude: station.latitude,
              longitude: station.longitude,
              width: 24, height: 24,
              callout: {
                content: '当前电站', color: '#ffffff', bgColor: '#1677FF',
                padding: 6, borderRadius: 6, display: 'ALWAYS',
                fontSize: 12, textAlign: 'center',
              },
            }] : []),
            ...catalog.markers,
          ] as any}
          onMarkerTap={onMarkerTap}
          onError={(e) => console.error('[map] 加载失败', e)}
          onRegionChange={(e: any) => {
            void viewport.regionChanged(e)
            const kind = mapRegionPhase(e)
            const cause = e?.detail?.causedBy ?? e?.causedBy
            // 模拟器图片不随原生地图运动，手势开始即撤下旧图，结束按新视野装载。
            if (overlay.isPreview && kind === 'begin' && (cause === 'drag' || cause === 'scale')) overlay.invalidate()
            if (kind === 'end' && (overlay.isPreview || cause !== 'update')) {
              void overlay.viewportChanged()
              if (cause !== 'update') {
                void catalog.refresh()
              }
            }
          }}
        />

        {overlay.preview && <View className="map-page__preview">
          {overlay.preview.images.map((img, i) => <Image key={`${overlay.preview!.id}-${i}`}
            className="map-page__preview-image" src={img.url} mode="scaleToFill" style={{ ...img.style, opacity: dataLayer === 'cloud' ? .65 : 1 }}
            onLoad={() => overlay.imageLoaded(overlay.preview!.id, i)}
            onError={() => overlay.imageError(overlay.preview!.id, '图层图片加载失败，请重试')} />)}
        </View>}

        {!layerPanelOpen && overlay.samples.map((p, i) => <View key={i} className="map-page__sample" style={{ left: p.left, top: p.top, transform: parseFloat(p.left) > 75 ? 'translate(-100%, -50%)' : 'translate(-50%, -50%)' }}><Text>{p.text}</Text></View>)}
        {pageVisible && overlay.wind && !layerPanelOpen && <WindParticles layoutVersion={sheetHeight} vectors={overlay.wind.vectors} region={overlay.wind.region} />}

        {/* 搜索框浮在地图顶部，拉满宽度；结果合并了城市、坐标、站点与公开电站 */}
        <View className="map-page__search">
          <Icon name="search" size={15} color="#9ca3af" />
          <Input
            className="map-page__search-input"
            placeholder="搜索城市、坐标或电站"
            placeholderClass="map-page__ph"
            value={keyword}
            onInput={(e) => { ++searchSeq.current; setKeyword(e.detail.value) }}
          />
          {keyword && (
            <View className="map-page__search-clear" onClick={() => { setKeyword(''); setResults(null) }}>
              <Icon name="minus" size={12} color="#9ca3af" />
            </View>
          )}
        </View>
        {searchStatus !== 'idle' && (
          <View className="map-page__results">
            {searchStatus === 'loading' && <Text className="map-page__result-empty">正在搜索…</Text>}
            {searchStatus === 'error' && <Text className="map-page__result-empty" onClick={() => setSearchRetry((v) => v + 1)}>搜索失败，点击重试</Text>}
            {searchStatus === 'success' && results?.length === 0 && <Text className="map-page__result-empty">没有匹配结果</Text>}
            {results?.map((r, i) => (
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

        <MapLayerControl onOpenChange={setLayerPanelOpen} value={layer} onChange={(value) => { if (value === layer) void overlay.refresh(); setLayer(value); if (value !== 'station') saveLayer(value) }} />
        {dataLayer && <View className="map-page__data-state" onClick={() => {
          if (overlay.error) { overlay.refresh(); return }
          if (overlay.observedAt) void Taro.showModal({
            title: '图层数据', showCancel: false,
            content: `${overlay.sourceLabel || '卫星云图'}\n${formatBeijingTime(overlay.observedAt)}（北京时间）\n${overlay.attribution}\n${overlay.coverage}${overlay.stale ? '\n当前显示缓存预报' : ''}`,
          })
        }}><Text>{overlay.loading ? '图层加载中…' : overlay.error ? (/正在后台准备/.test(overlay.errorMessage) ? '图层准备中 · 重试' : '图层暂不可用 · 重试') : overlay.observedAt ? `${overlay.modelName || '云图'} · ${formatBeijingTime(overlay.observedAt).slice(-5)}${overlay.stale ? ' · 缓存' : ''} ⓘ` : '等待图层数据'}</Text></View>}

        {overlay.legend && !overlay.loading && !overlay.error && !picked && (
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
            role="button" aria-label={satellite ? '切换普通底图' : '切换卫星底图'}
            onClick={() => setSatellite((v) => !v)}
          >
            <Icon
              name="globe"
              size={17}
              color={satellite ? '#ffffff' : '#1f2937'}
              fill={satellite ? 'rgba(255,255,255,0.3)' : false}
            />
          </View>
          <View className="map-page__tool" role="button" aria-label="回到当前电站" hoverClass="pressed" onClick={locate}>
            <Icon name="crosshair" size={17} color="#1f2937" />
          </View>
          <View className="map-page__tool" role="button" aria-label="放大地图" hoverClass="pressed" onClick={() => zoom(1)}>
            <Icon name="plus" size={17} color="#1f2937" />
          </View>
          <View className="map-page__tool" role="button" aria-label="缩小地图" hoverClass="pressed" onClick={() => zoom(-1)}>
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
                <MetricCard icon="leaf" iconColor="#16a34a" label="发电适宜度"
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
