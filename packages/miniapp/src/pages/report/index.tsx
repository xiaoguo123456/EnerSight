import { View, Text } from '@tarojs/components'
import Taro from '@tarojs/taro'
import {
  formatCo2, formatCoordinate, formatCurrency, formatEnergy, formatHours,
} from '@enersight/core/format'
import {
  DataSummaryGrid, Icon, PageHeader, SectionHeader, SuggestionList, TimelineAnalysis,
} from '@/components'
import type { SummaryCell } from '@/components'
import { mockReport } from '@/mocks'
import './index.scss'

export default function Report() {
  const r = mockReport
  const s = r.summary

  const cells: SummaryCell[] = [
    { icon: 'zap', tone: 'energy', label: '发电量',
      metric: formatEnergy(s.generation.value), deltaPercent: s.generation.delta_percent },
    { icon: 'clock', tone: 'primary', label: '等效利用小时',
      metric: formatHours(s.equivalent_hours.value), deltaPercent: s.equivalent_hours.delta_percent },
    { icon: 'leaf', tone: 'purple', label: 'CO₂减排',
      metric: formatCo2(s.co2_reduction.value), deltaPercent: s.co2_reduction.delta_percent },
    { icon: 'coins', tone: 'warning', label: '收益预估',
      metric: formatCurrency(s.estimated_revenue.value), deltaPercent: s.estimated_revenue.delta_percent },
  ]

  return (
    <View className="report">
      <PageHeader title="AI分析报告" subtitle="数据驱动绿色未来" />

      <View className="report__body">
        <View className="report__meta">
          <View className="report__station">
            <View className="report__thumb">
              <Icon name="sun" size={18} color="#ffffff" />
            </View>
            <View className="report__station-text">
              <Text className="report__station-name">{r.station.name}</Text>
              <View className="report__station-addr">
                <Icon name="mapPin" size={10} color="#9ca3af" />
                <Text className="report__station-addr-text">
                  {r.station.address}（{formatCoordinate(r.station.latitude, r.station.longitude)}）
                </Text>
              </View>
            </View>
          </View>
          <View className="report__dates">
            <Text className="report__date">报告日期：{r.report_date}</Text>
            <Text className="report__date">生成时间：{r.generated_at}</Text>
          </View>
        </View>

        {/* 综合判断：与首页指数卡同一视觉语言 */}
        <View className="report__verdict">
          <View className="report__verdict-head">
            <Text className="report__verdict-label">综合判断</Text>
            <Icon name="helpCircle" size={12} color="#9ca3af" />
          </View>
          <Text className="report__verdict-title">{r.verdict_title}</Text>
          <Text className="report__verdict-detail">{r.verdict_detail}</Text>
        </View>

        <View className="report__card">
          <SectionHeader icon="barChart" title="今日情况" action="查看详细分析" />
          <TimelineAnalysis periods={r.periods} />
        </View>

        <View className="report__risk">
          <View className="report__risk-head">
            <Icon name="alertTriangle" size={16} color="#f59e0b" />
            <Text className="report__risk-label">风险提醒</Text>
            <View
              className="report__risk-more"
              onClick={() => Taro.switchTab({ url: '/pages/alert/index' })}
            >
              <Text>查看更多</Text>
              <Icon name="chevronRight" size={12} color="#9ca3af" />
            </View>
          </View>
          <Text className="report__risk-title">{r.risk_title}</Text>
          <Text className="report__risk-detail">{r.risk_detail}</Text>
        </View>

        <View className="report__card">
          <SectionHeader icon="clipboard" iconColor="#16a34a" title="运营建议" />
          <SuggestionList items={r.suggestions} />
        </View>

        <View className="report__card">
          <SectionHeader icon="barChart" title="数据摘要" action="查看更多" />
          <DataSummaryGrid cells={cells} />
        </View>
      </View>
    </View>
  )
}
