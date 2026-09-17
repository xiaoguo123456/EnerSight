import { useAppShare } from '@/hooks/useAppShare'
import { Button, View, Text, Input, Picker, Map, Switch, Textarea } from '@tarojs/components'
import Taro, { useRouter } from '@tarojs/taro'
import { useEffect, useRef, useState } from 'react'
import type { CurtailmentRule, Mounting, PowerCurvePoint, StationSummary, StationType, TurbineClass, UpdateStationRequest } from '@enersight/core/types'
import { ApiError } from '@enersight/core/api'
import { capacityMwInput, mwToKw, thousands } from '@enersight/core/format'
import { homeApi } from '@/api/home'
import { stationsApi } from '@/api/stations'
import { Icon, InfoTip, PageHeader, SegmentedTabs, Skeleton } from '@/components'
import { useStationStore } from '@/store'
import { useAuthStore } from '@/store/auth'
import { decodeRouteParam } from '@/route'
import { Disclosure } from '@/components/Disclosure'
import { getSafeArea } from '@/hooks/useSafeArea'
import './form.scss'

/**
 * 自建电站：新建与编辑共用。docs/17 §一、§四
 * 经纬度三种来源：当前定位、地图选点（都是 GCJ-02）、手动输入（默认 WGS84，可切 GCJ-02）。
 * 提交带 coord，坐标转换由服务端完成，客户端不做换算；小程序里用 <Map> 预览点位。
 */

type Coord = 'wgs84' | 'gcj02'
type Origin = 'manual' | 'locate' | 'choose'
type DaysPreset = 'all' | 'weekday' | 'weekend'
interface WindowForm { start: number; end: number; limit: string; days: DaysPreset; originalDays?: number[] }
interface Form {
  name: string; type: StationType; capacity: string
  latitude: string; longitude: string; coord: Coord; origin: Origin
  tilt: string; azimuth: string; hub_height: string
  turbine: TurbineClass; curveText: string; mounting: Mounting; bifacial: boolean
  mode: 'none' | 'ratio' | 'schedule'; ratio: string; windows: WindowForm[]
}

const EMPTY: Form = { name: '', type: 'solar', capacity: '', latitude: '', longitude: '', coord: 'wgs84', origin: 'manual', tilt: '', azimuth: '', hub_height: '', turbine: 'generic', curveText: '', mounting: 'fixed', bifacial: false, mode: 'none', ratio: '', windows: [] }
const TURBINES: { value: TurbineClass; label: string }[] = [
  { value: 'generic', label: '通用功率曲线（额定 12 m/s）' },
  { value: 'low_wind', label: '低风速机型（额定 9.5 m/s）' },
  { value: 'medium_wind', label: '中风速机型（额定 11 m/s）' },
  { value: 'high_wind', label: '高风速机型（额定 12.5 m/s）' },
  { value: 'custom', label: '自定义功率曲线' },
]
const CURVE_PLACEHOLDER = '每行一对：轮毂风速 m/s,出力 %\n3,0\n6,15\n9,60\n12,100\n25,100'

