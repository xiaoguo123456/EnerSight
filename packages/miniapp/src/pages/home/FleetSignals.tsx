import { Button, Picker, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useEffect, useState } from 'react'
import { formatBeijingTime, formatEnergy, powerChartUnit } from '@enersight/core/format'
import type { FleetSignalResponse } from '@enersight/core/types'
import { api } from '@/api'
import { Icon, InfoTip, Skeleton, TrendChart } from '@/components'
import { useRequest } from '@/hooks/useRequest'
import { useMapStore } from '@/store'
import { servedModel } from '@/store/weatherModel'

const WATCH_KEY = 'enersight_signal_watch'
const THRESHOLD_KEY = 'enersight_signal_threshold'
const THRESHOLDS = [5, 10, 20]
const MODELS: Record<string, string> = {
  best_match: '自动', ecmwf_ifs: 'ECMWF', icon_global: 'ICON', gfs_global: 'GFS',
}

function readWatch(): string[] {
  try {
    const value = Taro.getStorageSync(WATCH_KEY)
    return Array.isArray(value) ? value.filter((x): x is string => typeof x === 'string') : []
  } catch { return [] }
}
function readThreshold(): number {
  try {
    const value = Number(Taro.getStorageSync(THRESHOLD_KEY))
    return THRESHOLDS.includes(value) ? value : 10
  } catch { return 10 }
}
function changeText(percent: number | null | undefined) {
  if (percent == null) return '—'
  if (Math.abs(percent) < 0.05) return '基本持平'
  return `${percent > 0 ? '↑' : '↓'} ${Math.abs(percent).toFixed(1)}%`
}
function direction(percent: number | null | undefined) {
  return percent == null || Math.abs(percent) < 0.05 ? 'flat' : percent > 0 ? 'up' : 'down'
}
function linkage(data: FleetSignalResponse) {
  const solar = direction(data.solar?.change_percent)
  const wind = direction(data.wind?.change_percent)
  if (solar === 'flat' || wind === 'flat') return '风光变化分开看'
  if (solar === wind) return solar === 'up' ? '风光同升' : '风光同降'
  return '风光方向相反'
}
function hourText(iso: string) { return iso.slice(11, 16) }
function dateText(iso: string) { return `${iso.slice(5, 7)}/${iso.slice(8, 10)}` }
function energyText(kwh: number) { const amount = formatEnergy(kwh); return `${amount.value} ${amount.unit}` }
function energyRange(lowKwh: number, highKwh: number) {
  const low = formatEnergy(lowKwh)
  const high = formatEnergy(highKwh)
  return low.unit === high.unit ? `${low.value}–${high.value} ${high.unit}` : `${low.value} ${low.unit}–${high.value} ${high.unit}`
}
function scopeHash(names: string[]) { return names.join('').split('').reduce((value, x) => (value * 31 + x.charCodeAt(0)) % 100000, 0) }

function Change({ label, percent }: { label: string; percent: number | null | undefined }) {
  return <View className="fleet-signal__change">
    <Text className="fleet-signal__label">{label}</Text>
    <Text className={`fleet-signal__delta fleet-signal__delta--${direction(percent)}`}>{changeText(percent)}</Text>
  </View>
}

