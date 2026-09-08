import { View, Text } from '@tarojs/components'
import Taro, { useRouter } from '@tarojs/taro'
import { useState } from 'react'
import {
  formatCoordinate, formatPercent, formatPower, formatRadiation,
  formatTemperature, formatWindSpeed,
} from '@enersight/core/format'
import type { TrendMetric, TrendSeries } from '@enersight/core/types'
import { homeApi } from '@/api/home'
import {
  EnergyScoreCard, ErrorState, Icon, MetricCard, MetricGrid, PageHeader,
  QuickEntryGrid, SectionHeader, SegmentedTabs, Skeleton, StatusBadge,
  TrendChart, fromTrendSeries,
} from '@/components'
import type { QuickEntry } from '@/components'
import { useRequest } from '@/hooks/useRequest'
import './detail.scss'

const TREND_TABS: { value: TrendMetric; label: string }[] = [
  { value: 'radiation', label: '辐射' },
  { value: 'wind_speed', label: '风速' },
  { value: 'cloud_cover', label: '云量' },
]

const ACTIONS: QuickEntry[] = [
  { icon: 'satellite', title: '卫星云图', subtitle: '实时云况监测', tone: 'primary',
    onTap: () => Taro.switchTab({ url: '/pages/alert/index' }) },
  { icon: 'fileText', title: 'AI分析报告', subtitle: '智能生成专业分析', tone: 'purple',
    onTap: () => Taro.navigateTo({ url: '/pages/report/index' }) },
]

export default function StationDetail() {
  const { params } = useRouter()
  const routeId = params.id ?? ''
  // 没带 id（含开发期直接作为启动页）时退回默认站点，与首页一致
  const req = useRequest(
    () => (routeId ? homeApi.detail(routeId) : homeApi.detailDefault()),
    [routeId],
  )
  const id = req.data?.station.id ?? routeId
  const [metric, setMetric] = useState<TrendMetric>('radiation')
  const [trendOverride, setTrendOverride] = useState<TrendSeries | null>(null)

  const switchTrend = async (m: TrendMetric) => {
    setMetric(m)
    if (m === 'radiation') { setTrendOverride(null); return }
    try { setTrendOverride(await homeApi.trends(id, m)) } catch { /* 保留上一条 */ }
  }

  if (req.status === 'loading') {
    return (
      <View className="detail">
        <PageHeader title="站点详情" />
        <View className="detail__body">
          <Skeleton height={110} lines={3} />
          <Skeleton height={130} lines={2} />
          <Skeleton height={150} lines={3} />
        </View>
      </View>
    )
  }
  if (req.status === 'error') {
    return (
      <View className="detail">
        <PageHeader title="站点详情" />
        <View className="detail__body"><ErrorState error={req.error} onRetry={req.reload} /></View>
      </View>
    )
  }

  const { station, weather, index, updated_at } = req.data
  const trend = trendOverride ?? req.data.trends
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
                  {station.address ?? '—'}（{formatCoordinate(station.latitude, station.longitude)}）
                </Text>
              </View>
              <View className="detail__station-spec">
                <Icon name={station.type === 'solar' ? 'sun' : 'wind'} size={12} color="#6b7280" />
                <Text className="detail__spec-text">{station.type === 'solar' ? '光伏' : '风电'}</Text>
                <View className="detail__spec-divider" />
                <Icon name="layers" size={12} color="#6b7280" />
                <Text className="detail__spec-text">装机容量 {cap.value} {cap.unit}</Text>
              </View>
            </View>
          </View>

          {weather && (
            <View className="detail__card">
              <View className="detail__weather-head">
                <SectionHeader icon="sun" iconColor="#f59e0b" title="当前天气" />
                <Text className="detail__updated">更新 {updated_at.slice(11, 16)}</Text>
              </View>
              <MetricGrid>
                <MetricCard icon="cloudSun" label="气温"
                  metric={formatTemperature(weather.temperature.value ?? 0)}
                  caption={weather.weather_text ?? undefined} />
                <MetricCard icon="wind" iconFill={false} label="风速"
                  metric={formatWindSpeed(weather.wind_speed.value ?? 0)}
                  deltaPercent={weather.wind_speed.delta_percent} />
                <MetricCard icon="cloud" label="云量"
                  metric={formatPercent(weather.cloud_cover.value ?? 0)}
                  deltaPercent={weather.cloud_cover.delta_percent} />
                <MetricCard icon="sun" label="辐射"
                  metric={formatRadiation(weather.radiation.value ?? 0)}
                  deltaPercent={weather.radiation.delta_percent} />
              </MetricGrid>
            </View>
          )}

          <EnergyScoreCard
            score={index?.score ?? null}
            level={index?.level ?? null}
            summary={index?.summary ?? null}
          />
        </View>

        {trend && (
          <View className="detail__card">
            <SectionHeader icon="trendingUp" title="24小时趋势" action="查看详情" />
            <SegmentedTabs options={TREND_TABS} value={metric} onChange={(v) => void switchTrend(v as TrendMetric)} />
            <View className="detail__chart">
              <TrendChart id="detail-trend" data={fromTrendSeries(trend)} />
            </View>
          </View>
        )}

        <View className="detail__card">
          <SectionHeader icon="grid" title="快捷操作" />
          <QuickEntryGrid entries={ACTIONS} />
        </View>
      </View>
    </View>
  )
}
