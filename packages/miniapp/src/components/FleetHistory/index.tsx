import { View, Text } from '@tarojs/components'
import Taro from '@tarojs/taro'
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
  model: string; start: string; end: string; today: string; first_recorded: string | null; expected_days: number; coverage_min: number | null; coverage_max: number | null; versions: string[]; capacity_changed: boolean;
  buckets: (Amount & { key: string; label: string })[];
  days: { date: string; state: string; record: RecordRow | null }[];
}
const iso = (date: Date) => date.toISOString().slice(0, 10)
const today = () => iso(new Date(Date.now() + 8 * 3600_000))
const value = (v: number | null) => v == null ? '—' : `${thousands(v / 1e6, 1)} GWh`

export function FleetHistory() {
  const model = useWeatherModel(s => s.model)
  const [period, setPeriod] = useState<Period>('week')
  const [anchor, setAnchor] = useState(today)
  const [selected, setSelected] = useState<string | null>(null)
  const req = useRequest(() => api.get<History>('/v1/predictions/fleet/history', { weather_model: model, period, anchor }), [model, period, anchor])
  const d = req.data?.model === model ? req.data : null
  const move = (step: number) => {
    const date = new Date(`${anchor}T00:00:00Z`)
    if (period === 'week') date.setUTCDate(date.getUTCDate() + step * 7)
    else if (period === 'month') { date.setUTCDate(1); date.setUTCMonth(date.getUTCMonth() + step) }
    else { date.setUTCMonth(0, 1); date.setUTCFullYear(date.getUTCFullYear() + step) }
    setAnchor(iso(date)); setSelected(null)
  }
  const showDay = (row: RecordRow) => Taro.showModal({ title: `${row.date} · ${row.sealed ? '已封存' : '暂定'}`, content: `估算电量 ${value(row.energy_kwh)}\n场站 ${row.covered_count} / ${row.total_count}\n容量覆盖 ${row.total_capacity_kw ? thousands(row.covered_capacity_kw / row.total_capacity_kw * 100, 1) : '—'}%\n版本 ${row.version}\n此为留存的模型估算，非实测电量。`, showCancel: false })
  const max = Math.max(1, ...(d?.buckets.map(b => b.energy_kwh ?? 0) ?? []))
  const active = d?.buckets.find(b => b.key === selected)
  const records = d?.days.filter(day => day.record) ?? []
  return <View className="fleet-history">
    <SegmentedTabs value={period} options={[{value:'week',label:'周'},{value:'month',label:'月'},{value:'year',label:'年'}]} onChange={v => { setPeriod(v as Period); setSelected(null) }} />
    <View className="history-nav"><View onClick={() => move(-1)}>‹ 上一期</View><Text>{period === 'year' ? anchor.slice(0,4)+'年' : period === 'month' ? anchor.slice(0,7) : d ? `${d.start.slice(5)} — ${d.end.slice(5)}` : '本周'}</Text><View className={d && d.end >= d.today ? 'history-disabled' : ''} onClick={() => { if (d && d.end < d.today) move(1) }}>下一期 ›</View></View>
    {anchor !== today() && <View className="history-refresh" onClick={() => { setAnchor(today()); setSelected(null) }}>回到本期</View>}
    {req.status === 'error' ? <ErrorState error={req.error.status === 404 ? new ApiError('HISTORY_UNAVAILABLE', '历史记录服务暂未开放，请稍后再试。', 404) : req.error} onRetry={req.reload} /> : !d ? <Skeleton height={200} /> : <>
      <View className="home__card">
        <View className="forecast-row"><Text className="forecast-title">累计估算电量</Text><Text className="forecast-muted">已记录 {d.recorded_days} 天</Text></View>
        <View className="history-total">{value(d.energy_kwh)}</View>
        <Text className="forecast-muted">{d.provisional_days ? `包含 ${d.provisional_days} 天暂定结果 · 封存前可能变化` : '仅累计已留存日期 · 非实测发电量'}</Text>
        <View className="forecast-split"><View><Text className="forecast-muted">光伏</Text>{value(d.solar_kwh)}</View><View><Text className="forecast-muted">风电</Text>{value(d.wind_kwh)}</View></View>
        {!d.recorded_days ? <View className="history-empty">{d.first_recorded ? '这段时间没有留存记录，不补算过去。' : '正在积累记录。首次有效汇总生成后，将从当天开始展示。'}</View> : <>
          <View className="history-legend"><View className="history-legend__item"><View className="history-legend__swatch history-solar" /><Text>光伏</Text></View><View className="history-legend__item"><View className="history-legend__swatch history-wind" /><Text>风电</Text></View><Text>点击柱形查看</Text></View>
          <View className="history-chart">{d.buckets.map((b,i) => <View key={b.key} className={`history-column ${b.key === selected ? 'history-column--active' : ''}`} onClick={() => { setSelected(b.key); if (period === 'year' && b.recorded_days) { setAnchor(b.key); setPeriod('month') } }}>
            <View className="history-track">{b.energy_kwh == null ? <View className="history-gap" /> : <View className="history-stack" style={{ height: `${Math.max(2, (b.energy_kwh / max) * 140)}px` }}><View className="history-solar" style={{ flex: b.solar_kwh || 0 }} /><View className="history-wind" style={{ flex: b.wind_kwh || 0 }} /></View>}</View>
            <Text className="history-axis">{period === 'year' ? i+1 : period === 'week' || i%5===0 ? Number(b.key.slice(8)) : ''}</Text>
          </View>)}</View>
          <Text className="forecast-muted">{active ? `${active.label} · ${value(active.energy_kwh)}${active.provisional_days ? ' · 暂定' : ''}` : period === 'year' ? '横轴：月份 · 点击月份查看每日明细' : '横轴：日期 · 空位表示未记录或尚未到来'}</Text>
        </>}
      </View>
      <View className="home__card"><Text className="forecast-title">数据完整度</Text><Text className="forecast-note">已留存 {d.recorded_days} / {d.expected_days} 天（截至今天）</Text><Text className="forecast-note">记录内目录容量覆盖：{d.coverage_min == null ? '—' : `${thousands(d.coverage_min,1)}% — ${thousands(d.coverage_max!,1)}%`}</Text>{(d.capacity_changed || d.versions.length>1) && <Text className="forecast-warning">本期目录覆盖或计算版本发生变化，电量变化不完全由天气引起。</Text>}<Text className="forecast-muted">计算版本：{d.versions.join('、') || '暂无'} · 此处覆盖率不代表预测准确率</Text></View>
      {!!records.length && <View className="home__card"><Text className="forecast-title">{period === 'year' ? '最近留存记录' : '每日明细'}</Text>{(period === 'year' ? records.slice(-7) : records).map(day => <View className="forecast-region" key={day.date} onClick={() => void showDay(day.record!)}><Text>{day.date.slice(5)} · {day.state === 'sealed' ? '已封存' : '暂定'}</Text><Text>{value(day.record!.energy_kwh)} ›</Text></View>)}</View>}
      <Text className="forecast-note">{d.first_recorded ? `本模型自 ${d.first_recorded} 起留存。` : '本模型尚无留存记录。'}每日仅计入一份结果，次日08:15后封存；缺失日期不记为零。</Text>
      {req.refreshError && <Text className="forecast-warning">刷新失败，当前展示上次读取的记录。</Text>}
      <View className="history-refresh" onClick={req.reload}>刷新记录</View>
    </>}
  </View>
}
