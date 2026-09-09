import { View } from '@tarojs/components'
import Taro, { useDidShow } from '@tarojs/taro'
import { useEffect, useState } from 'react'
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
  { icon: 'satellite', title: '卫星云图', subtitle: '实时云况监测', tone: 'primary',
    onTap: () => Taro.switchTab({ url: '/pages/alert/index' }) },
  { icon: 'fileText', title: 'AI分析报告', subtitle: '智能生成专业分析', tone: 'purple',
    onTap: () => Taro.navigateTo({ url: '/pages/report/index' }) },
  { icon: 'settings', title: '电站目录', subtitle: '浏览全部公开电站', tone: 'cyan',
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
  // 切换电站时首页可能隐藏；显示后重建画布，避免在零尺寸布局上初始化。
  const [chartVersion, setChartVersion] = useState(0)
  useDidShow(() => setChartVersion((v) => v + 1))
  const currentId = useStationStore((s) => s.currentId)
  const home = useRequest(() => homeApi.get(currentId ?? undefined), [currentId])

  const [metric, setMetric] = useState<TrendMetric>('radiation')
  // 首屏用 home 带回的辐射趋势；切 Tab 后单独拉
  const [trendOverride, setTrendOverride] = useState<TrendSeries | null>(null)

  useEffect(() => { setMetric('radiation'); setTrendOverride(null) }, [currentId])

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
  const trend = trendOverride ?? d.trends

  return (
    <View className="home">
      <StationTitleBar
        onSwitch={() => Taro.switchTab({ url: '/pages/station/index' })}
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
              <TrendChart key={`${station.id}-${chartVersion}`} id="home-trend" data={fromTrendSeries(trend)} />
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
