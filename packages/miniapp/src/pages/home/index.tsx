import { Picker, View, Text } from '@tarojs/components'
import Taro, { useDidShow, useDidHide, usePullDownRefresh } from '@tarojs/taro'
import { useEffect, useState } from 'react'
import { thousands, formatBeijingTime, formatPercent, formatRadiation, formatTemperature, formatWindSpeed, formatPower } from '@enersight/core/format'
import type { FleetPrediction, GenerationPrediction } from '@enersight/core/types'
import { api } from '@/api'
import { homeApi } from '@/api/home'
import { AlertBanner, ErrorState, Icon, MetricCard, MetricGrid, PageTitleBar, SectionHeader, SegmentedTabs, Skeleton, StationTitleBar, TrendChart } from '@/components'
import { useRequest } from '@/hooks/useRequest'
import { useStationStore } from '@/store'
import { useWeatherModel, WEATHER_MODELS, weatherModelLabel } from '@/store/weatherModel'
import { StationTrend } from '@/components/StationTrend'
import { FleetHistory } from '@/components/FleetHistory'
import './index.scss'

function energy(value?: number | null) {
  if (value == null) return { value: '—', unit: 'MWh' }
  const divisor = value >= 1e6 ? 1e6 : value >= 1000 ? 1000 : 1
  return { value: thousands(value / divisor, 1), unit: divisor === 1e6 ? 'GWh' : divisor === 1000 ? 'MWh' : 'kWh' }
}
function Value({ value }: { value?: number | null }) {
  const v = energy(value)
  return <View className="forecast-value"><Text>{v.value}</Text><Text className="forecast-value__unit">{v.unit}</Text></View>
}
function PowerCurve({ prediction, id }: { prediction: GenerationPrediction; id: string }) {
  const values = prediction.power_kw.map(p => p.value)
  const divisor = Math.max(...values.map(v => v ?? 0)) >= 1e6 ? 1e6 : 1000
  return <View className="forecast-curve">
    <View className="forecast-row"><Text className="forecast-title">今日预测功率</Text><Text className="forecast-muted">{divisor === 1e6 ? 'GW' : 'MW'}</Text></View>
    <Text className="forecast-muted">全日均为模型预测 · {prediction.timezone === 'Asia/Shanghai' ? '北京时间' : '场站当地时间'}</Text>
    <TrendChart id={id} key={id} height={174} data={{ values: values.map(v => v == null ? null : v / divisor), unit: divisor === 1e6 ? 'GW' : 'MW', yMax: null }} />
  </View>
}
function FleetSummary({ data, onClick }: { data: FleetPrediction | null; onClick: () => void }) {
  const coverage = data?.total_capacity_kw ? Math.min(100, data.covered_capacity_kw / data.total_capacity_kw * 100) : 0
  const v = energy(data?.energy_kwh)
  return <View className="fleet-short" onClick={onClick}>
    <View><Text className="forecast-title">{data && data.covered_count === data.total_count ? '全目录今日预测合计' : '全目录预测 · 已覆盖合计'}</Text><Text className="forecast-muted">{data ? `装机容量覆盖 ${coverage.toFixed(1)}%` : '正在准备汇总'} · 查看全部场站</Text></View>
    <Text className="fleet-short__value">{v.value} {v.unit} ›</Text>
  </View>
}