function parseCurve(text: string): PowerCurvePoint[] | string {
  const points: PowerCurvePoint[] = []
  for (const raw of text.split(/\n+/)) {
    const line = raw.trim()
    if (!line) continue
    const [a, b] = line.split(/[,，\s]+/)
    const v = Number(a); const pct = Number(b)
    if (!Number.isFinite(v) || !Number.isFinite(pct)) return `无法识别：${line}`
    if (v < 0 || v > 60 || pct < 0 || pct > 100) return `超出范围：${line}`
    points.push({ v, p: pct })
  }
  if (points.length < 3) return '功率曲线至少 3 个点'
  for (let i = 1; i < points.length; i++) if (points[i]!.v <= points[i - 1]!.v) return '功率曲线的风速必须递增'
  return points
}
const HOURS = Array.from({ length: 25 }, (_, i) => `${String(i).padStart(2, '0')}:00`)
const DAY_PRESETS: { value: DaysPreset; label: string; weekdays: number[] }[] = [
  { value: 'all', label: '每天', weekdays: [] }, { value: 'weekday', label: '工作日', weekdays: [1, 2, 3, 4, 5] }, { value: 'weekend', label: '周末', weekdays: [6, 7] },
]
const IS_WEAPP = process.env.TARO_ENV === 'weapp'
const LOCATION_INFO = { title: '位置与坐标系', content: '当前定位与地图选点自动按 GCJ-02 提交。手动输入请按读数来源选择坐标系：GPS 设备为 WGS84；高德、腾讯地图为 GCJ-02。百度 BD-09 坐标暂不支持，请先转换。选错不会报错，只会让电站与云图错位几百米，保存前请在地图预览里核对点位。' }
const MODEL_INFO = { title: '出力模型参数', content: '不填用默认值：光伏倾角取纬度、方位角 180° 正南；风机轮毂高度 100 m。\n风电：机型决定切入、额定、切出风速与曲线形状；自定义曲线按「风速,出力%」逐点给出，末点之后视作切出。空气密度按站点气压与气温按对应时刻修正，高海拔出力会低于平原。\n光伏：单轴跟踪按南北向水平轴、最大转角 60°、含背轨估算，倾角与方位角不再生效；双面组件按双面率 0.7、地面反射率 0.2 估算，增益约 5–12%，未用实测校准。' }
const LIMIT_INFO = { title: '出力约束', content: '限电或检修只影响「预计上网电量」，可发电量与发电适宜度仍按气象条件估算。固定比例：全天出力按比例折减。分时段上限：时段内出力封顶为装机容量的百分比，0 表示停机；结束时刻早于开始表示跨零点，适用星期按开始日，例如周五 22–02 含周六凌晨；多条时段重叠取最低。规则由你自行填写，不来自电网调度。' }
const FORM_INFO = { title: '自建电站', content: '仅本账号可见，最多 10 座。参与发电预测、预警、分析报告与卫星影像归档，不进入全目录汇总。装机容量按交流侧填写，光伏直流侧按容配比换算。' }

function num(s: string): number | null {
  const v = Number(s)
  return s.trim() === '' || Number.isNaN(v) ? null : v
}

function presetOf(weekdays: number[]): DaysPreset {
  const key = [...weekdays].sort().join(',')
  return key === '1,2,3,4,5' ? 'weekday' : key === '6,7' ? 'weekend' : 'all'
}

function fromStation(s: StationSummary, form: Form): Form {
  const rule = s.curtailment
  return {
    ...form, name: s.name, type: s.type, capacity: capacityMwInput(s.capacity),
    tilt: s.tilt == null ? '' : String(s.tilt), azimuth: s.azimuth == null ? '' : String(s.azimuth),
    hub_height: s.hub_height == null ? '' : String(s.hub_height),
    latitude: s.latitude.toFixed(5), longitude: s.longitude.toFixed(5), coord: 'gcj02', origin: 'manual',
    turbine: s.turbine_class ?? 'generic',
    curveText: (s.power_curve ?? []).map((pt) => `${pt.v},${pt.p}`).join('\n'),
    mounting: s.mounting ?? 'fixed',
    bifacial: !!s.bifacial,
    mode: rule ? rule.mode : 'none',
    ratio: rule?.mode === 'ratio' && rule.ratio_percent != null ? String(rule.ratio_percent) : '',
    windows: rule?.mode === 'schedule' ? (rule.windows ?? []).map((w) => ({ start: w.start_hour, end: w.end_hour, limit: String(w.limit_percent), days: presetOf(w.weekdays ?? []), originalDays: w.weekdays ?? [] })) : [],
  }
}

type FormSection = 'basic' | 'location' | 'model' | 'limits'
type FormError = { section: FormSection | 'submit'; message: string }
const invalid = (section: FormSection, message: string): FormError => ({ section, message })

