import { Map, View, Text, Input, CoverView, CoverImage } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useState } from 'react'
import {
  formatPercent, formatRadiation, formatTemperature, formatWindSpeed,
} from '@enersight/core/format'
import {
  AppHeader, Icon, MapLayerControl, MapLegend, MetricCard, MetricGrid,
  StatusBadge,
} from '@/components'
import type { LegendSpec, MapLayer } from '@/components'
import { ICON_PATHS, type IconName } from '@/components/Icon/paths'
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
  station: { title: '站点', colors: ['#16a34a', '#1677ff'], labels: ['正常', '待机'] },
}

function iconUri(name: IconName, color: string): string {
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" ` +
    `stroke="${color}" stroke-width="2" stroke-linecap="round" ` +
    `stroke-linejoin="round">${ICON_PATHS[name]}</svg>`
  return `data:image/svg+xml,${encodeURIComponent(svg)}`
}

export default function MapPage() {
  const [layer, setLayer] = useState<MapLayer>('cloud')
  const { station, index, weather } = mockHome

  return (
    <View className="map-page">
      <AppHeader title="新能源地图" subtitle="数据驱动绿色未来" />

      <View className="map-page__search">
        <View className="map-page__station">
          <View className="map-page__station-thumb">
            <Icon name="sun" size={15} color="#fff" />
          </View>
          <View className="map-page__station-text">
            <Text className="map-page__station-name">{station.name}</Text>
            <Text className="map-page__station-addr">{station.address}</Text>
          </View>
          <Icon name="chevronDown" size={12} color="#9ca3af" />
        </View>
        <View className="map-page__input">
          <Icon name="search" size={14} color="#9ca3af" />
          <Input
            className="map-page__input-el"
            placeholder="搜索城市 / 坐标 / 站点"
            placeholderClass="map-page__ph"
          />
        </View>
      </View>

      {/*
        map 是原生组件，普通 View 盖不住它 —— 浮层一律用 CoverView。
        底部面板不与地图重叠，避免层级问题（设计稿的轻微重叠需
        Skyline 同层渲染才能实现，见 docs/05 §6.7 spike 第 3、4 项）。
      */}
      <View className="map-page__canvas">
        <Map
          id="main-map"
          className="map-page__map"
          latitude={station.latitude}
          longitude={station.longitude}
          scale={7}
          showLocation
          markers={[{
            id: 1,
            latitude: station.latitude,
            longitude: station.longitude,
            width: 22, height: 22,
            callout: {
              content: station.name, color: '#ffffff', bgColor: '#1677FF',
              padding: 6, borderRadius: 6, display: 'ALWAYS',
              fontSize: 11, textAlign: 'center',
            },
          }] as any}
          onError={(e) => console.error('[map] 加载失败', e)}
        >
          <MapLayerControl value={layer} onChange={setLayer} />
          <MapLegend spec={LEGENDS[layer]} />

          <CoverView className="map-page__tools">
            <CoverView className="map-page__tool">
              <CoverImage className="map-page__tool-icon" src={iconUri('crosshair', '#1f2937')} />
            </CoverView>
            <CoverView className="map-page__tool">
              <CoverImage className="map-page__tool-icon" src={iconUri('plus', '#1f2937')} />
            </CoverView>
            <CoverView className="map-page__tool">
              <CoverImage className="map-page__tool-icon" src={iconUri('minus', '#1f2937')} />
            </CoverView>
          </CoverView>
        </Map>
      </View>

      <View className="map-page__sheet">
        <View className="map-page__handle" />
        <View className="map-page__sheet-head">
          <Text className="map-page__sheet-name">{station.name}</Text>
          <Icon name="chevronRight" size={13} color="#9ca3af" />
          <StatusBadge status={station.status} />
        </View>

        <MetricGrid>
          <MetricCard icon="leaf" iconColor="#16a34a" label="环境指数"
            metric={{ value: String(index.score), unit: '分' }}
            caption={index.level === 'good' ? '良好' : '—'} />
          <MetricCard icon="cloudSun" iconColor="#f59e0b" label="天气"
            metric={formatTemperature(weather.temperature.value)}
            caption={weather.weather_text} />
          <MetricCard icon="wind" iconColor="#1677ff" label="风速"
            metric={formatWindSpeed(weather.wind_speed.value)}
            deltaPercent={weather.wind_speed.delta_percent} />
          <MetricCard icon="sun" iconColor="#f97316" label="辐射"
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
  )
}