/** 同目标日、同场站样本的签发变化；只读留档，游客可看。docs/20 */
export function FleetSignals({ date, model, provinces, availableRegions, onProvince, refreshKey }: {
  date: string
  model: Parameters<typeof servedModel>[0]
  provinces: string[]
  availableRegions: string[]
  onProvince: (province: string) => void
  refreshKey: string
}) {
  const scope = provinces.join(',')
  const [open, setOpen] = useState(false)
  const [watch, setWatch] = useState<string[]>(readWatch)
  const [threshold, setThreshold] = useState(readThreshold)
  const setLayer = useMapStore(s => s.setActiveLayer)
  const req = useRequest(() => api.get<FleetSignalResponse>('/v1/predictions/fleet/signals', {
    date, weather_model: servedModel(model), ...(scope ? { provinces: scope } : {}),
  }), [date, model, scope, refreshKey])
  useEffect(() => { try { Taro.setStorageSync(WATCH_KEY, watch) } catch { /* 本机存储不可用时仍可浏览 */ } }, [watch])
  useEffect(() => { try { Taro.setStorageSync(THRESHOLD_KEY, threshold) } catch { /* 同上 */ } }, [threshold])

  const data = req.status === 'success' ? req.data : null
  const alerts = data?.regions.filter(r => watch.includes(r.province) && r.combined?.change_percent != null && Math.abs(r.combined.change_percent) >= threshold) ?? []
  const firstAlert = alerts[0]
  const toggleWatch = (name: string) => setWatch(previous => previous.includes(name) ? previous.filter(x => x !== name) : [...previous, name])
  const addRegion = (index: number) => {
    const region = availableRegions[index]
    if (region && !watch.includes(region)) setWatch(previous => [...previous, region])
  }
  const showCloud = () => { setLayer('cloud'); void Taro.switchTab({ url: '/pages/map/index' }) }
  const chart = data?.hours ?? []
  const unit = powerChartUnit(chart.flatMap(h => [h.current_kw, h.previous_kw]))
  const details = data?.evolution ?? []

  return <View className="home__card fleet-signal">
    <View className="fleet-signal__head">
      <View className="fleet-signal__title"><Icon name="lineChart" size={18} color="#1677ff" /><Text>风光变化 · {dateText(date)}</Text><InfoTip title="风光变化口径" content="对同一目标日、同一批目录场站的两次签发进行比较。只反映平台目录样本的气象预测变化，不代表全省实际出力或可交易电量。逐小时变化由 15 分钟插值曲线聚合；上升红、下降绿仅表示方向。" /></View>
      <Button className="forecast-action" onClick={() => setOpen(v => !v)}>{open ? '收起' : '详情'} <Icon name={open ? 'chevronUp' : 'chevronDown'} size={13} /></Button>
    </View>

    {req.status === 'loading' && <Skeleton height={112} lines={2} />}
    {req.status === 'error' && (req.error.status === 404
      ? <Text className="fleet-signal__state">当前服务端尚未支持风光变化</Text>
      : <View className="fleet-signal__state" onClick={() => void req.reload()}>变化信号暂不可用 · 点击重试</View>)}
    {req.refreshError && data && <View className="fleet-signal__state" onClick={() => void req.reload()}>刷新失败 · 当前保留上次结果</View>}
    {data && <>
      {firstAlert && <View className="fleet-signal__notice" onClick={() => onProvince(firstAlert.province)}><Icon name="bell" size={15} color="#98521a" /><Text>{alerts.map(r => r.province).join('、')}变化达到 {threshold}%</Text><Icon name="chevronRight" size={13} /></View>}
      <View className="fleet-signal__pair">
        <Change label="光伏" percent={data.solar?.change_percent} />
        <Change label="风电" percent={data.wind?.change_percent} />
      </View>
      {data.reason ? <Text className="fleet-signal__state">{data.reason}</Text>
        : <View className="fleet-signal__linkage"><Text>{linkage(data)}</Text><Text>较 {data.previous_generated_at ? formatBeijingTime(data.previous_generated_at) : '上一轮'} 签发</Text></View>}

      {data.top_windows.length > 0 && <View className="fleet-signal__section">
        <Text className="fleet-signal__section-title">重点时段</Text>
        {data.top_windows.map(item => <View className="fleet-signal__row" key={item.start_hour}>
          <Text>{hourText(item.start_hour)}–{hourText(item.end_hour)}</Text>
          <Text className={`fleet-signal__delta fleet-signal__delta--${direction(item.change_capacity_percent)}`}>{item.change_capacity_percent > 0 ? '↑' : '↓'} {Math.abs(item.change_capacity_percent).toFixed(1)}% 装机</Text>
        </View>)}
      </View>}

      {data.regions.length > 0 && <View className="fleet-signal__section">
        <Text className="fleet-signal__section-title">区域变化榜</Text>
        {data.regions.slice(0, open ? 10 : 4).map(item => <View className="fleet-signal__region" key={item.province}>
          <View className="fleet-signal__region-name" onClick={() => onProvince(item.province)}><Text>{item.province}</Text><Icon name="chevronRight" size={13} color="#9ca3af" /></View>
          <Text className={`fleet-signal__delta fleet-signal__delta--${direction(item.combined?.change_percent)}`}>{changeText(item.combined?.change_percent)}</Text>
          <View className={`fleet-signal__watch${watch.includes(item.province) ? ' fleet-signal__watch--on' : ''}`} role="button" aria-label={watch.includes(item.province) ? `取消关注${item.province}` : `关注${item.province}`} onClick={() => toggleWatch(item.province)}><Icon name="bell" size={16} color={watch.includes(item.province) ? '#1677ff' : '#64748b'} /></View>
        </View>)}
      </View>}

      {open && <View className="fleet-signal__details">
        {chart.length === 24 && <View className="fleet-signal__section">
          <Text className="fleet-signal__section-title">分时变化 <Text className="fleet-signal__unit">· {unit.label}</Text></Text>
          <View className="fleet-signal__legend"><Text>本轮预测</Text><Text>上轮预测</Text></View>
          <TrendChart id={`fleet-signal-${date.replace(/-/g, '')}-${scopeHash(provinces)}-${model}`} height={158} data={{ values: chart.map(h => h.current_kw / unit.divisor), comparison: chart.map(h => h.previous_kw / unit.divisor), labels: { value: '本轮', comparison: '上轮' }, times: chart.map(h => h.hour), unit: unit.label, yMax: null, stepMinutes: 60 }} />
        </View>}
        {details.length > 1 && <View className="fleet-signal__section">
          <Text className="fleet-signal__section-title">预报演变</Text>
          {details.map(item => <View className="fleet-signal__row" key={item.generated_at}><Text>{formatBeijingTime(item.issued_at ?? item.generated_at)}{item.issued_at ? ' 起报' : ' 签发'}</Text><Text>{energyText(item.energy_kwh)}</Text></View>)}
        </View>}
        <View className="fleet-signal__section">
          <Text className="fleet-signal__section-title">模型分歧</Text>
          {data.model_range ? <><Text className="fleet-signal__model-range">{energyRange(data.model_range.low_kwh, data.model_range.high_kwh)} · {data.model_range.members.length} 个模型</Text><Text className="fleet-signal__model-list">{data.model_range.members.map(m => `${MODELS[m.model] ?? m.model} ${energyText(m.energy_kwh)}`).join(' · ')}</Text></> : <Text className="fleet-signal__state">暂无同日可比模型</Text>}
        </View>
        <View className="fleet-signal__tools">
          {availableRegions.length > 0 && <Picker mode="selector" range={availableRegions} onChange={e => addRegion(Number(e.detail.value))}><View className="fleet-signal__tool"><Icon name="bell" size={15} /><Text>关注地区</Text></View></Picker>}
          {watch.length > 0 && <Picker mode="selector" range={THRESHOLDS.map(value => `变化 ≥ ${value}%`)} value={THRESHOLDS.indexOf(threshold)} onChange={e => setThreshold(THRESHOLDS[Number(e.detail.value)] ?? 10)}><View className="fleet-signal__tool"><Text>提醒阈值 {threshold}%</Text><Icon name="chevronDown" size={13} /></View></Picker>}
          <View className="fleet-signal__tool" role="button" onClick={showCloud}><Icon name="satellite" size={15} /><Text>卫星云图</Text></View>
          <View className="fleet-signal__tool" role="button" onClick={() => void Taro.switchTab({ url: '/pages/alert/index' })}><Icon name="cloud" size={15} /><Text>站点短临</Text></View>
        </View>
      </View>}
      <Text className="fleet-signal__foot">平台目录估算 · {data.generated_at ? `${formatBeijingTime(data.generated_at)} 更新` : '等待签发'}</Text>
    </>}
  </View>
}
