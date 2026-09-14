import { Button, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useState } from 'react'
import { weatherCsv } from '@enersight/core/format'
import type { TrendMetric, TrendSeries } from '@enersight/core/types'
import { homeApi } from '@/api/home'
import { useRequest } from '@/hooks/useRequest'
import { exportCsv } from '@/utils/exportCsv'
import { requireLogin } from '@/utils/requireLogin'
import { ErrorState, InfoTip, SectionHeader, SegmentedTabs, Skeleton, TrendChart, fromTrendSeries } from '@/components'
const OPTIONS = [{ value: 'radiation', label: '辐射' }, { value: 'wind_speed', label: '10米风速' }, { value: 'cloud_cover', label: '云量' }]
const INFO = {
  title: '24 小时气象趋势',
  content: '时间轴为电站当地时间，00:00 到次日 00:00，每 15 分钟一点。风速为离地 10 米预报风速；发电预测优先采用各高度层风速换算到风机轮毂高度，仅有 10 米风速时外推估算，不能直接用本图风速判断是否发电。辐射为前 15 分钟平均值，标在区间末。',
}
/**
 * 父级以站点 ID 为 key；每次指标或日期变化独立请求，旧结果不能充当新指标。传 exportName 时可导出全部指标。
 * dayOffset 为今日起第几天，与七天预测所选日期对应；首页随接口带回的 initial 只代表今日。
 */
export function StationTrend({ stationId, type, initial, version = 0, exportName, compact = false, dayOffset = 0 }: { stationId: string; type: string; initial: TrendSeries | null; version?: number; exportName?: string; compact?: boolean; dayOffset?: number }) {
  const [metric, setMetric] = useState<TrendMetric>(type === 'wind' ? 'wind_speed' : 'radiation')
  const [exporting, setExporting] = useState(false)
  const reuse = (m: string) => dayOffset === 0 && initial?.metric === m ? initial : null
  const req = useRequest(() => { const hit = reuse(metric); return hit ? Promise.resolve(hit) : homeApi.trends(stationId, metric, dayOffset) }, [stationId, metric, initial, dayOffset])
  const exportWeather = async () => {
    if (!requireLogin()) return
    setExporting(true)
    try {
      // 三个指标同一份服务端预报缓存，逐个请求不额外消耗气象额度
      // 风电站多导出一列轮毂高度风速，与发电预测同一换算；列名标注实际轮毂高度
      const metrics: { value: TrendMetric; label?: string }[] = [
        ...OPTIONS.map(o => ({ value: o.value as TrendMetric, label: o.label })),
        ...(type === 'wind' ? [{ value: 'hub_wind_speed' as TrendMetric }] : []),
      ]
      const series = await Promise.all(metrics.map(m => reuse(m.value) ?? homeApi.trends(stationId, m.value, dayOffset)))
      const day = series[0]?.points[0]?.time.slice(0, 10) ?? ''
      await exportCsv(`${exportName}_气象趋势_${day}.csv`, weatherCsv(series.map((s, i) => ({ label: metrics[i]!.label ?? `轮毂 ${s.hub_height ?? ''} 米风速`, unit: s.unit, points: s.points }))))
    } catch {
      void Taro.showToast({ title: '导出失败，请稍后重试', icon: 'none' })
    } finally {
      setExporting(false)
    }
  }
  return <View>
    {!compact && <SectionHeader icon="trendingUp" title="24 小时气象趋势" info={INFO} />}
    <View className={compact ? 'home__trend-controls' : ''}><SegmentedTabs options={OPTIONS} value={metric} onChange={(m) => setMetric(m as TrendMetric)} />{compact && <InfoTip {...INFO} />}</View>
    {req.refreshError && <View style={{ fontSize: '12px', color: '#a65b16', marginTop: '8px' }} onClick={req.reload}>更新失败，当前为上次曲线 · 点击重试</View>}
    <View style={{ marginTop: compact ? '4px' : '14px', minHeight: '150px' }}>
      {req.status === 'error' ? <ErrorState error={req.error} onRetry={req.reload} />
        : req.status !== 'success' || req.data.metric !== metric ? <Skeleton height={150} lines={3} />
        : <TrendChart key={`${stationId}-${metric}-${dayOffset}-${version}`} id={`trend-${stationId.replace(/[^a-zA-Z0-9]/g, '')}-${dayOffset}-${version}`} data={fromTrendSeries(req.data)} />}
    </View>
    {exportName && req.status === 'success' && <View className="forecast-actions"><Button className="forecast-action" disabled={exporting} onClick={exportWeather}>{exporting ? '正在导出…' : '导出气象 CSV'}</Button></View>}
  </View>
}
