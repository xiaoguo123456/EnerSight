import { View, Text, Button } from '@tarojs/components'
import Taro, { useRouter, usePullDownRefresh } from '@tarojs/taro'
import { useEffect } from 'react'
import {
  formatBeijingTime, formatCoordinate, formatPercent, formatPower, formatRadiation,
  formatTemperature, formatWindSpeed,
} from '@enersight/core/format'
import { useStationStore } from '@/store'
import { homeApi } from '@/api/home'
import {
  EnergyScoreCard, ErrorState, Icon, MetricCard, MetricGrid, PageHeader,
  SectionHeader, Skeleton, StatusBadge,
} from '@/components'
import { useRequest } from '@/hooks/useRequest'
import { decodeRouteParam } from '@/route'
import { StationTrend } from '@/components/StationTrend'
import { useWeatherModel, weatherModelLabel } from '@/store/weatherModel'
import { DataFreshness } from '@/components/DataFreshness'
import './detail.scss'

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
  const favorites = useStationStore((s) => s.favorites)
  const toggleFavorite = useStationStore((s) => s.toggleFavorite)
  const currentId = useStationStore((s) => s.currentId)
  usePullDownRefresh(async () => { await req.reload(); Taro.stopPullDownRefresh() })
  const selectStation = () => { setCurrent(id); void Taro.showToast({ title: '已设为当前电站', icon: 'success' }) }

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
              <Text className="detail__capacity-label">目录装机容量</Text>
              <Text className="detail__capacity-value">{cap.value}<Text className="detail__capacity-unit"> {cap.unit}</Text></Text>
            </View>
            <View className="detail__favorite" onClick={() => toggleFavorite(station)}>{favorites.some((s) => s.id === id) ? '已收藏 · 点击取消' : '收藏到常看电站'}</View>
            <View className="detail__selection" onClick={selectStation}>{currentId === id ? '✓ 当前查看电站' : '设为当前电站'}</View>
            <View className="detail__actions">
              <View className="detail__action detail__action--primary" onClick={() => Taro.navigateTo({ url: `/pages/report/index?id=${encodeURIComponent(id)}` })}>
                <Icon name="fileText" size={16} color="#1264d6" /><Text>分析报告</Text>
              </View>
              <View className="detail__action" onClick={() => { setCurrent(id); Taro.switchTab({ url: '/pages/map/index' }) }}>
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

        <DataFreshness label={`气象预报 · ${weatherModelLabel(useWeatherModel.getState().model)}`} time={updated_at} refreshing={req.refreshing} failed={!!req.refreshError} onRefresh={req.reload} />
        <View className="detail__card"><StationTrend key={id} stationId={id} type={station.type} initial={req.data.trends} /></View>

        {weather && (
          <View className="detail__card">
            <View className="detail__weather-head">
              <SectionHeader icon="sun" iconColor="#f59e0b" title="当前气象" />

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
          <View className="detail__info-row"><Text className="detail__info-label">原始名称</Text><Text className="detail__info-value">{station.original_name || station.name}</Text></View>
          {station.local_name && <View className="detail__info-row"><Text className="detail__info-label">中文名称</Text><Text className="detail__info-value">{station.local_name}</Text></View>}
          <View className="detail__info-row"><Text className="detail__info-label">资料来源</Text><Text className="detail__info-value">{station.source === 'gem' ? 'Global Energy Monitor' : station.source === 'wri' ? 'WRI 全球电站数据库' : '来源待核实'}</Text></View>
          <View className="detail__info-row"><Text className="detail__info-label">目录入库</Text><Text className="detail__info-value">{formatBeijingTime(station.catalog_updated_at)}（北京时间，非源数据发布日期）</Text></View>
          {station.owner_name && <View className="detail__info-row"><Text className="detail__info-label">业主</Text><Text className="detail__info-value">{station.owner_name}</Text></View>}
          <View className="detail__actions">
            <View className="detail__action" onClick={() => Taro.setClipboardData({ data: `电站：${station.name}\nID：${id}\n地区：${station.address || '暂无'}\n坐标：${formatCoordinate(station.latitude, station.longitude)}\n来源：${station.source || '待核实'}\n页面：站点详情\n反馈时间：${new Date().toISOString()}` })}>复制资料</View>
            <Button className="detail__action detail__feedback" openType="feedback">资料纠错</Button>
          </View>
          <Text className="detail__note">反馈前可复制资料，附上需要更正的字段和来源。</Text>
          <Text className="detail__note">电站资料来自公开目录，气象与发电适宜度为模型估算。</Text>
        </View>
      </View>
    </View>
  )
}
