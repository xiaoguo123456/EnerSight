import { View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useState } from 'react'
import {
  formatPercent, formatRadiation, formatTemperature, formatWindSpeed,
} from '@enersight/core/format'
import type { TrendMetric, TrendSeries } from '@enersight/core/types'
import { homeApi } from '@/api/home'
import {
  AlertBanner, EmptyState, EnergyScoreCard, ErrorState, MetricCard, MetricGrid,
  QuickEntryGrid, SectionHeader, SegmentedTabs, Skeleton, StationTitleBar,
  TrendChart, fromTrendSeries,
} from '@/components'
import type { QuickEntry } from '@/components'
import { useRequest } from '@/hooks/useRequest'
import { useStationStore } from '@/store'
import './index.scss'

const ENTRIES: QuickEntry[] = [
  { icon: 'map', title: '地图总览', subtitle: '宏观掌握区域情况', tone: 'energy',
    onTap: () => Taro.switchTab({ url: '/pages/map/index' }) },
  { icon: 'satellite', title: '卫星云图', subtitle: '实时云况监测', tone: 'primary' },
  { icon: 'fileText', title: 'AI分析报告', subtitle: '智能生成专业分析', tone: 'purple',
    onTap: () => Taro.navigateTo({ url: '/pages/report/index' }) },
  { icon: 'settings', title: '站点管理', subtitle: '站点信息与设备', tone: 'cyan',
    onTap: () => Taro.switchTab({ url: '/pages/station/index' }) },
]

const TREND_TABS: { value: TrendMetric; label: string }[] = [
  { value: 'radiation', label: '辐射' },
  { value: 'wind_speed', label: '风速' },
  { value: 'cloud_cover', label: '云量' },
]

/**
 * 首页信息层级：
 *   顶栏     站点名 = 页面标题
 *   Hero     环境指数，全页唯一大字
 *   支撑     四宫格，紧贴 Hero
 *   趋势 / 预警 / 入口
 *
 * 一个 /v1/home 请求覆盖首屏；切趋势 Tab 才再请求 /v1/trends。
 */
export default function Home() {
  const currentId = useStationStore((s) => s.currentId)
  const home = useRequest(() => homeApi.get(currentId ?? undefined), [currentId])

  const [metric, setMetric] = useState<TrendMetric>('radiation')
  // 首屏用 home 带回的辐射趋势；切 Tab 后单独拉
  const [trendOverride, setTrendOverride] = useState<TrendSeries | null>(null)

  const switchTrend = async (m: TrendMetric) => {
    setMetric(m)
    const sid = home.data?.station?.id
    if (!sid) return
    if (m === 'radiation' && home.data?.trends) {
      setTrendOverride(null)
      return
    }
    try {
      setTrendOverride(await homeApi.trends(sid, m))
    } catch {
      // 趋势切换失败不打断页面，保留上一条曲线
    }
  }

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
            title="还没有站点"
            description="添加第一个站点，开始查看发电环境"
            actionText="去添加"
            onAction={() => Taro.switchTab({ url: '/pages/station/index' })}
          />
        </View>
      </View>
    )
  }

  const { station, index, weather, alert } = d
  const trend = trendOverride ?? d.trends

  return (
    <View className="home">
      <StationTitleBar
        name={station.name}
        status={station.status}
        address={station.address ?? '—'}
      />

      <View className="home__body">
        <View className="home__group">
          <EnergyScoreCard
            score={index?.score ?? null}
            level={index?.level ?? null}
            summary={index?.summary ?? null}
          />
          {weather && (
            <MetricGrid>
              <MetricCard icon="cloudSun" label="天气"
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
          )}
        </View>

        {trend && (
          <View className="home__card">
            <SectionHeader icon="trendingUp" title="24小时趋势" action="查看更多" />
            <SegmentedTabs options={TREND_TABS} value={metric} onChange={(v) => void switchTrend(v as TrendMetric)} />
            <View className="home__chart">
              <TrendChart id="home-trend" data={fromTrendSeries(trend)} />
            </View>
          </View>
        )}

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
