import { View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useState } from 'react'
import {
  formatPercent, formatRadiation, formatTemperature, formatWindSpeed,
} from '@enersight/core/format'
import {
  AlertBanner, EnergyScoreCard, MetricCard, MetricGrid,
  QuickEntryGrid, SectionHeader, SegmentedTabs, StationTitleBar, TrendChart,
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
  { value: 'radiation', label: '辐射' },
  { value: 'wind_speed', label: '风速' },
  { value: 'cloud_cover', label: '云量' },
] as const
type TrendKey = (typeof TREND_TABS)[number]['value']

/**
 * 首页信息层级：
 *   顶栏     站点名 = 页面标题
 *   Hero     环境指数，全页唯一大字
 *   支撑     四宫格，紧贴 Hero，8px 间距把它们编成一组
 *   ── 16px ──
 *   趋势     次级内容
 *   预警     需要打断用户的，用左侧色条
 *   入口     辅助
 */
export default function Home() {
  const { station, index, weather, alert } = mockHome
  const [trend, setTrend] = useState<TrendKey>('radiation')

  return (
    <View className="home">
      <StationTitleBar
        name={station.name}
        status={station.status}
        address={station.address}
      />

      <View className="home__body">
        {/* Hero + 支撑数据编成一组 */}
        <View className="home__group">
          <EnergyScoreCard
            score={index.score}
            level={index.level}
            summary={index.summary}
          />
          <MetricGrid>
            <MetricCard icon="cloudSun" label="天气"
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

        <View className="home__card">
          <SectionHeader icon="trendingUp" title="24小时趋势" action="查看更多" />
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