function validate(f: Form): FormError | null {
  if (!f.name.trim()) return invalid('basic', '请填写电站名称')
  if (f.name.trim().length > 64) return invalid('basic', '名称不超过 64 个字')
  const cap = num(f.capacity); if (cap === null || cap <= 0) return invalid('basic', '装机容量需大于 0')
  if (!/^\d+(\.\d{1,3})?$/.test(f.capacity.trim())) return invalid('basic', '装机容量最多保留 3 位小数')
  const lat = num(f.latitude); const lon = num(f.longitude)
  if (lat === null || lon === null) return invalid('location', '请填写经纬度，或使用定位 / 地图选点')
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return invalid('location', '经纬度超出范围')
  if (f.type === 'solar') {
    const tilt = num(f.tilt); if (f.tilt && (tilt === null || tilt < 0 || tilt > 90)) return invalid('model', '倾角需在 0 ~ 90°')
    const az = num(f.azimuth); if (f.azimuth && (az === null || az < 0 || az > 360)) return invalid('model', '方位角需在 0 ~ 360°')
  } else {
    const hub = num(f.hub_height); if (f.hub_height && (hub === null || hub <= 0 || hub > 200)) return invalid('model', '轮毂高度需在 0 ~ 200 m')
    if (f.turbine === 'custom') { const parsed = parseCurve(f.curveText); if (typeof parsed === 'string') return invalid('model', parsed) }
  }
  if (f.mode === 'ratio') { const r = num(f.ratio); if (r === null || r <= 0 || r > 100) return invalid('limits', '限电比例需在 0 ~ 100%') }
  if (f.mode === 'schedule') {
    if (!f.windows.length) return invalid('limits', '至少添加一条限电时段')
    for (const w of f.windows) {
      const l = num(w.limit); if (l === null || l < 0 || l > 100) return invalid('limits', '出力上限需在 0 ~ 100%')
      if (w.start === w.end) return invalid('limits', '时段起止不能相同')
    }
  }
  return null
}

function toRule(f: Form): CurtailmentRule | null {
  if (f.mode === 'ratio') return { mode: 'ratio', ratio_percent: num(f.ratio)!, windows: [] }
  if (f.mode === 'schedule') return { mode: 'schedule', ratio_percent: null, windows: f.windows.map((w) => ({ start_hour: w.start, end_hour: w.end, limit_percent: num(w.limit)!, weekdays: w.originalDays ?? DAY_PRESETS.find((d) => d.value === w.days)!.weekdays })) }
  return null
}

