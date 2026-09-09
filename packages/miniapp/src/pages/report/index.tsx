import { View, Text } from '@tarojs/components'
import Taro, { useRouter, usePullDownRefresh, useShareAppMessage } from '@tarojs/taro'
import {
  formatBeijingTime, formatCo2, formatCurrency, formatEnergy, formatHours,
} from '@enersight/core/format'
import { homeApi } from '@/api/home'
import { reportsApi } from '@/api/reports'
import {
  DataSummaryGrid, ErrorState, Icon, PageHeader, SectionHeader, Skeleton,
  SuggestionList, TimelineAnalysis,
} from '@/components'
import type { SummaryCell } from '@/components'
import { useRequest } from '@/hooks/useRequest'
import { decodeRouteParam } from '@/route'
import { useStationStore } from '@/store'
import { DataFreshness } from '@/components/DataFreshness'
import './index.scss'

export default function Report() {
  const { params } = useRouter()
  const currentId = useStationStore((st) => st.currentId)
  const routeId = decodeRouteParam(params.id) || currentId || ''

  // 没带 id 时先取默认站点再拉报告
  const req = useRequest(async () => {
    const sid = routeId || (await homeApi.get()).station?.id
    if (!sid) throw new Error('no station')
    return reportsApi.get(sid)
  }, [routeId])

  usePullDownRefresh(async () => { await req.reload(); Taro.stopPullDownRefresh() })
  useShareAppMessage(() => ({ title: `${req.data?.station.name || '晴川观象'} · 气象分析`, path: `/pages/report/index?id=${encodeURIComponent(req.data?.station.id || routeId)}` }))
  const setCurrent = useStationStore((s) => s.setCurrent)
  if (req.status === 'loading') {
    return (
      <View className="report">
        <PageHeader title="分析报告" />
        <View className="report__body">
          <Skeleton height={90} lines={2} />
          <Skeleton height={130} lines={3} />
          <Skeleton height={160} lines={3} />
        </View>
      </View>
    )
  }
  if (req.status === 'error') {
    return (
      <View className="report">
        <PageHeader title="分析报告" />
        <View className="report__body"><ErrorState error={req.error} onRetry={req.reload} /></View>
      </View>
    )
  }

  const r = req.data
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
      <PageHeader title="分析报告" />

      <View className="report__body">
        <View className="report__meta">
          <Text className="report__eyebrow">每日气象分析 · {r.report_date}</Text>
          <Text className="report__station-name">{r.station.name}</Text>
          {r.station.address && <Text className="report__station-addr-text">{r.station.address}</Text>}
          <Text className="report__method">{r.method === 'ai' ? 'AI 辅助分析' : '规则分析'} · 气象模型估算，非实测</Text>
        </View>

        <DataFreshness label="报告生成" time={r.generated_at_iso} staleMinutes={20} refreshing={req.refreshing} failed={!!req.refreshError} onRefresh={req.reload} />
        {r.data_as_of && <Text className="report__data-note">气象数据截至 {formatBeijingTime(r.data_as_of)}（北京时间）</Text>}
        {/* 先给结论，再提供风险与分时依据。 */}
        <View className="report__verdict">
          <View className="report__verdict-head">
            <Text className="report__verdict-label">综合判断</Text>
          </View>
          <Text className="report__verdict-title">{r.verdict_title}</Text>
          <Text className="report__verdict-detail">{r.verdict_detail}</Text>
        </View>

        {r.risk_title && (
          <View className="report__risk">
            <View className="report__risk-head">
              <Icon name="alertTriangle" size={16} color="#a16207" />
              <Text className="report__risk-label">风险提醒</Text>
              <View
                className="report__risk-more"
                onClick={() => { setCurrent(r.station.id); Taro.switchTab({ url: '/pages/alert/index' }) }}
              >
                <Text>查看预警</Text>
                <Icon name="chevronRight" size={12} color="#9ca3af" />
              </View>
            </View>
            <Text className="report__risk-title">{r.risk_title}</Text>
            {r.risk_detail && <Text className="report__risk-detail">{r.risk_detail}</Text>}
          </View>
        )}

        <View className="report__card">
          <SectionHeader icon="barChart" title="分时分析" />
          <TimelineAnalysis periods={r.periods} />
        </View>

        <View className="report__card">
          <SectionHeader icon="clipboard" iconColor="#64748b" title="运营建议" />
          <SuggestionList items={r.suggestions} />
        </View>

        <View className="report__card">
          <SectionHeader icon="barChart" title="估算数据" />
          <DataSummaryGrid cells={cells} />
          <Text className="report__data-note">发电量与收益为模型估算，非实际结算。电价假设：{r.tariff_yuan_per_kwh ?? '未记录'} 元/kWh；减排系数：{r.co2_factor_kg_per_kwh ?? '未记录'} kg/kWh。</Text>
        </View>
        <Text className="report__generated">生成时间：{r.generated_at_iso ? `${formatBeijingTime(r.generated_at_iso)}（北京时间）` : r.generated_at}</Text>
      </View>
    </View>
  )
}
