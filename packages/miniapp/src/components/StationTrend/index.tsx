import { View } from '@tarojs/components'
import { useState } from 'react'
import type { TrendMetric, TrendSeries } from '@enersight/core/types'
import { homeApi } from '@/api/home'
import { useRequest } from '@/hooks/useRequest'
import { ErrorState, SectionHeader, SegmentedTabs, Skeleton, TrendChart, fromTrendSeries } from '@/components'
const OPTIONS = [{ value: 'radiation', label: '辐射' }, { value: 'wind_speed', label: '10米风速' }, { value: 'cloud_cover', label: '云量' }]
/** 父级以站点 ID 为 key；每次指标变化独立请求，旧结果不能充当新指标。 */
export function StationTrend({ stationId, type, initial, version = 0 }: { stationId: string; type: string; initial: TrendSeries | null; version?: number }) {
  const [metric, setMetric] = useState<TrendMetric>(type === 'wind' ? 'wind_speed' : 'radiation')
  const req = useRequest(() => initial?.metric === metric ? Promise.resolve(initial) : homeApi.trends(stationId, metric), [stationId, metric, initial])
  return <View>
    <SectionHeader icon="trendingUp" title="24 小时气象趋势" />
    <SegmentedTabs options={OPTIONS} value={metric} onChange={(m) => setMetric(m as TrendMetric)} />
    {req.refreshError && <View style={{ fontSize: '12px', color: '#a65b16', marginTop: '8px' }} onClick={req.reload}>更新失败，当前为上次曲线 · 点击重试</View>}
    <View style={{ fontSize: '11px', color: '#718096', marginTop: '8px' }}>时间轴为电站当地时间{metric === 'wind_speed' ? ' · 离地10米预报风速' : ''}</View>
    {metric === 'wind_speed' && type === 'wind' && <View style={{ fontSize: '12px', color: '#526174', lineHeight: 1.7, marginTop: '6px' }}>发电预测先将10米风速换算至风机轮毂高度，再代入功率曲线；不能直接用本图风速判断是否发电。</View>}
    <View style={{ marginTop: '14px', minHeight: '150px' }}>
      {req.status === 'error' ? <ErrorState error={req.error} onRetry={req.reload} />
        : req.status !== 'success' || req.data.metric !== metric ? <Skeleton height={150} lines={3} />
        : <TrendChart key={`${stationId}-${metric}-${version}`} id={`trend-${stationId.replace(/[^a-zA-Z0-9]/g, '')}-${version}`} data={fromTrendSeries(req.data)} />}
    </View>
  </View>
}
