import { useHomeShare } from '@/hooks/useAppShare'
import { Button, Picker, Switch, View, Text } from '@tarojs/components'
import Taro, { useDidShow, useDidHide, usePullDownRefresh } from '@tarojs/taro'
import { useEffect, useState } from 'react'
import { powerChartUnit, powerCsv, thousands, formatBeijingTime, formatPercent, formatRadiation, formatTemperature, formatUtilization, formatWindSpeed, formatPower } from '@enersight/core/format'
import type { DailyOutlook, EnsembleSummary, FleetDay, FleetPrediction, ForecastBasis, GenerationPrediction, StationOutlook, StationSummary } from '@enersight/core/types'
import { api } from '@/api'
import { homeApi } from '@/api/home'
import { stationsApi } from '@/api/stations'
import { AlertBanner, ErrorState, ForecastSpread, Icon, InfoTip, MetricCard, MetricGrid, OutlookStrip, PageTitleBar, RecordSheet, SectionHeader, SegmentedTabs, Skeleton, StationTitleBar, TrendChart, dayLabel } from '@/components'
import { ApiError } from '@enersight/core/api'
import { useRequest } from '@/hooks/useRequest'
import { useStationStore } from '@/store'
import { servedModel, useWeatherModel, WEATHER_MODELS, weatherModelLabel } from '@/store/weatherModel'
import { StationTrend } from '@/components/StationTrend'
import { exportCsv } from '@/utils/exportCsv'
import { requireLogin } from '@/utils/requireLogin'
import './index.scss'
import { FleetSignals } from './FleetSignals'

const RESOLVED_LABEL: Record<string, string> = { ecmwf_ifs: 'ECMWF IFS 9 km', ncep_gfs013: 'GFS 0.13°', ncep_gfs025: 'GFS 0.25°', dwd_icon: 'ICON 13 km' }
const COMPACT_MODEL: Record<string, string> = { ecmwf_ifs: 'ECMWF', ncep_gfs013: 'GFS', ncep_gfs025: 'GFS', dwd_icon: 'ICON' }
const DISCLAIMER = '预测不等于实际并网电量，未计入检修与故障。'
/** 限电第二层开关，默认关闭，记在本机。docs/17 §四 */
const PROVINCE_GRID_KEY = 'enersight_province_grid'
/** 省份不详的汇总桶：是合法的汇总项，但不作为筛选项。docs/17 §二 */
const UNKNOWN_REGION = '地区待补充'

