import { useHomeShare } from '@/hooks/useAppShare'
import { Button, Picker, View, Text } from '@tarojs/components'
import Taro, { useDidShow, useDidHide, usePullDownRefresh } from '@tarojs/taro'
import { useEffect, useState } from 'react'
import { powerChartUnit, thousands, formatBeijingTime, formatPercent, formatRadiation, formatTemperature, formatWindSpeed, formatPower } from '@enersight/core/format'
import type { DailyOutlook, FleetDay, FleetPrediction, ForecastBasis, GenerationPrediction, StationOutlook, StationSummary } from '@enersight/core/types'
import { api } from '@/api'
import { homeApi } from '@/api/home'
import { stationsApi } from '@/api/stations'
import { AlertBanner, ErrorState, Icon, InfoTip, MetricCard, MetricGrid, OutlookStrip, PageTitleBar, SectionHeader, SegmentedTabs, Skeleton, StationTitleBar, TrendChart, dayLabel } from '@/components'
import { ApiError } from '@enersight/core/api'
import { useRequest } from '@/hooks/useRequest'
import { useStationStore } from '@/store'
import { useWeatherModel, WEATHER_MODELS, weatherModelLabel } from '@/store/weatherModel'
import { StationTrend } from '@/components/StationTrend'
import './index.scss'

const RESOLVED_LABEL: Record<string, string> = { ecmwf_ifs: 'ECMWF IFS 9 km', ncep_gfs013: 'GFS 0.13°', ncep_gfs025: 'GFS 0.25°', dwd_icon: 'ICON 13 km' }
const DISCLAIMER = '预测不等于实际并网电量，未计入检修与故障。'

