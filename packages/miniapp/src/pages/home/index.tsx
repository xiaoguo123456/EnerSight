import { View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import {
  formatPercent, formatRadiation, formatTemperature, formatWindSpeed,
} from '@enersight/core/format'
import { useState } from 'react'
import {
  AlertBanner, AppHeader, EnergyScoreCard, MetricCard, MetricGrid,
  QuickEntryGrid, SectionHeader, SegmentedTabs, StationSelector, TrendChart,
} from '@/components'
import type { QuickEntry } from '@/components'
import { mockHome } from '@/mocks/home'
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

const TREND_TABS = [
  { value: 'radiation', label: '辐射（W/m²）' },
  { value: 'wind_speed', label: '风速（m/s）' },
  { value: 'cloud_cover', label: '云量（%）' },
] as const

type TrendKey = (typeof TREND_TABS)[number]['value']

export default function Home() {
  const { station, index, weather, alert } = mockHome
  const [trend, setTrend] = useState<TrendKey>('radiation')

  return (
    <View className="home">
      <AppHeader title="AI新能源气象遥感分析平台" subtitle="数据驱动绿色未来" />

      <View className="home__body">
        <StationSelector {...station} />

        <EnergyScoreCard
          score={index.score}
          level={index.level}
          summary={index.summary}
        />

        <MetricGrid>
          <MetricCard
            icon="cloudSun" iconColor="#f59e0b" label="天气"
            metric={formatTemperature(weather.temperature.value)}
            caption={weather.weather_text}
          />
          <MetricCard
            icon="wind" iconColor="#1677ff" label="风速"
            metric={formatWindSpeed(weather.wind_speed.value)}
            deltaPercent={weather.wind_speed.delta_percent}
          />
          <MetricCard
            icon="cloud" iconColor="#60a5fa" label="云量"
            metric={formatPercent(weather.cloud_cover.value)}
            deltaPercent={weather.cloud_cover.delta_percent}
          />
          <MetricCard
            icon="sun" iconColor="#f97316" label="辐射"
            metric={formatRadiation(weather.radiation.value)}
            deltaPercent={weather.radiation.delta_percent}
          />
        </MetricGrid>

        <View className="home__card">
          <SectionHeader
            icon="trendingUp" title="24小时趋势" action="查看更多"
          />
          <SegmentedTabs
            options={TREND_TABS as unknown as { value: string; label: string }[]}
            value={trend}
            onChange={(v) => setTrend(v as TrendKey)}
          />
          <View className="home__chart">
            <TrendChart id="home-trend" data={mockHome.trends[trend]} />
          </View>
        </View>

        <AlertBanner
          title={alert.title}
          description={alert.description}
          onMore={() => Taro.switchTab({ url: '/pages/alert/index' })}
        />

        <View className="home__card">
          <SectionHeader icon="grid" title="快捷入口" />
          <QuickEntryGrid entries={ENTRIES} />
        </View>
      </View>
    </View>
  )
}