function energy(value?: number | null) {
  if (value == null) return { value: '—', unit: 'MWh' }
  const divisor = value >= 1e6 ? 1e6 : value >= 1000 ? 1000 : 1
  return { value: thousands(value / divisor, 1), unit: divisor === 1e6 ? 'GWh' : divisor === 1000 ? 'MWh' : 'kWh' }
}
function fmtEnergy(value?: number | null) { const v = energy(value); return `${v.value} ${v.unit}` }
function fmtDate(d: { date: string; weekday: number; lead_days: number }) { return `${dayLabel(d)} ${d.date.slice(5).replace('-', '/')}` }
/** 模型选择器内展示批次；拿不到起报时只给拉取时间。 */
function shortTime(iso: string) {
  const t = formatBeijingTime(iso)
  const today = formatBeijingTime(new Date().toISOString()).slice(0, 5)
  return t.startsWith(today) ? t.slice(6) : t
}
function basisShort(basis?: ForecastBasis | null) {
  if (!basis) return ''
  // 三模式的起报统一报三家中最早的那一家，写明「最早」，免得被当成三家同一批。docs/19 §一
  const label = basis.model === 'ensemble' ? '最早起报' : '起报'
  return basis.issued_at ? ` · ${label} ${shortTime(basis.issued_at)}` : ` · 拉取 ${shortTime(basis.fetched_at)}`
}
const MEMBER_LABEL: Record<string, string> = { ecmwf_ifs: 'ECMWF', icon_global: 'ICON', gfs_global: 'GFS', cma_grapes_global: 'GRAPES' }
/** 三模式：统一按最早起报，每家各自的起报列在后面。 */
function ensembleBasisDetail(e: EnsembleSummary, basis?: ForecastBasis | null) {
  const each = e.members.map(m => `${MEMBER_LABEL[m.model] ?? m.model} ${m.issued_at ? formatBeijingTime(m.issued_at) : '起报未知'}`).join('、')
  const head = basis?.issued_at
    ? `气象批次：三模式，起报按三家中最早的 ${formatBeijingTime(basis.issued_at)} 标注`
    : `气象批次：三模式，有一家拿不到起报时刻，只标服务端拉取时间 ${formatBeijingTime(basis?.fetched_at)}`
  return `${head}；各家起报：${each}（北京时间）。三家都每 6 小时起报一轮，发布要晚 3–7 小时，所以同一时刻常常不是同一批。`
}
function basisDetail(basis?: ForecastBasis | null) {
  if (!basis) return '气象批次：未知'
  const model = basis.resolved_model ? RESOLVED_LABEL[basis.resolved_model] ?? basis.resolved_model : '混合模型'
  return basis.issued_at
    ? `气象批次：${model}，起报 ${formatBeijingTime(basis.issued_at)}，数据 ${formatBeijingTime(basis.available_at ?? basis.fetched_at)} 更新，服务端 ${formatBeijingTime(basis.fetched_at)} 拉取（北京时间）。起报以首日为准，第 7 天可能由更早或更粗批次补齐。`
    : `气象批次：${model}，无法确认起报时刻，服务端 ${formatBeijingTime(basis.fetched_at)} 拉取（北京时间）。`
}
function peakOf(points: { time: string; value: number | null }[]) {
  let best: { time: string; value: number } | null = null
  for (const p of points) if (p.value != null && (!best || p.value > best.value)) best = { time: p.time, value: p.value }
  return best ? { value: best.value, time: best.time.slice(11, 16) } : null
}
function Value({ value }: { value?: number | null }) {
  const v = energy(value)
  return <View className="forecast-value"><Text>{v.value}</Text><Text className="forecast-value__unit">{v.unit}</Text></View>
}
function PowerCurve({ points, grid, band, id, title, step = 60, onExport }: { points: { time: string; value: number | null }[]; grid?: { time: string; value: number | null }[] | null; band?: { low: { time: string; value: number | null }[] | null; high: { time: string; value: number | null }[] | null } | null; id: string; title: string; step?: number; onExport?: () => void }) {
  const values = points.map(p => p.value)
  // 带的上沿可能高过主曲线，进位单位要一起看，否则带会溢出画布
  const unit = powerChartUnit(band?.high ? [...values, ...band.high.map(p => p.value)] : values)
  const [table, setTable] = useState(false)
  const gridByTime = new Map(grid?.map(p => [p.time, p.value]))
  const compare = grid ? points.map(p => gridByTime.get(p.time) ?? null) : undefined
  const scale = (v: number | null | undefined) => v == null ? null : v / unit.divisor
  // 带按主曲线的时刻对齐；服务端已经保证同序等长，这里仍按 time 查一次防错位
  const bandData = band?.low && band?.high ? (() => {
    const low = new Map(band.low!.map(p => [p.time, p.value]))
    const high = new Map(band.high!.map(p => [p.time, p.value]))
    return { low: points.map(p => scale(low.get(p.time))), high: points.map(p => scale(high.get(p.time))) }
  })() : undefined
  return <View className="forecast-curve">
    <View className="forecast-row">
      <Text className="forecast-subtitle">{title}<Text className="forecast-unit"> · {unit.label}</Text></Text>
      <Button className="forecast-action" onClick={() => setTable(v => !v)}>{table ? '收起明细' : '数据明细'}</Button>
    </View>
    {grid && <View className="forecast-series"><Text>可发</Text><Text className="forecast-series__grid">预计上网</Text></View>}
    {bandData && !grid && <View className="forecast-series"><Text>中位模式</Text><Text className="forecast-series__band">三模式区间</Text></View>}
    <TrendChart id={id} key={id} height={180} data={{ values: values.map(scale), comparison: compare?.map(scale), shortfallShade: true, band: bandData, times: points.map(p => p.time), unit: unit.label, yMax: null, stepMinutes: step }} />
    {table && <View className="forecast-details">
      {onExport && <View className="forecast-actions"><Button className="forecast-action" onClick={onExport}>导出七天 CSV</Button></View>}
      <View className="forecast-table">{points.map((p, i) => <View key={p.time} className="forecast-row"><Text>{p.time.slice(11,16)}</Text><Text>可发 {p.value == null ? '—' : (p.value / unit.divisor).toFixed(2)} {unit.label}{compare ? ` · 上网 ${compare[i] == null ? '—' : (compare[i]! / unit.divisor).toFixed(2)} ${unit.label}` : ''}</Text></View>)}</View>
    </View>}
  </View>
}
/** 上网口径按数字优先摆两格；没有规则不出现。 */
function GridSplit({ p }: { p: GenerationPrediction | DailyOutlook | null | undefined }) {
  if (!p || p.grid_energy_kwh == null) return null
  return <View className="forecast-split forecast-split--tight">
    <View><Text className="forecast-label">预计上网</Text><Text>{fmtEnergy(p.grid_energy_kwh)}</Text></View>
    <View><Text className="forecast-label">限电损失</Text><Text>{fmtEnergy(p.curtailed_kwh)}</Text></View>
  </View>
}

