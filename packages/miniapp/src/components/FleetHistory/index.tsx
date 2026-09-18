import { View, Text } from '@tarojs/components'
import { useState } from 'react'
import { api } from '@/api'
import { useRequest } from '@/hooks/useRequest'
import { useWeatherModel } from '@/store/weatherModel'
import { ApiError } from '@enersight/core/api'
import { thousands } from '@enersight/core/format'
import { ErrorState, SegmentedTabs, Skeleton } from '@/components'
import './index.scss'

type Period = 'week' | 'month' | 'year'
interface Amount { energy_kwh: number | null; solar_kwh: number | null; wind_kwh: number | null; recorded_days: number; provisional_days: number }
interface RecordRow { date: string; generated_at: string; sealed: boolean; energy_kwh: number; covered_count: number; total_count: number; covered_capacity_kw: number; total_capacity_kw: number; version: string }
interface History extends Amount {
  model: string; provinces: string[]; start: string; end: string; today: string; first_recorded: string | null; expected_days: number; coverage_min: number | null; coverage_max: number | null; versions: string[]; capacity_changed: boolean;
  buckets: (Amount & { key: string; label: string })[];
  days: { date: string; state: string; record: RecordRow | null }[];
}
const iso = (date: Date) => date.toISOString().slice(0, 10)
const today = () => iso(new Date(Date.now() + 8 * 3600_000))
const value = (v: number | null) => v == null ? '—' : `${thousands(v / 1e6, 1)} GWh`

export function FleetHistory({ provinces = [], onClearScope }: { provinces?: string[]; onClearScope?: () => void }) {
  const model = useWeatherModel(s => s.model)
  const scope = provinces.join(',')
  const [period, setPeriod] = useState<Period>('week')
  const [anchor, setAnchor] = useState(today)
  const [selected, setSelected] = useState<string | null>(null)
  const req = useRequest(() => api.get<History>('/v1/predictions/fleet/history', { weather_model: model, period, anchor, ...(scope ? { provinces: scope } : {}) }), [model, period, anchor, scope])
  const d = req.data?.model === model ? req.data : null
  const move = (step: number) => {
    const date = new Date(`${anchor}T00:00:00Z`)
    if (period === 'week') date.setUTCDate(date.getUTCDate() + step * 7)
    else if (period === 'month') { date.setUTCDate(1); date.setUTCMonth(date.getUTCMonth() + step) }
    else { date.setUTCMonth(0, 1); date.setUTCFullYear(date.getUTCFullYear() + step) }
    setAnchor(iso(date)); setSelected(null)
  }
  const max = Math.max(1, ...(d?.buckets.map(b => b.energy_kwh ?? 0) ?? []))
  const active = d?.buckets.find(b => b.key === selected)
  const records = d?.days.filter(day => day.record) ?? []
  const split = d?.buckets.some(b => b.solar_kwh != null) ?? false
  return <View className="fleet-history">
    {!!provinces.length && <View className="region-filter"><View className="region-filter__chips">{provinces.map(name => <View key={name} className="region-filter__chip"><Text>{name}</Text></View>)}</View>{onClearScope && <View className="region-filter__actions"><View className="region-filter__clear" hoverClass="pressed" onClick={onClearScope}><Text>看全国</Text></View></View>}</View>}
    <SegmentedTabs value={period} options={[{value:'week',label:'周'},{value:'month',label:'月'},{value:'year',label:'年'}]} onChange={v => { setPeriod(v as Period); setSelected(null) }} />
    <View className="history-nav"><View onClick={() => move(-1)}>‹ 上一期</View><Text>{period === 'year' ? anchor.slice(0,4)+'年' : period === 'month' ? anchor.slice(0,7) : d ? `${d.start.slice(5)} — ${d.end.slice(5)}` : '本周'}</Text><View className={d && d.end >= d.today ? 'history-disabled' : ''} onClick={() => { if (d && d.end < d.today) move(1) }}>下一期 ›</View></View>
    {anchor !== today() && <View className="history-refresh" onClick={() => { setAnchor(today()); setSelected(null) }}>回到本期</View>}
    {req.status === 'error' ? <ErrorState error={req.error.status === 404 ? new ApiError('HISTORY_UNAVAILABLE', '历史记录服务暂未开放，请稍后再试。', 404) : req.error} onRetry={req.reload} /> : !d ? <Skeleton height={200} /> : <>
      <View className="home__card">
        <View className="forecast-row"><Text className="forecast-title">累计估算电量</Text></View>
        <View className="history-total">{value(d.energy_kwh)}</View>
        <View className="forecast-split"><View><Text className="forecast-muted">光伏</Text>{value(d.solar_kwh)}</View><View><Text className="forecast-muted">风电</Text>{value(d.wind_kwh)}</View></View>
        {!d.recorded_days ? <View className="history-empty">{provinces.length ? '所选地区暂无留存记录' : '暂无记录'}</View> : <>
          {!split && <Text className="forecast-muted">2026-09-18 之前的留档没有分能源类型的细分，这些日子只统计总量。</Text>}
          {split && <View className="history-legend"><View className="history-legend__item"><View className="history-legend__swatch history-solar" /><Text>光伏</Text></View><View className="history-legend__item"><View className="history-legend__swatch history-wind" /><Text>风电</Text></View></View>}
          <View className="history-chart">{d.buckets.map((b,i) => <View key={b.key} className={`history-column ${b.key === selected ? 'history-column--active' : ''}`} onClick={() => { setSelected(b.key); if (period === 'year' && b.recorded_days) { setAnchor(b.key); setPeriod('month') } }}>
            <View className="history-track">{b.energy_kwh == null ? <View className="history-gap" /> : <View className="history-stack" style={{ height: `${Math.max(2, (b.energy_kwh / max) * 140)}px` }}>{b.solar_kwh == null ? <View className="history-unsplit" style={{ flex: 1 }} /> : <><View className="history-solar" style={{ flex: b.solar_kwh }} /><View className="history-wind" style={{ flex: b.wind_kwh || 0 }} /></>}</View>}</View>
            <Text className="history-axis">{period === 'year' ? i+1 : period === 'week' || i%5===0 ? Number(b.key.slice(8)) : ''}</Text>
          </View>)}</View>
          {active && <Text className="history-selection">{active.label} · {value(active.energy_kwh)}</Text>}
        </>}
      </View>
      {!!records.length && <View className="home__card"><Text className="forecast-title">{period === 'year' ? '最近留存记录' : '每日明细'}</Text>{(period === 'year' ? records.slice(-7) : records).map(day => <View className="forecast-region" key={day.date}><Text>{day.date.slice(5)}</Text><Text>{value(day.record!.energy_kwh)}</Text></View>)}</View>}
      {req.refreshError && <Text className="forecast-warning">刷新失败，当前展示上次读取的记录。</Text>}
      <View className="history-refresh" onClick={req.reload}>刷新记录</View>
    </>}
  </View>
}
