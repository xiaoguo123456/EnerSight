import { View, Text } from '@tarojs/components'
import Taro, { useDidShow, usePullDownRefresh } from '@tarojs/taro'
import { useState } from 'react'
import {
  formatPercent, formatRadiation, formatTemperature, formatWindSpeed,
} from '@enersight/core/format'
import { homeApi } from '@/api/home'
import {
  AlertBanner, EmptyState, EnergyScoreCard, ErrorState, MetricCard, MetricGrid,
  QuickEntryGrid, SectionHeader, Skeleton, StationTitleBar,
} from '@/components'
import type { QuickEntry } from '@/components'
import { useRequest } from '@/hooks/useRequest'
import { useStationStore } from '@/store'
import { StationTrend } from '@/components/StationTrend'
import { DataFreshness } from '@/components/DataFreshness'
import { useWeatherModel, weatherModelLabel } from '@/store/weatherModel'
import './index.scss'

const ENTRIES: QuickEntry[] = [
  { icon: 'map', title: '地图总览', subtitle: '宏观掌握区域情况', tone: 'primary',
    onTap: () => Taro.switchTab({ url: '/pages/map/index' }) },
  { icon: 'satellite', title: '卫星云图', subtitle: '实时云况监测', tone: 'primary',
    onTap: () => Taro.switchTab({ url: '/pages/alert/index' }) },
  { icon: 'fileText', title: '分析报告', subtitle: '查看气象分析', tone: 'primary',
    onTap: () => Taro.navigateTo({ url: '/pages/report/index' }) },
  { icon: 'factory', title: '电站目录', subtitle: '浏览全部公开电站', tone: 'primary',
    onTap: () => Taro.switchTab({ url: '/pages/station/index' }) },
]

/**
 * 首页信息层级：
 *   顶栏     站点名 = 页面标题
 *   摘要     发电适宜度与气象说明
 *   支撑     当前气象指标
 *   趋势 / 预警 / 入口
 *
 * 一个 /v1/home 请求覆盖首屏；切趋势 Tab 才再请求 /v1/trends。
 */
export default function Home() {
  // 切换电站时首页可能隐藏；显示后重建画布，避免在零尺寸布局上初始化。
  const [chartVersion, setChartVersion] = useState(0)
  useDidShow(() => setChartVersion((v) => v + 1))
  const model = useWeatherModel(s => s.model)
  const currentId = useStationStore((s) => s.currentId)
  const home = useRequest(() => homeApi.get(currentId ?? undefined), [currentId])

  usePullDownRefresh(async () => { await home.reload(); Taro.stopPullDownRefresh() })

  if (home.status === 'loading') {
    return (
      <View className="home">
        <View className="home__body" style={{ paddingTop: '96px' }}>
          <Skeleton height={150} lines={3} />
          <Skeleton height={90} lines={2} />
          <Skeleton height={220} lines={4} />
        </View>
      </View>
    )
  }

  if (home.status === 'error') {
    return (
      <View className="home">
        <View className="home__body" style={{ paddingTop: '96px' }}>
          <ErrorState error={home.error} onRetry={home.reload} />
        </View>
      </View>
    )
  }

  const d = home.data
  if (!d.has_station || !d.station) {
    return (
      <View className="home">
        <View className="home__body" style={{ paddingTop: '96px' }}>
          <EmptyState
            icon="mapPin"
            title="目录暂无电站"
            description="平台更新目录后即可查看发电环境"
            actionText="查看目录"
            onAction={() => Taro.switchTab({ url: '/pages/station/index' })}
          />
        </View>
      </View>
    )
  }

  const { station, index, weather, alert } = d

  return (
    <View className="home">
      <StationTitleBar
        onSwitch={() => Taro.switchTab({ url: '/pages/station/index' })}
        name={station.name}
        status={station.status}
        address={station.address ?? '—'}
      />

      <View className="home__body">
        <DataFreshness label={`气象预报 · ${weatherModelLabel(model)}`} time={weather?.observed_at} refreshing={home.refreshing} failed={!!home.refreshError} onRefresh={home.reload} />
        {!currentId && <Text className="home__data-note" onClick={() => Taro.switchTab({ url: '/pages/station/index' })}>当前为目录示例电站，点击选择关注的电站</Text>}
        <View className="home__detail-link" onClick={() => Taro.navigateTo({ url: `/pages/station/detail?id=${encodeURIComponent(station.id)}` })}>查看完整电站资料 ›</View>
        <View className="home__group">
          <EnergyScoreCard
            score={index?.score ?? null}
            level={index?.level ?? null}
            summary={index?.summary ?? null}
          />
          {weather && (
            <MetricGrid>
              <MetricCard icon="cloudSun" label="天气"
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
          )}
        </View>

        <View className="home__card"><StationTrend key={station.id} stationId={station.id} type={station.type} initial={d.trends} version={chartVersion} /></View>

        {alert && (
          <AlertBanner
            title={alert.title}
            description={alert.description}
            onMore={() => Taro.switchTab({ url: '/pages/alert/index' })}
          />
        )}

        <View className="home__card">
          <SectionHeader icon="grid" title="快捷入口" />
          <QuickEntryGrid entries={ENTRIES} />
        </View>
      </View>
    </View>
  )
}