/** 地区筛选条：只在选中时出现。单选时另给一个入口去该省电站目录。docs/01「全目录按地区筛选」 */
function RegionFilter({ names, onRemove, onClear }: { names: string[]; onRemove: (name: string) => void; onClear: () => void }) {
  const toCatalog = () => {
    Taro.setStorageSync('enersight_prediction_province', names[0])
    void Taro.switchTab({ url: '/pages/station/index' })
  }
  return <View className="region-filter">
    <View className="region-filter__chips">
      {names.map(name => <View key={name} className="region-filter__chip" hoverClass="pressed" onClick={() => onRemove(name)}><Text>{name}</Text><Icon name="x" size={12} strokeWidth={2} color="currentColor" /></View>)}
    </View>
    <View className="region-filter__actions">
      {names.length === 1 && <View className="region-filter__link" hoverClass="pressed" onClick={toCatalog}><Text>查看该省电站 ›</Text></View>}
      <View className="region-filter__clear" hoverClass="pressed" onClick={onClear}><Text>清除</Text></View>
    </View>
  </View>
}

function scopeText(names: string[]) {
  if (!names.length) return ''
  return names.length <= 2 ? names.join(' · ') : `${names[0]} 等 ${names.length} 个地区`
}

function periodText(period: string) {
  const [year, month] = period.split('-')
  return month ? `${year} 年 ${Number(month)} 月` : `${year} 年`
}

/** 限电第二层：按省级月度利用率折算的上网参考。开关默认关；月均值与真实限电差距大，只作参考。 */
function ProvinceGridToggle({ on, onToggle, children }: { on: boolean; onToggle: (on: boolean) => void; children: React.ReactNode }) {
  return <View className="forecast-province">
    <View className="forecast-row"><Text className="forecast-subtitle">省级限电参考</Text><Switch checked={on} color="#1677ff" onChange={e => onToggle(!!e.detail.value)} /></View>
    {on && children}
  </View>
}

function StationProvinceGrid({ g, type, on, onToggle }: { g: DailyOutlook['province_grid'] | undefined; type: string; on: boolean; onToggle: (on: boolean) => void }) {
  if (!g) return null
  const u = formatUtilization(g.utilization)
  return <ProvinceGridToggle on={on} onToggle={onToggle}>
    <View className="forecast-split forecast-split--tight"><View><Text className="forecast-label">预计上网</Text><Text>{fmtEnergy(g.energy_kwh)}</Text></View><View><Text className="forecast-label">限电损失</Text><Text>{fmtEnergy(g.curtailed_kwh)}</Text></View></View>
    <Text className="forecast-muted">{`按${g.region} ${periodText(g.period)}${type === 'wind' ? '风电' : '光伏'}利用率 ${u.value}${u.unit} 折算，月均值仅作参考。来源：${g.source}`}</Text>
  </ProvinceGridToggle>
}

function FleetProvinceGrid({ g, on, onToggle }: { g: FleetDay['province_grid'] | undefined; on: boolean; onToggle: (on: boolean) => void }) {
  if (!g) return null
  return <ProvinceGridToggle on={on} onToggle={onToggle}>
    <View className="forecast-split forecast-split--tight"><View><Text className="forecast-label">预计上网</Text><Text>{fmtEnergy(g.energy_kwh)}</Text></View><View><Text className="forecast-label">限电损失</Text><Text>{fmtEnergy(g.curtailed_kwh)}</Text></View></View>
    <Text className="forecast-muted">{`${thousands(g.applied_count)} 座按所在省 ${g.periods.map(periodText).join('、')}利用率折算${g.unapplied_count ? `，${thousands(g.unapplied_count)} 座省份不详按可发电量计入` : ''}；月均值仅作参考。来源：${g.source}`}</Text>
  </ProvinceGridToggle>
}