function energy(value?: number | null) {
  if (value == null) return { value: '—', unit: 'MWh' }
  const divisor = value >= 1e6 ? 1e6 : value >= 1000 ? 1000 : 1
  return { value: thousands(value / divisor, 1), unit: divisor === 1e6 ? 'GWh' : divisor === 1000 ? 'MWh' : 'kWh' }
}
function fmtEnergy(value?: number | null) { const v = energy(value); return `${v.value} ${v.unit}` }
function fmtDate(d: { date: string; weekday: number; lead_days: number }) { return `${dayLabel(d)} ${d.date.slice(5).replace('-', '/')}` }
/** 起报一句话：顶栏只放这一句；拿不到起报时不猜，只给拉取时间。docs/17 §二 */
function shortTime(iso: string) {
  const t = formatBeijingTime(iso)
  const today = formatBeijingTime(new Date().toISOString()).slice(0, 5)
  return t.startsWith(today) ? t.slice(6) : t
}
function basisShort(basis?: ForecastBasis | null) {
  if (!basis) return ''
  return basis.issued_at ? ` · 起报 ${shortTime(basis.issued_at)}` : ` · 拉取 ${shortTime(basis.fetched_at)}`
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
function PowerCurve({ points, grid, id, title, step = 60 }: { points: { time: string; value: number | null }[]; grid?: { time: string; value: number | null }[] | null; id: string; title: string; step?: number }) {
  const values = points.map(p => p.value)
  const unit = powerChartUnit(values)
  const [table, setTable] = useState(false)
  const gridByTime = new Map(grid?.map(p => [p.time, p.value]))
  const compare = grid ? points.map(p => gridByTime.get(p.time) ?? null) : undefined
  return <View className="forecast-curve">
    <View className="forecast-row"><Text className="forecast-subtitle">{title}</Text><Text className="forecast-unit">{unit.label}</Text></View>
    {grid && <View className="forecast-series"><Text style={{ color: '#1677ff' }}>可发</Text><Text style={{ color: '#16a34a' }}>预计上网</Text></View>}
    <TrendChart id={id} key={id} height={190} data={{ values: values.map(v => v == null ? null : v / unit.divisor), comparison: compare?.map(v => v == null ? null : v / unit.divisor), times: points.map(p => p.time), unit: unit.label, yMax: null, stepMinutes: step }} />
    <Button className="forecast-action" onClick={() => setTable(v => !v)}>{table ? '收起数据明细' : '查看数据明细'}</Button>
    {table && <View className="forecast-table">{points.map((p, i) => <View key={p.time} className="forecast-row"><Text>{p.time.slice(11,16)}</Text><Text>可发 {p.value == null ? '—' : (p.value / unit.divisor).toFixed(2)} {unit.label}{compare ? ` · 上网 ${compare[i] == null ? '—' : (compare[i]! / unit.divisor).toFixed(2)} ${unit.label}` : ''}</Text></View>)}</View>}
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

/** 本站：今日与未来 7 天合成一张卡，一个大数字、一条 7 天横条、一张曲线。 */
function StationForecast({ station, p, version, onReload, refreshError, generatedAt, req }: {
  station: StationSummary; p: GenerationPrediction | null | undefined; version: number; onReload: () => void; refreshError: boolean; generatedAt?: string; req: { data: StationOutlook | null; status: string; error: ApiError | null; reload: () => Promise<void>; refreshError: ApiError | null }
}) {
  const [selected, setSelected] = useState(0)
  const days = req.data?.days ?? []
  const day = days[selected]
  const value = day ? day.energy_kwh : p?.energy_kwh
  const points = day ? day.power_kw : p?.power_kw
  const label = day ? fmtDate(day) : '今日'
  const peak = points ? peakOf(points) : null
  const typeText = station.type === 'wind' ? '风电' : '光伏'
  const cap = formatPower(station.capacity)
  const info = {
    title: '预测口径',
    content: [
      ...(req.data?.assumptions ?? p?.assumptions ?? []),
      station.type === 'wind' ? '风电按轮毂高度风速与通用功率曲线估算，低于切入或高于切出风速时功率为零，不代表实测停机。' : '光伏按倾斜面辐射与 PVWatts 估算，夜间功率为零。',
      '七天功率曲线均按 15 分钟计算，采用电站当地时间。',
      basisDetail(req.data?.basis ?? p?.basis),
      DISCLAIMER,
    ].join('\n'),
  }
  return <View className="home__card forecast-main">
    <View className="forecast-row"><Text className="forecast-title">{label} 发电量估算</Text><InfoTip {...info} /></View>
    {station.prediction_blocked_reason ? <View className="forecast-empty"><Text>{station.prediction_blocked_reason}，暂不估算电量</Text><Button className="forecast-action" onClick={() => Taro.switchTab({ url: '/pages/station/index' })}>选择其他电站</Button></View> : <Value value={value} />}
    <GridSplit p={day ?? p} />
    <View className="forecast-row forecast-meta"><Text>{typeText} · 装机 {cap.value} {cap.unit}{station.is_own ? ' · 自建' : ''}</Text><Text className="forecast-link" onClick={() => Taro.navigateTo({ url: `/pages/station/detail?id=${encodeURIComponent(station.id)}` })}>电站资料 ›</Text></View>
    {req.status === 'error' ? <ErrorState error={req.error!} onRetry={req.reload} />
      : req.status !== 'success' ? <Skeleton height={120} lines={2} />
      : <OutlookStrip days={days.map(d => ({ ...d, caption: d.weather_text, level: d.index_level, score: d.index_score }))} selected={selected} onSelect={setSelected} />}
    {value != null && points && <PowerCurve points={points} grid={day ? day.grid_power_kw : p?.grid_power_kw} id={`power-${station.id.replace(/[^a-zA-Z0-9]/g, '')}-${selected}-${version}`} title={`${label} 预测功率`} step={day ? day.resolution_minutes : (p?.resolution_minutes ?? 60)} />}
    {value != null && peak && <Text className="forecast-caption">峰值 {formatPower(peak.value).value} {formatPower(peak.value).unit} · {peak.time}{day?.weather_text ? ` · 日间 ${day.weather_text}` : ''}</Text>}
    {value == null && !station.prediction_blocked_reason && <Text className="forecast-state">{station.prediction_blocked_reason ? `${station.prediction_blocked_reason}，暂不估算日电量` : !p && !day ? '预测服务暂未就绪，请稍后刷新' : '气象数据或电站参数不完整，暂不估算'}</Text>}
    {(refreshError || req.refreshError) && <Text className="forecast-warning" onClick={onReload}>刷新失败，当前保留上次预测 · 点击重试</Text>}
    {(day?.estimated ?? p?.estimated) && <Text className="forecast-warning">部分气象输入使用补值或降级估算</Text>}
    <View className="forecast-foot"><Text>预测计算于 {formatBeijingTime(req.data?.generated_at ?? generatedAt)}</Text><Text className="forecast-link" onClick={onReload}>刷新</Text></View>
  </View>
}

/** 全目录：同样一张卡。选中日由父级持有，区域贡献要跟着切。 */
function FleetForecast({ f, selected, onSelect, version, onReload, refreshError }: { f: FleetPrediction; selected: number; onSelect: (i: number) => void; version: number; onReload: () => void; refreshError: boolean }) {
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
    <View className="forecast-row"><Text className="forecast-title">{label} {coveredCount === f.total_count ? '发电量合计' : '已覆盖电站合计'}</Text><InfoTip {...info} /></View>
    <Value value={value} />
    <View className="forecast-split forecast-split--tight"><View><Text className="forecast-label">光伏</Text><Text>{fmtEnergy(value == null ? null : solar)}</Text></View><View><Text className="forecast-label">风电</Text><Text>{fmtEnergy(value == null ? null : wind)}</Text></View></View>
    <View className="fleet-status"><Text>容量覆盖 {coverage.toFixed(1)}%</Text><Text>{value == null ? (f.status === 'error' ? '暂不可用' : '准备中') : ''}</Text></View>
    {refreshError && <Text className="forecast-warning" onClick={onReload}>刷新失败 · 点击重试</Text>}
    {days.length > 0 && <OutlookStrip days={days} selected={selected} onSelect={onSelect} title="七天电量" showHorizonNote={false} />}
    {value != null && <PowerCurve points={points} id={`fleet-${f.model}-${selected}-${version}`} title={`${label} 预测功率合计`} step={day?.resolution_minutes ?? f.resolution_minutes ?? 60} />}
    {value == null && days.length > 0 && <Text className="forecast-state">该日尚无覆盖电站结果</Text>}
    <View className="forecast-foot fleet-refresh"><Button className="forecast-action" onClick={onReload}>刷新</Button></View>
  </View>
}

function FleetSummary({ data, onClick }: { data: FleetPrediction | null; onClick: () => void }) {
  const coverage = data?.total_capacity_kw ? Math.min(100, data.covered_capacity_kw / data.total_capacity_kw * 100) : 0
  const v = energy(data?.energy_kwh)
  return <View className="fleet-short" hoverClass="pressed" onClick={onClick}>
    <View><Text className="forecast-title">全目录今日合计</Text><Text className="forecast-caption">{data ? `目录容量覆盖 ${coverage.toFixed(1)}%` : '正在准备汇总'}</Text></View>
    <Text className="fleet-short__value">{`${v.value} ${v.unit} ›`}</Text>
  </View>
}

export default function Home() {
  useHomeShare()
  const { model, setModel } = useWeatherModel()
  const [scope, setScope] = useState('station')
  const [visible, setVisible] = useState(true)
  const [version, setVersion] = useState(0)
  const [showAllRegions, setShowAllRegions] = useState(false)
  const [fleetDay, setFleetDay] = useState(0)
  useDidShow(() => { setVisible(true); setVersion(v => v + 1) })
  useDidHide(() => setVisible(false))
  const currentId = useStationStore(s => s.currentId)
  const home = useRequest(() => homeApi.get(currentId ?? undefined), [currentId, model])
  const outlook = useRequest(() => home.data?.station ? stationsApi.outlook(home.data.station.id, 7) : Promise.resolve(null), [home.data?.station?.id])
  const refreshStation = async () => { await Promise.all([home.reload(), outlook.reload()]); setVersion(v => v + 1) }
  const fleet = useRequest(() => api.get<FleetPrediction>('/v1/predictions/fleet', { weather_model: model }), [model])
  const f = fleet.data?.model === model ? fleet.data : null
  const d = home.data && (!home.data.prediction || home.data.prediction.model === model) ? home.data : null
  useEffect(() => {
    if (!visible || !f) return
    const timer = setTimeout(() => void fleet.reload(), f.updating || ['queued','building'].includes(f.status) ? 8000 : 120000)
    return () => clearTimeout(timer)
  }, [f, visible, fleet.reload])
  usePullDownRefresh(async () => { await Promise.all([refreshStation(), fleet.reload()]); Taro.stopPullDownRefresh() })
  const choose = (index: number) => {
    const next = WEATHER_MODELS[index]?.id
    if (next && next !== model) { setModel(next); setVersion(v => v + 1) }
  }
  const station = d?.station
  const weather = d?.weather
  const p = d?.prediction
  const fleetDays = f?.days ?? []
  const selectedFleet: FleetDay | undefined = fleetDays[fleetDay]
  const regions = selectedFleet ? (selectedFleet.regions ?? []) : (f?.regions ?? [])
  const basis = scope === 'fleet' ? f?.basis : outlook.data?.basis ?? p?.basis
  const regionClick = (province: string) => {
    if (province === '地区待补充') return
    Taro.setStorageSync('enersight_prediction_province', province)
    void Taro.switchTab({ url: '/pages/station/index' })
  }
  return <View className="home">
    {scope === 'station' && station ? <StationTitleBar name={station.name} status={station.status} own={station.is_own} address={station.address ?? (station.is_own ? '仅本账号可见' : '公开电站')} onSwitch={() => Taro.switchTab({ url: '/pages/station/index' })} /> : <PageTitleBar title={scope === 'fleet' ? '全目录发电预测' : '发电预测'} />}
    <View className="home__body">
      <View className="forecast-toolbar">
        <Picker mode="selector" range={WEATHER_MODELS.map(m => m.label)} value={WEATHER_MODELS.findIndex(m => m.id === model)} onChange={e => choose(Number(e.detail.value))}>
          <View className="forecast-model"><Text>{`${model === 'best_match' ? (basis?.resolved_model ? `自动 · ${RESOLVED_LABEL[basis.resolved_model] ?? basis.resolved_model}` : '自动选择 · 模型待确认') : weatherModelLabel(model)}${scope === 'fleet' ? '' : basisShort(basis)}`}</Text><Icon name="chevronDown" size={14} strokeWidth={1.5} /></View>
        </Picker>
        {scope === 'fleet' && <View className="fleet-history-link" hoverClass="pressed" onClick={() => Taro.navigateTo({ url: '/pages/fleet-history/index' })}><Icon name="trendingUp" size={15} strokeWidth={1.5} /><Text>历史趋势</Text></View>}
      </View>
      <SegmentedTabs value={scope} options={[{ value: 'station', label: '本站' }, { value: 'fleet', label: '全部电站' }]} onChange={setScope} />
      {scope === 'station' ? <>
        {home.status === 'error' ? <ErrorState error={home.error} onRetry={home.reload} /> : d?.has_station === false ? <View className="home__card"><Text>选择或添加一座电站，开始查看预测</Text><Button className="forecast-action" onClick={() => Taro.switchTab({ url: '/pages/station/index' })}>选择电站</Button></View> : !d || !station ? <View className="home__card"><Skeleton height={220} lines={3} /></View> : <>
          <StationForecast key={`${station.id}-${model}`} station={station} p={p} version={version} req={outlook} onReload={refreshStation} refreshError={!!home.refreshError} generatedAt={p?.generated_at} />
          {d.alert && <AlertBanner title={d.alert.title} description={d.alert.description} onMore={() => Taro.switchTab({ url: '/pages/alert/index' })} />}
          {weather && <View className="home__card"><SectionHeader icon="cloudSun" title="气象依据" info={{ title: '气象依据', content: '取当前 15 分钟时段的预报值：气温、10 米风速、云量为瞬时值，辐射为对应区间的平均值。发电适宜度按全天气象条件估算，只反映气象，不含设备状态与限电。' }} /><MetricGrid>
            <MetricCard icon="cloudSun" label="天气" metric={formatTemperature(weather.temperature.value)} caption={weather.weather_text ?? undefined} />
            <MetricCard icon="sun" label="辐射" metric={formatRadiation(weather.radiation.value)} />
            <MetricCard icon="wind" iconFill={false} label="10米风速" metric={formatWindSpeed(weather.wind_speed.value)} />
            <MetricCard icon="cloud" label="云量" metric={formatPercent(weather.cloud_cover.value)} />
          </MetricGrid>{d.index?.summary && <Text className="forecast-caption">{d.index.summary}</Text>}</View>}
          <FleetSummary data={f} onClick={() => setScope('fleet')} />
          <View className="home__card"><StationTrend key={`${station.id}-${model}`} stationId={station.id} type={station.type} initial={d.trends} version={version} /></View>
        </>}
      </> : <>
        {fleet.status === 'error' ? <ErrorState error={fleet.error} onRetry={fleet.reload} /> : !f ? <View className="home__card"><Skeleton height={220} lines={3} /></View> : <>
          <FleetForecast f={f} selected={fleetDay} onSelect={setFleetDay} version={version} onReload={fleet.reload} refreshError={!!fleet.refreshError} />
          {!!regions.length && <View className="home__card"><SectionHeader icon="map" title={`区域贡献 · ${selectedFleet ? fmtDate(selectedFleet) : '今日'}`} info={{ title: '区域贡献', content: '按电站所在省份汇总已覆盖电站的日电量，仅含已计算的电站。点击地区可进入该省的电站目录。' }} />{(showAllRegions ? regions : regions.slice(0,6)).map(r => <View className="forecast-region" key={r.province} hoverClass="pressed" onClick={() => regionClick(r.province)}><Text>{r.province}</Text><Text>{energy(r.energy_kwh).value} {energy(r.energy_kwh).unit} ›</Text></View>)}{regions.length > 6 && <View className="forecast-more" onClick={() => setShowAllRegions(v => !v)}>{showAllRegions ? '收起地区' : `查看全部 ${regions.length} 个地区`}</View>}</View>}
        </>}
      </>}
    </View>
  </View>
}
