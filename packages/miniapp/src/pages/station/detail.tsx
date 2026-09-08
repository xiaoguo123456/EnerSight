import { View, Text } from '@tarojs/components'
import Taro, { useRouter } from '@tarojs/taro'
import { useState } from 'react'
import {
  formatCoordinate, formatPercent, formatPower, formatRadiation,
  formatTemperature, formatWindSpeed,
} from '@enersight/core/format'
import {
  EnergyScoreCard, Icon, MetricCard, MetricGrid, PageHeader,
  QuickEntryGrid, SectionHeader, SegmentedTabs, StatusBadge, TrendChart,
} from '@/components'
import type { QuickEntry } from '@/components'
import { mockHome, mockStations } from '@/mocks'
import './detail.scss'

const TREND_TABS = [
  { value: 'radiation', label: '辐射' },
  { value: 'wind_speed', label: '风速' },
  { value: 'cloud_cover', label: '云量' },
] as const
type TrendKey = (typeof TREND_TABS)[number]['value']

const ACTIONS: QuickEntry[] = [
  { icon: 'satellite', title: '卫星云图', subtitle: '实时云况监测', tone: 'primary' },
  { icon: 'fileText', title: 'AI分析报告', subtitle: '智能生成专业分析', tone: 'purple',
    onTap: () => Taro.navigateTo({ url: '/pages/report/index' }) },
]

export default function StationDetail() {
  const { params } = useRouter()
  const station = mockStations.find((s) => s.id === params.id) ?? mockStations[0]!
  const { index, weather } = mockHome
  const [trend, setTrend] = useState<TrendKey>('radiation')

  const cap = formatPower(station.capacity)

  return (
    <View className="detail">
      <PageHeader title="站点详情" />

      <View className="detail__body">
        <View className="detail__group">
        <View className="detail__station">
          <View className={`detail__thumb detail__thumb--${station.type}`}>
            <Icon name={station.type === 'solar' ? 'sun' : 'wind'} size={20} color="#fff" />
          </View>
          <View className="detail__station-info">
            <View className="detail__station-row">
              <Text className="detail__station-name">{station.name}</Text>
              <StatusBadge status={station.status} />
            </View>
            <View className="detail__station-addr">
              <Icon name="mapPin" size={11} color="#9ca3af" />
              <Text className="detail__station-addr-text">
                {station.address}（{formatCoordinate(station.latitude, station.longitude)}）
              </Text>
            </View>
            <View className="detail__station-spec">
              <Icon name={station.type === 'solar' ? 'sun' : 'wind'} size={12} color="#6b7280" />
              <Text className="detail__spec-text">
                {station.type === 'solar' ? '光伏' : '风电'}
              </Text>
              <View className="detail__spec-divider" />
              <Icon name="layers" size={12} color="#6b7280" />
              <Text className="detail__spec-text">
                装机容量 {cap.value} {cap.unit}
              </Text>
            </View>
          </View>
        </View>

        <View className="detail__card">
          <View className="detail__weather-head">
            <SectionHeader icon="sun" iconColor="#f59e0b" title="当前天气" />
            <Text className="detail__updated">数据更新时间：2026-09-07 09:41</Text>
          </View>
          <MetricGrid>
            <MetricCard icon="cloudSun" label="气温"
              metric={formatTemperature(weather.temperature.value)}
              caption={weather.weather_text} />
            <MetricCard icon="wind" iconFill={false} label="风速"
              metric={formatWindSpeed(weather.wind_speed.value)}
              deltaPercent={weather.wind_speed.delta_percent} />
            <MetricCard icon="cloud" label="云量"
              metric={formatPercent(weather.cloud_cover.value)}
              deltaPercent={weather.cloud_cover.delta_percent} />
            <MetricCard icon="sun" label="辐射"
              metric={formatRadiation(weather.radiation.value)}
              deltaPercent={weather.radiation.delta_percent} />
          </MetricGrid>
        </View>

        <EnergyScoreCard score={index.score} level={index.level} summary={index.summary} />
        </View>

        <View className="detail__card">
          <SectionHeader icon="trendingUp" title="24小时趋势" action="查看详情" />
          <SegmentedTabs
            options={TREND_TABS as unknown as { value: string; label: string }[]}
            value={trend}
            onChange={(v) => setTrend(v as TrendKey)}
          />
          <View className="detail__chart">
            <TrendChart id="detail-trend" data={mockHome.trends[trend]} />
          </View>
        </View>

        <View className="detail__card">
          <SectionHeader icon="grid" title="快捷操作" />
          <QuickEntryGrid entries={ACTIONS} />
        </View>
      </View>
    </View>
  )
}