/** 本站按所选日电量、峰值、功率曲线、七日电量组织。选中日由父级持有，下方气象趋势跟着切。 */
function StationForecast({ station, p, version, onReload, refreshError, generatedAt, req, selected, onSelect, provinceOn, onProvince, onRecord }: {
  station: StationSummary; p: GenerationPrediction | null | undefined; version: number; onReload: () => void; refreshError: boolean; generatedAt?: string; req: { data: StationOutlook | null; status: string; error: ApiError | null; reload: () => Promise<void>; refreshError: ApiError | null }
  selected: number; onSelect: (i: number) => void; provinceOn: boolean; onProvince: (on: boolean) => void; onRecord?: () => void
}) {
  const days = req.data?.days ?? []
  const day = days[selected]
  const value = day ? day.energy_kwh : p?.energy_kwh
  const points = day ? day.power_kw : p?.power_kw
  const label = day ? fmtDate(day) : '今日'
  const peak = points ? peakOf(points) : null
  const info = {
    title: '预测口径',
    content: [
      ...(req.data?.assumptions ?? p?.assumptions ?? []),
      station.type === 'wind' ? '风电按轮毂高度风速与机型功率曲线估算（公开电站按投运年份选机型），低于切入或高于切出风速时功率为零，不代表实测停机。' : '光伏按倾斜面辐射与 PVWatts 估算，夜间功率为零。',
      '七天功率曲线均按 15 分钟计算，采用电站当地时间。',
      req.data?.ensemble ? ensembleBasisDetail(req.data.ensemble, req.data.basis) : basisDetail(req.data?.basis ?? p?.basis),
      DISCLAIMER,
    ].join('\n'),
  }
  return <View className="home__card forecast-main">
    <View className="forecast-row"><View className="forecast-heading"><Text className="forecast-title">{label} 发电量估算</Text><InfoTip {...info} /></View><Button className="forecast-action" onClick={() => Taro.navigateTo({ url: `/pages/station/detail?id=${encodeURIComponent(station.id)}` })}>电站资料 ›</Button></View>
    {station.prediction_blocked_reason ? <View className="forecast-empty"><Text>{station.prediction_blocked_reason}，暂不估算电量</Text><Button className="forecast-action" onClick={() => Taro.switchTab({ url: '/pages/station/index' })}>选择其他电站</Button></View> : <View className="forecast-overview">
      <View className="forecast-headline">
        <Value value={value} />
        {/* 按实测订正过：电量与曲线乘了系数，指数没乘。系数与样本在 ⓘ 里。docs/19 §三 */}
        {value != null && (day ? day.corrected : p?.corrected) && <Text className="forecast-badge">实测订正</Text>}
      </View>
      {value != null && peak && <View className="forecast-peak"><Text className="forecast-label">峰值 · {peak.time}</Text><Text>{formatPower(peak.value).value}<Text className="forecast-unit"> {formatPower(peak.value).unit}</Text></Text></View>}
    </View>}
    {day && <ForecastSpread day={day} ensemble={req.data?.ensemble} stationId={station.id} />}
    <GridSplit p={day ?? p} />
    {(day ?? p)?.grid_energy_kwh == null && !station.prediction_blocked_reason && value != null && <StationProvinceGrid g={(day ?? p)?.province_grid} type={station.type} on={provinceOn} onToggle={onProvince} />}
    {value != null && points && <PowerCurve points={points} grid={day ? day.grid_power_kw : p?.grid_power_kw} band={day ? { low: day.power_kw_low, high: day.power_kw_high } : null} id={`power-${station.id.replace(/[^a-zA-Z0-9]/g, '')}-${selected}-${version}`} title="预测功率" step={day ? day.resolution_minutes : (p?.resolution_minutes ?? 60)}
      onExport={() => { if (requireLogin()) void exportCsv(`${station.name}_预测功率_${days[0]?.date ?? p?.date ?? ''}.csv`, powerCsv(days.length ? days : p ? [p] : [])) }} />}
    {req.status === 'error' ? <ErrorState error={req.error!} onRetry={req.reload} />
      : req.status !== 'success' ? <Skeleton height={100} lines={2} />
      : <OutlookStrip days={days.map(d => ({ ...d, caption: d.weather_text, level: d.index_level, score: d.index_score }))} selected={selected} onSelect={onSelect} />}
    {value == null && !station.prediction_blocked_reason && <Text className="forecast-state">{station.prediction_blocked_reason ? `${station.prediction_blocked_reason}，暂不估算日电量` : !p && !day ? '预测服务暂未就绪，请稍后刷新' : '气象数据或电站参数不完整，暂不估算'}</Text>}
    {(refreshError || req.refreshError) && <Text className="forecast-warning" onClick={onReload}>刷新失败，当前保留上次预测 · 点击重试</Text>}
    {(day?.estimated ?? p?.estimated) && <Text className="forecast-warning">部分气象输入使用补值或降级估算</Text>}
    <View className="forecast-foot"><Text>{formatBeijingTime(req.data?.generated_at ?? generatedAt)} 更新</Text><View className="forecast-actions">{onRecord && <Button className="forecast-action" onClick={onRecord}>记一笔</Button>}<Button className="forecast-action" onClick={onReload}>刷新</Button></View></View>
  </View>
}

