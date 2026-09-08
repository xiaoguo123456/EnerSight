import { Map, View, Text, Input } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useState } from 'react'
import {
  formatPercent, formatRadiation, formatTemperature, formatWindSpeed,
} from '@enersight/core/format'
import {
  Icon, MapLayerControl, MapLegend, MetricCard, MetricGrid, StatusBadge,
} from '@/components'
import type { LegendSpec, MapLayer } from '@/components'
import { getSafeArea } from '@/hooks/useSafeArea'
import { mockHome } from '@/mocks'
import './index.scss'

// 图例由接口下发，此处为接口落地前的占位。docs/06 §7.2
const LEGENDS: Record<MapLayer, LegendSpec> = {
  radiation: {
    title: '辐射强度（W/m²）',
    colors: ['#3b5bdb', '#22b8cf', '#51cf66', '#fcc419', '#ff922b', '#f03e3e'],
    stops: [0, 200, 400, 600, 800, 1000],
  },
  temperature: {
    title: '温度（℃）',
    colors: ['#4c6ef5', '#22b8cf', '#51cf66', '#fcc419', '#f76707'],
    stops: [-20, -10, 0, 10, 20, 30, 40],
  },
  wind: {
    title: '风速（m/s）',
    colors: ['#e7f5ff', '#74c0fc', '#4c6ef5', '#7048e8', '#f03e3e'],
    stops: [0, 5, 10, 15, 20],
  },
  cloud: {
    title: '云量强度',
    colors: ['#1f2937', '#6b7280', '#ffffff'],
    labels: ['低', '高'],
  },
  station: { title: '站点状态', colors: ['#16a34a', '#1677ff'], labels: ['正常', '待机'] },
}

// 底部面板高度，地图浮层的 bottom 要避开它
const SHEET_HEIGHT = 172

export default function MapPage() {
  const [layer, setLayer] = useState<MapLayer>('cloud')
  // 卫星影像底图。docs/01 §四：全球地图 / 行政地图 / 卫星影像底图
  const [satellite, setSatellite] = useState(false)
  const { station, index, weather } = mockHome
  const safe = getSafeArea()

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
        <View className="map-page__station">
          <View className="map-page__station-thumb">
            <Icon name="sun" size={14} color="#fff" />
          </View>
          <Text className="map-page__station-name">{station.name}</Text>
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
          latitude={station.latitude}
          longitude={station.longitude}
          scale={9}
          showLocation
          showScale
          enableSatellite={satellite}
          markers={[{
            id: 1,
            latitude: station.latitude,
            longitude: station.longitude,
            width: 24, height: 24,
            callout: {
              content: station.name, color: '#ffffff', bgColor: '#1677FF',
              padding: 6, borderRadius: 6, display: 'ALWAYS',
              fontSize: 12, textAlign: 'center',
            },
          }] as any}
          onError={(e) => console.error('[map] 加载失败', e)}
        />

        {/* 搜索框浮在地图顶部，拉满宽度 */}
        <View className="map-page__search">
          <Icon name="search" size={15} color="#9ca3af" />
          <Input
            className="map-page__search-input"
            placeholder="搜索城市 / 坐标 / 站点"
            placeholderClass="map-page__ph"
          />
        </View>

        <MapLayerControl value={layer} onChange={setLayer} />

        <View className="map-page__legend" style={{ bottom: `${SHEET_HEIGHT + 12}px` }}>
          <MapLegend spec={LEGENDS[layer]} />
        </View>

        <View className="map-page__tools" style={{ bottom: `${SHEET_HEIGHT + 12}px` }}>
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
          <View className="map-page__tool">
            <Icon name="crosshair" size={17} color="#1f2937" />
          </View>
          <View className="map-page__tool">
            <Icon name="plus" size={17} color="#1f2937" />
          </View>
          <View className="map-page__tool">
            <Icon name="minus" size={17} color="#1f2937" />
          </View>
        </View>

        {/* 底部面板叠在地图上 */}
        <View className="map-page__sheet" style={{ height: `${SHEET_HEIGHT}px` }}>
          <View className="map-page__handle" />
          <View
            className="map-page__sheet-head"
            onClick={() => Taro.navigateTo({ url: '/pages/station/detail?id=s1' })}
          >
            <Text className="map-page__sheet-name">{station.name}</Text>
            <StatusBadge status={station.status} />
            <View className="map-page__sheet-spacer" />
            <Icon name="chevronRight" size={15} color="#9ca3af" />
          </View>

          <MetricGrid>
            <MetricCard icon="leaf" iconColor="#16a34a" label="环境指数"
              metric={{ value: String(index.score), unit: '分' }}
              caption="良好" />
            <MetricCard icon="cloudSun" label="天气"
              metric={formatTemperature(weather.temperature.value)}
              caption={weather.weather_text} />
            <MetricCard icon="wind" iconFill={false} label="风速"
              metric={formatWindSpeed(weather.wind_speed.value)}
              deltaPercent={weather.wind_speed.delta_percent} />
            <MetricCard icon="sun" label="辐射"
              metric={formatRadiation(weather.radiation.value)}
              deltaPercent={weather.radiation.delta_percent} />
          </MetricGrid>

          <View
            className="map-page__hint"
            onClick={() => Taro.navigateTo({ url: '/pages/report/index' })}
          >
            <Icon name="barChart" size={14} color="#1677ff" />
            <Text className="map-page__hint-text">
              未来2小时整体适宜发电，14:30后云量逐步增加
            </Text>
            <Icon name="chevronRight" size={13} color="#9ca3af" />
          </View>
        </View>
      </View>
    </View>
  )
}