export default function StationForm() {
  useAppShare()
  const { params } = useRouter()
  const id = decodeRouteParam(params.id)
  const editing = !!id
  const [form, setForm] = useState<Form>(EMPTY)
  const [modelOpen, setModelOpen] = useState(false)
  const [limitsOpen, setLimitsOpen] = useState(false)
  const [formError, setFormError] = useState<FormError | null>(null)
  useEffect(() => { setFormError(null) }, [form])
  const initial = useRef<Form | null>(null)
  const [loading, setLoading] = useState(editing)
  const [failed, setFailed] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [locating, setLocating] = useState(false)
  const { currentId, setCurrent, remember } = useStationStore()

  useEffect(() => {
    // 自建电站必须登录；从分享或历史页面直接进入时转到登录页。docs/09 §4.3
    if (!useAuthStore.getState().loggedIn) void Taro.redirectTo({ url: '/pages/login/index' })
  }, [])

  useEffect(() => {
    if (!editing) return
    homeApi.detail(id).then((d) => {
      if (!d.station.is_own) { setFailed('公开电站由平台维护，不能编辑'); return }
      const loaded = fromStation(d.station, EMPTY)
      initial.current = loaded
      setForm(loaded)
      setLimitsOpen(loaded.mode !== 'none')
      setModelOpen(loaded.turbine === 'custom')
    }).catch(() => setFailed('电站加载失败')).finally(() => setLoading(false))
  }, [id, editing])

  const set = (k: keyof Form) => (e: { detail: { value: string } }) => setForm((f) => ({ ...f, [k]: e.detail.value }))
  const setCoordField = (k: 'latitude' | 'longitude') => (e: { detail: { value: string } }) => setForm((f) => ({ ...f, [k]: e.detail.value, origin: 'manual' }))
  const patch = (p: Partial<Form>) => setForm((f) => ({ ...f, ...p }))
  const setWindow = (i: number, p: Partial<WindowForm>) => setForm((f) => ({ ...f, windows: f.windows.map((w, j) => j === i ? { ...w, ...p } : w) }))

  const locate = async () => {
    setLocating(true)
    try {
      const r = await Taro.getLocation({ type: 'gcj02' })
      patch({ latitude: r.latitude.toFixed(5), longitude: r.longitude.toFixed(5), coord: 'gcj02', origin: 'locate' })
    } catch {
      const answer = await Taro.showModal({ title: '无法获取位置', content: '可以在地图上选点，或手动输入经纬度。若已拒绝授权，可在设置中重新打开。', confirmText: '去设置', cancelText: '手动输入' })
      if (answer.confirm) { try { await Taro.openSetting() } catch { /* 非微信环境忽略 */ } }
    } finally { setLocating(false) }
  }
  const choose = async () => {
    try {
      const r = await Taro.chooseLocation({})
      patch({ latitude: r.latitude.toFixed(5), longitude: r.longitude.toFixed(5), coord: 'gcj02', origin: 'choose', ...(form.name ? {} : { name: r.name || '' }) })
    } catch { /* 用户取消 */ }
  }

  const submit = async () => {
    const err = validate(form)
    if (err) {
      setFormError(err)
      if (err.section === 'model') setModelOpen(true)
      if (err.section === 'limits') setLimitsOpen(true)
      Taro.nextTick(() => void Taro.pageScrollTo({ selector: `#sform-${err.section}`, offsetTop: -getSafeArea().navBarHeight, duration: 200 }))
      return
    }
    setFormError(null)
    // 按千瓦习惯多填三个零会差 1000 倍，超大容量先请用户确认单位
    const mw = num(form.capacity)!
    if (mw > 3000 && (!editing || form.capacity !== initial.current?.capacity)) {
      const answer = await Taro.showModal({ title: '确认装机容量', content: `装机 ${thousands(mw)} MW，单位为兆瓦，确认无误？`, confirmText: '确认' })
      if (!answer.confirm) return
    }
    setSaving(true)
    const body = {
      name: form.name.trim(), type: form.type, capacity: mwToKw(mw),
      latitude: num(form.latitude)!, longitude: num(form.longitude)!, coord: form.coord,
      tilt: form.type === 'solar' ? num(form.tilt) : null, azimuth: form.type === 'solar' ? num(form.azimuth) : null,
      hub_height: form.type === 'wind' ? num(form.hub_height) : null,
      turbine_class: form.type === 'wind' ? form.turbine : null,
      power_curve: form.type === 'wind' && form.turbine === 'custom' ? (parseCurve(form.curveText) as PowerCurvePoint[]) : null,
      mounting: form.type === 'solar' ? form.mounting : null,
      bifacial: form.type === 'solar' ? form.bifacial : null,
      curtailment: toRule(form),
    }
    try {
      if (editing) {
        // 只提交改动，避免无关编辑清空参数或重复转换坐标。
        const patchBody: UpdateStationRequest = { ...body }
        const before = initial.current
        if (before) {
          const fields = { name: 'name', type: 'type', capacity: 'capacity', tilt: 'tilt', azimuth: 'azimuth', hub_height: 'hub_height', turbine_class: 'turbine', power_curve: 'curveText', mounting: 'mounting', bifacial: 'bifacial' } as const
          for (const [key, field] of Object.entries(fields)) {
            if (form[field] === before[field] && (form.type === before.type || key === 'name' || key === 'capacity')) delete patchBody[key as keyof Omit<UpdateStationRequest, 'coord'>]
          }
          if (form.latitude === before.latitude && form.longitude === before.longitude && form.coord === before.coord) {
            delete patchBody.latitude; delete patchBody.longitude
          }
          if (JSON.stringify(toRule(form)) === JSON.stringify(toRule(before))) delete patchBody.curtailment
        }
        const s = await stationsApi.update(id, patchBody)
        remember(s)
        void Taro.showToast({ title: '已保存', icon: 'success' })
      } else {
        const s = await stationsApi.create(body)
        remember(s); setCurrent(s.id)
        Taro.setStorageSync('enersight_station_tab', 'mine')
        void Taro.showToast({ title: '已添加并设为当前电站', icon: 'none' })
      }
      setTimeout(() => Taro.navigateBack(), 700)
    } catch (e) {
      setFormError({ section: 'submit', message: e instanceof ApiError ? e.message : '保存失败，请重试' })
    } finally { setSaving(false) }
  }

  const remove = async () => {
    const answer = await Taro.showModal({ title: '删除电站', content: '将同时删除它的预警与发电记录，删除后可重新添加。', confirmText: '删除', confirmColor: '#ef4444' })
    if (!answer.confirm) return
    try {
      await stationsApi.remove(id)
      if (currentId === id) { try { Taro.removeStorageSync('enersight_current_public_station') } catch { /* ignore */ } useStationStore.setState({ currentId: null }) }
      Taro.setStorageSync('enersight_station_tab', 'mine')
      void Taro.showToast({ title: '已删除', icon: 'success' })
      setTimeout(() => Taro.navigateBack(), 600)
    } catch (e) { void Taro.showToast({ title: e instanceof ApiError ? e.message : '删除失败', icon: 'none' }) }
  }

  const lat = num(form.latitude); const lon = num(form.longitude)
  const hasPoint = lat !== null && lon !== null && Math.abs(lat) <= 90 && Math.abs(lon) <= 180
  const capMw = num(form.capacity)
  const capHint = capMw && capMw > 0 ? `= ${thousands(mwToKw(capMw))} kW` : ''
  const originTag = form.origin === 'locate' ? '来自定位' : form.origin === 'choose' ? '来自地图选点' : null

  const modelSummary = form.type === 'solar'
    ? [form.mounting === 'single_axis' ? '单轴跟踪' : '固定支架', form.bifacial ? '双面' : '单面', ...(form.mounting === 'fixed' ? [`倾角 ${form.tilt ? `${form.tilt}°` : '随纬度'}`, `方位 ${form.azimuth || '180'}°`] : [])].join(' · ')
    : `${TURBINES.find(t => t.value === form.turbine)?.label} · 轮毂 ${form.hub_height || '100'} m`
  const limitsSummary = form.mode === 'none' ? '未设置' : form.mode === 'ratio' ? `限电 ${form.ratio || '待填写'}${form.ratio ? '%' : ''}` : `分时段上限 · ${form.windows.length} 条`
  const errorIn = (section: FormSection) => formError?.section === section && <Text className="sform__error">{formError.message}</Text>
  return <View className="sform">
    <PageHeader title={editing ? '编辑电站' : '添加电站'} />
    <View className="sform__body">
      {failed && <View className="sform__card"><Text className="sform__hint">{failed}</Text></View>}
      {loading && !failed && <Skeleton height={200} lines={4} />}
      {!loading && !failed && <>
        <View className="sform__card" id="sform-basic">
          {errorIn('basic')}
          <View className="sform__row-head"><Text className="sform__card-title">基本信息</Text><InfoTip {...FORM_INFO} /></View>
          <View className="sform__field"><Text className="sform__label">电站名称</Text>
            <View className="sform__input-wrap"><Input className="sform__input sform__input--bare" value={form.name} maxlength={64} placeholder="例如：某某光伏电站" placeholderClass="sform__ph" onInput={set('name')} /></View></View>
          <View className="sform__field"><Text className="sform__label">类型</Text>
            <SegmentedTabs value={form.type} options={[{ value: 'solar', label: '光伏' }, { value: 'wind', label: '风电' }]} onChange={(v) => patch({ type: v as StationType })} /></View>
          <View className="sform__field"><Text className="sform__label">装机容量（MW，交流侧）</Text>
            <View className="sform__input-wrap"><Input className="sform__input sform__input--bare" type="digit" value={form.capacity} placeholder="例如 50" placeholderClass="sform__ph" onInput={set('capacity')} />{capHint && <Text className="sform__adorn">{capHint}</Text>}</View></View>
        </View>

        <View className="sform__card" id="sform-location">
          {errorIn('location')}
          <View className="sform__row-head"><Text className="sform__card-title">位置</Text><InfoTip {...LOCATION_INFO} /></View>
          <View className="sform__actions">
            <View className="sform__locate" hoverClass="pressed" onClick={locating ? undefined : locate}><Icon name="crosshair" size={14} color="#1264d6" /><Text className="sform__locate-text">{locating ? '定位中…' : '当前定位'}</Text></View>
            <View className="sform__locate" hoverClass="pressed" onClick={choose}><Icon name="mapPin" size={14} color="#1264d6" /><Text className="sform__locate-text">地图选点</Text></View>
            {originTag && <Text className="sform__origin">{originTag}</Text>}
          </View>
          <View className="sform__coords">
            <View className="sform__field sform__field--half"><Text className="sform__label">纬度</Text>
              <View className="sform__input-wrap"><Input className="sform__input sform__input--bare" type="digit" value={form.latitude} placeholder="31.30000" placeholderClass="sform__ph" onInput={setCoordField('latitude')} /></View></View>
            <View className="sform__field sform__field--half"><Text className="sform__label">经度</Text>
              <View className="sform__input-wrap"><Input className="sform__input sform__input--bare" type="digit" value={form.longitude} placeholder="120.62000" placeholderClass="sform__ph" onInput={setCoordField('longitude')} /></View></View>
          </View>
          <View className="sform__field"><Text className="sform__label">读数坐标系</Text>
            <SegmentedTabs value={form.coord} options={[{ value: 'wgs84', label: 'WGS84（GPS）' }, { value: 'gcj02', label: 'GCJ-02（高德等）' }]} onChange={(v) => patch({ coord: v as Coord, origin: 'manual' })} /></View>
          {hasPoint && (IS_WEAPP
            ? <View className="sform__map-wrap"><Map className="sform__map" latitude={lat!} longitude={lon!} scale={11} enableZoom={false} enableScroll={false} showLocation={false} onError={() => undefined}
              circles={[{ latitude: lat!, longitude: lon!, radius: 400, color: '#1677ffcc', fillColor: '#1677ff33', strokeWidth: 2 }]} />
              {form.coord === 'wgs84' && <Text className="sform__map-note">预览按 GCJ-02 显示，WGS84 读数会偏移几百米</Text>}</View>
            : <View className="sform__map-placeholder"><Text>地图预览仅在小程序中显示</Text></View>)}
        </View>

        <Disclosure id="sform-model" title="出力模型参数" summary={modelSummary} info={MODEL_INFO} open={modelOpen} onToggle={() => setModelOpen(v => !v)}>
          <View className="sform__fields">{errorIn('model')}
          {form.type === 'solar' ? <>
            <View className="sform__field"><Text className="sform__label">安装方式</Text>
              <SegmentedTabs value={form.mounting} options={[{ value: 'fixed', label: '固定支架' }, { value: 'single_axis', label: '单轴跟踪' }]} onChange={(v) => patch({ mounting: v as Mounting })} /></View>
            {form.mounting === 'fixed' && <View className="sform__coords">
              <View className="sform__field sform__field--half"><Text className="sform__label">组件倾角（°）</Text>
                <View className="sform__input-wrap"><Input className="sform__input sform__input--bare" type="digit" value={form.tilt} placeholder="默认 = 纬度" placeholderClass="sform__ph" onInput={set('tilt')} /></View></View>
              <View className="sform__field sform__field--half"><Text className="sform__label">方位角（°）</Text>
                <View className="sform__input-wrap"><Input className="sform__input sform__input--bare" type="digit" value={form.azimuth} placeholder="默认 180" placeholderClass="sform__ph" onInput={set('azimuth')} /></View></View>
            </View>}
            <View className="sform__switch"><Text className="sform__label">双面组件</Text><Switch checked={form.bifacial} color="#1677ff" onChange={(e) => patch({ bifacial: !!e.detail.value })} /></View>
          </> : <>
            <View className="sform__field"><Text className="sform__label">机型</Text>
              <Picker mode="selector" range={TURBINES.map((t) => t.label)} value={TURBINES.findIndex((t) => t.value === form.turbine)} onChange={(e) => patch({ turbine: TURBINES[Number(e.detail.value)]!.value })}>
                <View className="sform__select"><Text className="sform__select-value">{TURBINES.find((t) => t.value === form.turbine)!.label}</Text><Icon name="chevronDown" size={14} color="#64748b" /></View></Picker></View>
            {form.turbine === 'custom' && <View className="sform__field"><Text className="sform__label">功率曲线</Text>
              <Textarea className="sform__textarea" value={form.curveText} placeholder={CURVE_PLACEHOLDER} placeholderClass="sform__ph" maxlength={2000} autoHeight onInput={(e) => patch({ curveText: e.detail.value })} /></View>}
            <View className="sform__field"><Text className="sform__label">轮毂高度（m）</Text>
              <View className="sform__input-wrap"><Input className="sform__input sform__input--bare" type="digit" value={form.hub_height} placeholder="默认 100" placeholderClass="sform__ph" onInput={set('hub_height')} /></View></View>
          </>}
          </View>
        </Disclosure>

        <Disclosure id="sform-limits" title="限电与检修" summary={limitsSummary} info={LIMIT_INFO} open={limitsOpen} onToggle={() => setLimitsOpen(v => !v)}>
          <View className="sform__fields">{errorIn('limits')}
          <SegmentedTabs value={form.mode} options={[{ value: 'none', label: '不设置' }, { value: 'ratio', label: '固定比例' }, { value: 'schedule', label: '分时段上限' }]}
            onChange={(v) => patch({ mode: v as Form['mode'], windows: v === 'schedule' && !form.windows.length ? [{ start: 11, end: 14, limit: '60', days: 'all' }] : form.windows })} />
          {form.mode === 'ratio' && <View className="sform__field"><Text className="sform__label">限电比例（%）</Text>
            <View className="sform__input-wrap"><Input className="sform__input sform__input--bare" type="digit" value={form.ratio} placeholder="例如 15" placeholderClass="sform__ph" onInput={set('ratio')} /></View></View>}
          {form.mode === 'schedule' && <>
            {form.windows.map((w, i) => <View key={i} className="sform__window">
              <View className="sform__window-head"><Text className="sform__label">时段 {i + 1}{w.limit === '0' ? ' · 停机' : ''}</Text>
                <View className="sform__remove" onClick={() => patch({ windows: form.windows.filter((_, j) => j !== i) })}><Icon name="trash" size={14} color="#98521a" /><Text>移除</Text></View></View>
              <View className="sform__coords">
                <Picker mode="selector" range={HOURS.slice(0, 24)} value={w.start} onChange={(e) => setWindow(i, { start: Number(e.detail.value) })}>
                  <View className="sform__select sform__select--half"><Text className="sform__select-value">{HOURS[w.start]}</Text><Icon name="chevronDown" size={14} color="#64748b" /></View></Picker>
                <Text className="sform__dash">至</Text>
                <Picker mode="selector" range={HOURS} value={w.end} onChange={(e) => setWindow(i, { end: Number(e.detail.value) })}>
                  <View className="sform__select sform__select--half"><Text className="sform__select-value">{HOURS[w.end]}</Text><Icon name="chevronDown" size={14} color="#64748b" /></View></Picker>
              </View>
              <View className="sform__coords">
                <View className="sform__field sform__field--half"><Text className="sform__label">出力上限（装机 %）</Text>
                  <View className="sform__input-wrap"><Input className="sform__input sform__input--bare" type="digit" value={w.limit} placeholder="0 表示停机" placeholderClass="sform__ph" onInput={(e) => setWindow(i, { limit: e.detail.value })} /></View></View>
                <View className="sform__field sform__field--half"><Text className="sform__label">适用</Text>
                  <Picker mode="selector" range={DAY_PRESETS.map((d) => d.label)} value={DAY_PRESETS.findIndex((d) => d.value === w.days)} onChange={(e) => setWindow(i, { days: DAY_PRESETS[Number(e.detail.value)]!.value, originalDays: undefined })}>
                    <View className="sform__select"><Text className="sform__select-value">{w.originalDays?.length ? `周${w.originalDays.map(d => '一二三四五六日'[d - 1]).join('、')}` : DAY_PRESETS.find((d) => d.value === w.days)!.label}</Text><Icon name="chevronDown" size={14} color="#64748b" /></View></Picker></View>
              </View>
            </View>)}
            {form.windows.length < 12 && <View className="sform__actions">
              <View className="sform__add-window" hoverClass="pressed" onClick={() => patch({ windows: [...form.windows, { start: 11, end: 14, limit: '60', days: 'all' }] })}><Icon name="plus" size={14} color="#1264d6" /><Text>添加时段</Text></View>
              <View className="sform__add-window" hoverClass="pressed" onClick={() => patch({ windows: [...form.windows, { start: 0, end: 24, limit: '0', days: 'all' }] })}><Icon name="sliders" size={14} color="#1264d6" /><Text>检修停机</Text></View>
            </View>}
          </>}
          </View>
        </Disclosure>

        {editing && <View className="sform__delete" hoverClass="pressed" onClick={remove}><Icon name="trash" size={14} color="#b91c1c" /><Text>删除电站</Text></View>}
      </>}
    </View>
    {/* 提交条不按键盘状态收起。曾经用 onKeyboardHeightChange 收它，结果真机上
        「填完装机容量后按钮再也不出现」：blur 放开之后，键盘关闭动画期间迟到的
        height>0 事件又把它收回去，此后再无事件送到。它是唯一的提交入口，被这种
        竞态卡住就等于表单交不出去；而这个门槛只在真机生效，模拟器里永远测不到。
        被键盘挡住是观感问题，按钮消失是功能问题 —— 不拿后者换前者。
        输入框的 adjust-position 会把焦点滚到键盘上方，固定底栏不会盖住它。 */}
    {!loading && !failed && <View className="sform__footer">
      {formError?.section === 'submit' && <Text className="sform__error">{formError.message}</Text>}
      <Button className="sform__submit" disabled={saving} onClick={submit}><Text className="sform__submit-text">{saving ? '保存中…' : editing ? '保存修改' : '添加电站'}</Text></Button>
    </View>}
  </View>
}