/** 全目录：同样一张卡。选中日由父级持有，区域贡献要跟着切。 */
function FleetForecast({ f, selected, onSelect, version, onReload, refreshError, provinceOn, onProvince, scopeLabel, onHistory }: { f: FleetPrediction; selected: number; onSelect: (i: number) => void; version: number; onReload: () => void; refreshError: boolean; provinceOn: boolean; onProvince: (on: boolean) => void; scopeLabel?: string; onHistory: () => void }) {
  const days = f.days ?? []
  const day = days[selected]
  const value = day ? day.energy_kwh : f.energy_kwh
  const solar = day ? day.solar_kwh : f.solar_kwh
  const wind = day ? day.wind_kwh : f.wind_kwh
  const points = day ? day.power_kw : f.power_kw
  const label = day ? fmtDate(day) : '今日'
  const coveredCount = day?.covered_count ?? f.covered_count
  const coveredCapacity = day?.covered_capacity_kw ?? f.covered_capacity_kw
  const coverage = f.total_capacity_kw ? Math.min(100, coveredCapacity / f.total_capacity_kw * 100) : 0
  const info = {
    title: '全目录口径',
    content: [
      ...(f.assumptions ?? []),
      `已计算 ${thousands(coveredCount)} / ${thousands(f.total_count)} 座；口径待核验或参数不足 ${thousands(f.invalid_count)} 座，气象待补 ${thousands(day?.failed_count ?? f.failed_count)} 座，已排除完全重复记录 ${thousands(f.duplicate_count)} 条。`,
      '七天电量、选中日电量及功率曲线统一使用该日已计算电站。各日有完整气象数据的电站可能不同，因此柱形变化也会受到覆盖范围影响。后 3 天预报仅作参考。',
      `计算时间：${formatBeijingTime(f.generated_at)}（北京时间）。${f.message ?? ''}`,
      ...(scopeLabel ? [`本页只统计 ${scopeLabel}，覆盖统计的分母也按这些地区计。`] : []),
      '覆盖率按目录申报容量计算，不是全国覆盖率或准确率。区域汇总采用分能源类型的气象网格与默认设备参数，网格大小见上述说明；本站预测使用本站位置，二者可能存在近似差异。',
      basisDetail(f.basis),
      DISCLAIMER,
    ].join('\n'),
  }
  if (value == null && !days.some(d => d.energy_kwh != null)) {
    return <View className="home__card fleet-preparing">
      <Text className="forecast-title">{f.status === 'error' ? '预测暂不可用' : '预测准备中'}</Text>
      {f.status === 'error' ? <Button className="forecast-action" onClick={onReload}>重试</Button> : <Skeleton height={180} lines={2} />}
    </View>
  }
  return <View className="home__card forecast-main">
    <View className="forecast-row"><View className="forecast-heading"><Text className="forecast-title">{label}{scopeLabel ? ` ${scopeLabel}` : ''} {coveredCount === f.total_count ? '发电量合计' : '已覆盖电站合计'}</Text><InfoTip {...info} /></View><Button className="forecast-action" onClick={onHistory}>历史趋势 ›</Button></View>
    <Value value={value} />
    <View className="forecast-split forecast-split--tight"><View><Text className="forecast-label">光伏</Text><Text>{fmtEnergy(value == null ? null : solar)}</Text></View><View><Text className="forecast-label">风电</Text><Text>{fmtEnergy(value == null ? null : wind)}</Text></View></View>
    {value != null && <FleetProvinceGrid g={day?.province_grid} on={provinceOn} onToggle={onProvince} />}
    <View className="fleet-status"><Text>容量覆盖 {coverage.toFixed(1)}%</Text><Text>{value == null ? (f.status === 'error' ? '暂不可用' : '准备中') : ''}</Text></View>
    {refreshError && <Text className="forecast-warning" onClick={onReload}>刷新失败 · 点击重试</Text>}
    {value != null && <PowerCurve points={points} id={`fleet-${f.model}-${selected}-${version}`} title="预测功率合计" step={day?.resolution_minutes ?? f.resolution_minutes ?? 60}
      onExport={() => { if (requireLogin()) void exportCsv(`全目录_预测功率合计_${f.date}.csv`, powerCsv(days.length ? days : [f], '预测功率合计')) }} />}
    {days.length > 0 && <OutlookStrip days={days} selected={selected} onSelect={onSelect} title="七天电量" showHorizonNote={false} />}
    {value == null && days.length > 0 && <Text className="forecast-state">该日尚无覆盖电站结果</Text>}
    <View className="forecast-foot fleet-refresh"><Button className="forecast-action" onClick={onReload}>刷新</Button></View>
  </View>
}

