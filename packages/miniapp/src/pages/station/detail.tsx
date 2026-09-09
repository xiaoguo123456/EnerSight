import { View, Text } from '@tarojs/components'
import Taro, { useRouter } from '@tarojs/taro'
import { useEffect, useState } from 'react'
import {
  formatCoordinate, formatPercent, formatPower, formatRadiation,
  formatTemperature, formatWindSpeed,
} from '@enersight/core/format'
import type { TrendMetric, TrendSeries } from '@enersight/core/types'
import { useStationStore } from '@/store'
import { homeApi } from '@/api/home'
import {
  EnergyScoreCard, ErrorState, Icon, MetricCard, MetricGrid, PageHeader,
  SectionHeader, SegmentedTabs, Skeleton, StatusBadge,
  TrendChart, fromTrendSeries,
} from '@/components'
import { useRequest } from '@/hooks/useRequest'
import { decodeRouteParam } from '@/route'
import './detail.scss'

const TREND_TABS: { value: TrendMetric; label: string }[] = [
  { value: 'radiation', label: '辐射' },
  { value: 'wind_speed', label: '风速' },
  { value: 'cloud_cover', label: '云量' },
]

export default function StationDetail() {
  const { params } = useRouter()
  const routeId = decodeRouteParam(params.id)
  // 没带 id（含开发期直接作为启动页）时退回默认站点，与首页一致
  const req = useRequest(
    () => (routeId ? homeApi.detail(routeId) : homeApi.detailDefault()),
    [routeId],
  )
  const id = req.data?.station.id ?? routeId
  const remember = useStationStore((s) => s.remember)
  useEffect(() => { if (req.data?.station) remember(req.data.station) }, [req.data, remember])
  const setCurrent = useStationStore((s) => s.setCurrent)
  useEffect(() => {
    if (req.data?.station) setCurrent(req.data.station.id)
  }, [req.data, setCurrent])
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
            <View className="detail__station-spec">
              <Icon name={station.type === 'solar' ? 'sun' : 'wind'} size={16} color="#64748b" />
              <Text>{station.type === 'solar' ? '光伏电站' : '风力电站'}</Text>
              <StatusBadge status={station.status} />
            </View>
            <Text className="detail__station-name">{station.name}</Text>
            <View className="detail__capacity">
              <Text className="detail__capacity-label">装机容量</Text>
              <Text className="detail__capacity-value">{cap.value}<Text className="detail__capacity-unit"> {cap.unit}</Text></Text>
            </View>
            <View className="detail__actions">
              <View className="detail__action detail__action--primary" onClick={() => Taro.navigateTo({ url: `/pages/report/index?id=${encodeURIComponent(id)}` })}>
                <Icon name="fileText" size={16} color="#1264d6" /><Text>分析报告</Text>
              </View>
              <View className="detail__action" onClick={() => Taro.switchTab({ url: '/pages/map/index' })}>
                <Icon name="map" size={16} color="#475569" /><Text>地图查看</Text>
              </View>
            </View>
          </View>

          <EnergyScoreCard
            score={index?.score ?? null}
            level={index?.level ?? null}
            summary={index?.summary ?? null}
          />
        </View>

        {trend && (
          <View className="detail__card">
            <SectionHeader icon="trendingUp" title="24 小时气象趋势" />
            <SegmentedTabs options={TREND_TABS} value={metric} onChange={(v) => void switchTrend(v as TrendMetric)} />
            <View className="detail__chart">
              <TrendChart id="detail-trend" data={fromTrendSeries(trend)} />
            </View>
          </View>
        )}

        {weather && (
          <View className="detail__card">
            <View className="detail__weather-head">
              <SectionHeader icon="sun" iconColor="#f59e0b" title="当前气象" />
              <Text className="detail__updated">更新 {updated_at.slice(5, 10)} {updated_at.slice(11, 16)}</Text>
            </View>
            <MetricGrid>
              <MetricCard icon="cloudSun" label="气温"
                metric={formatTemperature(weather.temperature.value)}
                caption={weather.weather_text ?? undefined} />
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
        )}

        <View className="detail__card">
          <SectionHeader icon="mapPin" title="电站资料" />
          <View className="detail__info-row"><Text className="detail__info-label">所在地区</Text><Text className="detail__info-value">{station.address || '暂无地区信息'}</Text></View>
          <View className="detail__info-row"><Text className="detail__info-label">地理坐标</Text><Text className="detail__info-value">{formatCoordinate(station.latitude, station.longitude)}</Text></View>
          <Text className="detail__note">电站资料来自公开目录，气象与发电适宜度为模型估算。</Text>
        </View>
      </View>
    </View>
  )
}