export default function Home() {
  const { model, setModel } = useWeatherModel()
  const [scope, setScope] = useState('station')
  const [fleetView, setFleetView] = useState('today')
  const [visible, setVisible] = useState(true)
  const [version, setVersion] = useState(0)
  const [showAllRegions, setShowAllRegions] = useState(false)
  useDidShow(() => { setVisible(true); setVersion(v => v + 1) })
  useDidHide(() => setVisible(false))
  const currentId = useStationStore(s => s.currentId)
  const home = useRequest(() => homeApi.get(currentId ?? undefined), [currentId, model])
  const fleet = useRequest(() => api.get<FleetPrediction>('/v1/predictions/fleet', { weather_model: model }), [model])
  const f = fleet.data?.model === model ? fleet.data : null
  const d = home.data && (!home.data.prediction || home.data.prediction.model === model) ? home.data : null
  useEffect(() => {
    if (!visible || !f) return
    const timer = setTimeout(() => void fleet.reload(), ['queued','building'].includes(f.status) ? 8000 : 120000)
    return () => clearTimeout(timer)
  }, [f, visible, fleet.reload])
  usePullDownRefresh(async () => { await Promise.all([home.reload(), fleet.reload()]); Taro.stopPullDownRefresh() })
  const choose = (index: number) => {
    const next = WEATHER_MODELS[index]?.id
    if (next && next !== model) { setModel(next); setVersion(v => v + 1) }
  }
  const station = d?.station
  const weather = d?.weather
  const p = d?.prediction
  const regions = f?.regions ?? []
  const coverage = f?.total_capacity_kw ? Math.min(100, f.covered_capacity_kw / f.total_capacity_kw * 100) : 0
  const info = () => Taro.showModal({ title: '预测口径', content: (scope === 'fleet' ? f?.assumptions : p?.assumptions)?.join('\n') || '基于气象与场站参数估算，未计入限电、检修与故障影响。', showCancel: false })
  const regionClick = (province: string) => {
    if (province === '地区待补充') return
    Taro.setStorageSync('enersight_prediction_province', province)
    void Taro.switchTab({ url: '/pages/station/index' })
  }
  return <View className="home">
    {scope === 'station' && station ? <StationTitleBar name={station.name} status={station.status} address={station.address ?? '公开场站'} onSwitch={() => Taro.switchTab({ url: '/pages/station/index' })} /> : <PageTitleBar title={scope === 'fleet' ? '全目录发电预测' : '发电预测'} />}
    <View className="home__body">
      <View className="forecast-toolbar"><Text className="forecast-muted">{scope === 'fleet' && fleetView === 'history' ? '留存的模型估算' : `今日 ${(scope === 'fleet' ? f?.date : p?.date)?.slice(5) || '预测'}`}</Text>
        <Picker mode="selector" range={WEATHER_MODELS.map(m => m.label)} value={WEATHER_MODELS.findIndex(m => m.id === model)} onChange={e => choose(Number(e.detail.value))}>
          <View className="forecast-model"><Text>模型：{weatherModelLabel(model)}</Text><Icon name="chevronDown" size={14} strokeWidth={1.5} /></View>
        </Picker>
      </View>
      <SegmentedTabs value={scope} options={[{ value: 'station', label: '本站预测' }, { value: 'fleet', label: '全部场站' }]} onChange={setScope} />
      {scope === 'fleet' && <SegmentedTabs value={fleetView} options={[{value:'today',label:'今日预测'},{value:'history',label:'历史记录'}]} onChange={setFleetView} />}
      {scope === 'fleet' && fleetView === 'history' ? <FleetHistory /> : scope === 'station' ? <>
        {home.status === 'error' ? <ErrorState error={home.error} onRetry={home.reload} /> : !d ? <View className="home__card"><Text className="forecast-muted">正在更新 {weatherModelLabel(model)} 预测…</Text><Skeleton height={180} lines={3} /></View> : <>
          <View className="home__card forecast-main">
            <View className="forecast-row"><Text className="forecast-title">今日发电潜力估算</Text><View onClick={info} className="forecast-help">预测口径 ⓘ</View></View>
            <Value value={p?.energy_kwh} />
            <Text className="forecast-note">气象条件下估算 · 未计入限电、检修</Text>
            <View className="forecast-row forecast-meta"><Text>{station?.type === 'wind' ? '风电' : station?.type === 'solar' ? '光伏' : '场站'} · 装机 {formatPower(station?.capacity).value} {formatPower(station?.capacity).unit}</Text><Text onClick={() => station && Taro.navigateTo({ url: `/pages/station/detail?id=${encodeURIComponent(station.id)}` })}>场站资料 ›</Text></View>
            {p?.energy_kwh != null && <PowerCurve prediction={p} id={`power-${model}-${version}`} />}
            {p?.energy_kwh != null && station?.type === 'wind' && <Text className="forecast-note">按轮毂高度风速估算，低于切入或高于切出风速时预测功率为零。全天 {p.power_kw.filter(point => point.value === 0).length} 小时预测为零，不代表实测停机。</Text>}
            {p?.energy_kwh != null && station?.type === 'solar' && <Text className="forecast-note">光伏主要在白天发电，夜间预测功率为零；曲线为气象模型估算。</Text>}
            {p?.energy_kwh == null && <Text className="forecast-note">{station?.prediction_blocked_reason || (!p ? '预测服务暂未就绪，请稍后刷新。' : '全天气象数据或场站参数不完整，暂不估算日总量。')}</Text>}
            {home.refreshError && <Text className="forecast-warning" onClick={home.reload}>刷新失败，当前保留上次预测 · 点击重试</Text>}
          </View>
          {weather && <View className="home__card"><SectionHeader icon="cloudSun" title="气象依据" /><MetricGrid>
            <MetricCard icon="cloudSun" label="天气" metric={formatTemperature(weather.temperature.value)} caption={weather.weather_text ?? undefined} />
            <MetricCard icon="sun" label="辐射" metric={formatRadiation(weather.radiation.value)} />
            <MetricCard icon="wind" iconFill={false} label="10米风速" metric={formatWindSpeed(weather.wind_speed.value)} />
            <MetricCard icon="cloud" label="云量" metric={formatPercent(weather.cloud_cover.value)} />
          </MetricGrid><Text className="forecast-muted">{d.index?.summary}</Text></View>}
          <FleetSummary data={f} onClick={() => setScope('fleet')} />
          {station && <View className="home__card"><StationTrend key={`${station.id}-${model}`} stationId={station.id} type={station.type} initial={d.trends} version={version} /></View>}
          {d.alert && <AlertBanner title={d.alert.title} description={`${d.alert.description} · 公共预警采用自动模型`} onMore={() => Taro.switchTab({ url: '/pages/alert/index' })} />}
        </>}
      </> : <>
        {fleet.status === 'error' ? <ErrorState error={fleet.error} onRetry={fleet.reload} /> : !f ? <View className="home__card"><Text>正在读取 {weatherModelLabel(model)} 汇总…</Text><Skeleton height={180} /></View> : <>
          <View className="home__card forecast-main">
            <View className="forecast-row"><Text className="forecast-title">{f.covered_count === f.total_count ? '今日发电潜力合计' : '已覆盖场站预测合计'}</Text><View onClick={info} className="forecast-help">口径 ⓘ</View></View>
            <Value value={f.energy_kwh} /><Text className="forecast-note">平台运营目录 · 未计入限电、检修</Text>
            <View className="forecast-split"><View><Text className="forecast-muted">已覆盖光伏</Text><Text>{energy(f.energy_kwh == null ? null : f.solar_kwh).value} {energy(f.energy_kwh == null ? null : f.solar_kwh).unit}</Text></View><View><Text className="forecast-muted">已覆盖风电</Text><Text>{energy(f.energy_kwh == null ? null : f.wind_kwh).value} {energy(f.energy_kwh == null ? null : f.wind_kwh).unit}</Text></View></View>
            <View className="forecast-coverage"><View className="forecast-row"><Text>目录申报容量计算覆盖率</Text><Text>{coverage.toFixed(1)}%</Text></View><View className="forecast-progress"><View style={{ width: `${coverage}%` }} /></View><Text className="forecast-muted">已计算 {thousands(f.covered_count)} / {thousands(f.total_count)} 座 · 非全国覆盖率或准确率</Text></View>
            {['queued','building'].includes(f.status) && <Text className="forecast-note">{f.status === 'queued' ? '后台排队计算中' : '后台正在计算更多区域'}，结果自动更新。</Text>}
            {f.message && <Text className="forecast-warning">{f.message}</Text>}
            {(f.invalid_count > 0 || f.failed_count > 0) && <Text className="forecast-note">口径待核验或参数不足 {f.invalid_count} 座 · 气象待补 {f.failed_count} 座，未计入总量。</Text>}
            {f.duplicate_count > 0 && <Text className="forecast-note">已排除完全重复记录 {f.duplicate_count} 条。</Text>}
            {f.energy_kwh != null && <PowerCurve prediction={f} id={`fleet-${model}-${version}`} />}
          </View>
          {!!regions.length && <View className="home__card"><SectionHeader icon="map" title="区域贡献" /><Text className="forecast-muted">仅含已覆盖场站 · 点击地区查看目录</Text>{(showAllRegions ? regions : regions.slice(0,6)).map(r => <View className="forecast-region" key={r.province} onClick={() => regionClick(r.province)}><Text>{r.province}</Text><Text>{energy(r.energy_kwh).value} {energy(r.energy_kwh).unit} ›</Text></View>)}{regions.length > 6 && <View className="forecast-more" onClick={() => setShowAllRegions(v => !v)}>{showAllRegions ? '收起地区' : `查看全部 ${regions.length} 个地区`}</View>}</View>}
          <Text className="forecast-note">区域汇总采用 1°气象网格与默认设备参数，适合观察整体规模；本站预测使用本站位置，二者可能存在近似差异。</Text>
        </>}
      </>}
      {!(scope === 'fleet' && fleetView === 'history') && <Text className="forecast-muted">{(scope === 'fleet' ? f?.generated_at : p?.generated_at) ? `计算于 ${formatBeijingTime(scope === 'fleet' ? f?.generated_at : p?.generated_at)}（北京时间）` : ''}</Text>}
      <View className="forecast-footer"><Text onClick={() => { void home.reload(); void fleet.reload() }}>刷新预测</Text><Text>预测不等于实际并网电量</Text></View>
    </View>
  </View>
}