export default function Home() {
  useHomeShare()
  const { model, setModel } = useWeatherModel()
  const [scope, setScope] = useState('station')
  const [visible, setVisible] = useState(true)
  const [version, setVersion] = useState(0)
  const [showAllRegions, setShowAllRegions] = useState(false)
  // 地区筛选只在本次浏览内有效：记住了下次进来会看到一个自己忘了开的筛选
  const [provinces, setProvinces] = useState<string[]>([])
  // 风光变化默认看次日；今日仍可从七日条切回。docs/20
  const [fleetDay, setFleetDay] = useState(1)
  // 本站七天预测的选中日；下方 24 小时气象趋势取同一天
  const [stationDay, setStationDay] = useState(0)
  // 气象趋势默认展开，收起只在本次浏览内有效
  const [trendOpen, setTrendOpen] = useState(true)
  const toggleTrend = () => setTrendOpen(v => !v)
  // 省级限电参考默认关；本站与全部电站共用一个开关
  const [provinceOn, setProvinceOnState] = useState(() => { try { return Taro.getStorageSync(PROVINCE_GRID_KEY) === true } catch { return false } })
  const setProvinceOn = (on: boolean) => {
    setProvinceOnState(on)
    try { Taro.setStorageSync(PROVINCE_GRID_KEY, on) } catch { /* 存储不可用时只在本次浏览生效 */ }
  }
  useDidShow(() => { setVisible(true); setVersion(v => v + 1) })
  useDidHide(() => setVisible(false))
  const currentId = useStationStore(s => s.currentId)
  const home = useRequest(() => homeApi.get(currentId ?? undefined), [currentId, model])
  const outlook = useRequest(() => home.data?.station ? stationsApi.outlook(home.data.station.id, 7) : Promise.resolve(null), [home.data?.station?.id])
  const refreshStation = async () => { await Promise.all([home.reload(), outlook.reload()]); setVersion(v => v + 1) }
  // 记一笔实测：只对我的电站。记完服务端已重新拟合，刷新本站预测就能看到订正。docs/19 §三
  const [recording, setRecording] = useState(false)
  // 全目录不认三模式：显式带实际用的模型，免得后端未部署新版时回 400
  const fleet = useRequest(() => api.get<FleetPrediction>('/v1/predictions/fleet', { weather_model: servedModel(model) }), [model])
  const served = servedModel(model)
  const f = fleet.data?.model === served ? fleet.data : null
  // 选中地区后整份按省汇总由服务端给，客户端不自己求和，口径只有一套。docs/17 §二
  const scopeKey = provinces.join(',')
  const scoped = useRequest(() => scopeKey ? api.get<FleetPrediction>('/v1/predictions/fleet', { weather_model: servedModel(model), provinces: scopeKey }) : Promise.resolve(null), [scopeKey, model, f?.generated_at])
  const shownFleet = scopeKey ? (scoped.data?.model === served ? scoped.data : null) : f
  const reloadFleet = async () => { await Promise.all([fleet.reload(), ...(scopeKey ? [scoped.reload()] : [])]) }
  const d = home.data && (!home.data.prediction || home.data.prediction.model === served) ? home.data : null
  useEffect(() => {
    if (!visible || !f) return
    const timer = setTimeout(() => void fleet.reload(), f.updating || ['queued','building'].includes(f.status) ? 8000 : 120000)
    return () => clearTimeout(timer)
  }, [f, visible, fleet.reload])
  usePullDownRefresh(async () => { await Promise.all([refreshStation(), reloadFleet()]); Taro.stopPullDownRefresh() })
  // 全目录不做三模式（三倍坐标）：该视图下选择器里不出现这一项，已选的按服务端实际用的自动模型显示，
  // 切回本站仍是三模式，不改写用户的选择。docs/19 §一
  const options = scope === 'fleet' ? WEATHER_MODELS.filter(m => m.id !== 'ensemble') : WEATHER_MODELS
  const shownModel = scope === 'fleet' ? served : model
  const choose = (index: number) => {
    const next = options[index]?.id
    if (next && next !== shownModel) { setModel(next); setVersion(v => v + 1) }
  }
  const station = d?.station
  const weather = d?.weather
  const p = d?.prediction
  useEffect(() => { setStationDay(0) }, [station?.id, model])
  const trendDay = outlook.data?.days?.[stationDay]
  const fleetDays = f?.days ?? []
  const selectedFleet: FleetDay | undefined = fleetDays[fleetDay]
  const regions = selectedFleet ? (selectedFleet.regions ?? []) : (f?.regions ?? [])
  const basis = scope === 'fleet' ? f?.basis : outlook.data?.basis ?? p?.basis
  const modelText = shownModel === 'best_match' ? `自动${basis?.resolved_model ? ` · ${COMPACT_MODEL[basis.resolved_model] ?? basis.resolved_model}` : ''}` : weatherModelLabel(shownModel)
  const stationMeta = station ? [station.address?.match(/[㐀-鿿]+/g)?.join(' · ') || station.address, station.type === 'wind' ? '风电' : '光伏', `${formatPower(station.capacity).value} ${formatPower(station.capacity).unit}`].filter(Boolean).join(' · ') : ''
  /** 换筛选就换一次 version：功率曲线的 canvas id 带着它，新旧两张图不会撞同一个 id。
   *  模拟器把 canvas 当叠加层按 id 记位置，撞 id 时新图可能画进旧图的位置里。 */
  const applyProvinces = (next: string[]) => {
    setProvinces(next)
    setVersion(v => v + 1)
  }
  const openHistory = () => {
    const query = scopeKey ? `?provinces=${encodeURIComponent(scopeKey)}` : ''
    void Taro.navigateTo({ url: `/pages/fleet-history/index${query}` })
  }
  const regionClick = (province: string) => {
    if (province === UNKNOWN_REGION) return
    const next = provinces.includes(province) ? provinces.filter(x => x !== province) : [...provinces, province]
    // 首次选中滚回页首，让用户看见上面的数字变了；之后的切换不滚。
    // 不做滚动动画：动画期间创建的 canvas 在模拟器里会按动画中途的位置落点
    if (!provinces.length && next.length) void Taro.pageScrollTo({ scrollTop: 0, duration: 0 })
    applyProvinces(next)
  }
  return <View className="home">
    {scope === 'station' && station ? <StationTitleBar name={station.name} status={station.status} own={station.is_own} compact address={stationMeta} onSwitch={() => Taro.switchTab({ url: '/pages/station/index' })} /> : <PageTitleBar title={scope === 'fleet' ? '全目录发电预测' : '发电预测'} />}
    <View className="home__body">
      <View className="forecast-toolbar">
        <SegmentedTabs variant="underline" value={scope} options={[{ value: 'station', label: '本站' }, { value: 'fleet', label: '全部电站' }]} onChange={setScope} />
        <Picker className="forecast-picker" mode="selector" range={options.map(m => m.id === shownModel ? `${m.label}${m.id !== 'ensemble' && basis?.resolved_model ? ` · ${RESOLVED_LABEL[basis.resolved_model] ?? basis.resolved_model}` : ''}${basisShort(basis)}` : `${m.label} · ${m.description}`)} value={options.findIndex(m => m.id === shownModel)} onChange={e => choose(Number(e.detail.value))}>
          <View className="forecast-model"><Text>{modelText}</Text><Icon name="chevronDown" size={14} strokeWidth={1.5} /></View>
        </Picker>
      </View>
      {scope === 'station' ? <>
        {home.status === 'error' ? <ErrorState error={home.error} onRetry={home.reload} /> : d?.has_station === false ? <View className="home__card"><Text>选择或添加一座电站，开始查看预测</Text><Button className="forecast-action" onClick={() => Taro.switchTab({ url: '/pages/station/index' })}>选择电站</Button></View> : !d || !station ? <View className="home__card"><Skeleton height={220} lines={3} /></View> : <>
          <StationForecast key={`${station.id}-${model}`} station={station} p={p} version={version} req={outlook} onReload={refreshStation} refreshError={!!home.refreshError} generatedAt={p?.generated_at} selected={stationDay} onSelect={setStationDay} provinceOn={provinceOn} onProvince={setProvinceOn} onRecord={station.is_own ? () => setRecording(true) : undefined} />
          {d.alert && <AlertBanner title={d.alert.title} description={d.alert.description} onMore={() => Taro.switchTab({ url: '/pages/alert/index' })} />}
          {weather && <View className="home__card home__weather"><SectionHeader icon="cloudSun" title="气象依据" info={{ title: '气象依据', content: '取当前 15 分钟时段的预报值：气温、10 米风速、云量为瞬时值，辐射为对应区间的平均值。发电适宜度按全天气象条件估算，只反映气象，不含设备状态与限电。' }} /><MetricGrid>
            <MetricCard icon="cloudSun" label="天气" metric={formatTemperature(weather.temperature.value)} caption={weather.weather_text ?? undefined} />
            <MetricCard icon="sun" label="辐射" metric={formatRadiation(weather.radiation.value)} />
            <MetricCard icon="wind" iconFill={false} label="10米风速" metric={formatWindSpeed(weather.wind_speed.value)} />
            <MetricCard icon="cloud" label="云量" metric={formatPercent(weather.cloud_cover.value)} />
          </MetricGrid></View>}
          <View className="home__card home__trend">
            <Button className="home__trend-toggle" ariaLabel={trendOpen ? '收起24小时气象趋势' : '展开24小时气象趋势'} onClick={toggleTrend}><View className="forecast-heading"><Icon name="trendingUp" size={18} /><Text>{`24 小时气象趋势${trendDay ? ` · ${fmtDate(trendDay)}` : ''}`}</Text></View><Icon name={trendOpen ? 'chevronUp' : 'chevronDown'} size={16} /></Button>
            {trendOpen && <StationTrend key={`${station.id}-${model}`} stationId={station.id} type={station.type} initial={d.trends} version={version} exportName={station.name} dayOffset={stationDay} compact />}
          </View>
        </>}
      </> : <>
        {fleet.status === 'error' ? <ErrorState error={fleet.error} onRetry={fleet.reload} /> : !f ? <View className="home__card"><Skeleton height={220} lines={3} /></View> : <>
          {!!provinces.length && <RegionFilter names={provinces} onRemove={regionClick} onClear={() => applyProvinces([])} />}
          {selectedFleet && <FleetSignals date={selectedFleet.date} model={model} provinces={provinces} fallbackProvince={regions.find(r => station?.address?.startsWith(r.province))?.province} onProvince={regionClick} refreshKey={f.generated_at} />}
          {scopeKey && scoped.status === 'error' ? <ErrorState error={scoped.error} onRetry={scoped.reload} />
            : !shownFleet ? <View className="home__card"><Skeleton height={220} lines={3} /></View>
            : <FleetForecast f={shownFleet} selected={fleetDay} onSelect={setFleetDay} version={version} onReload={reloadFleet} refreshError={!!(scopeKey ? scoped.refreshError : fleet.refreshError)} provinceOn={provinceOn} onProvince={setProvinceOn} scopeLabel={scopeText(provinces)} onHistory={openHistory} />}
          {!!regions.length && <View className="home__card"><SectionHeader icon="map" title={`区域贡献 · ${selectedFleet ? fmtDate(selectedFleet) : '今日'}`} info={{ title: '区域贡献', content: '按电站所在省份汇总已覆盖电站的日电量，仅含已计算的电站。点击地区即在本页筛选，可多选，再点取消；清除后回到全国。省份不详的一档不参与筛选。' }} />{(showAllRegions ? regions : regions.slice(0,6)).map(r => {
            const on = provinces.includes(r.province)
            return <View className={`forecast-region${on ? ' forecast-region--on' : ''}`} key={r.province} hoverClass="pressed" onClick={() => regionClick(r.province)}>
              <View className="forecast-region__name">{on && <Icon name="checkCircle" size={15} color="#1677ff" />}<Text>{r.province}</Text></View>
              <Text>{energy(r.energy_kwh).value} {energy(r.energy_kwh).unit}</Text>
            </View>
          })}{regions.length > 6 && <View className="forecast-more" onClick={() => setShowAllRegions(v => !v)}>{showAllRegions ? '收起地区' : `查看全部 ${regions.length} 个地区`}</View>}</View>}
        </>}
      </>}
    </View>
    {station?.is_own && <RecordSheet stationId={station.id} capacityKw={station.capacity} visible={recording} onClose={() => setRecording(false)} onDone={s => { void refreshStation(); if (s.entries.some(e => e.status === 'pending')) setTimeout(() => void refreshStation(), 8000) }} />}
  </View>
}
