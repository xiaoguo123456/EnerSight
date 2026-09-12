import { View, Text, Input, Picker, Map } from '@tarojs/components'
import Taro, { useRouter } from '@tarojs/taro'
import { useEffect, useState } from 'react'
import type { CurtailmentRule, StationSummary, StationType } from '@enersight/core/types'
import { ApiError } from '@enersight/core/api'
import { formatPower } from '@enersight/core/format'
import { homeApi } from '@/api/home'
import { stationsApi } from '@/api/stations'
import { Icon, InfoTip, PageHeader, SegmentedTabs, Skeleton } from '@/components'
import { useStationStore } from '@/store'
import { decodeRouteParam } from '@/route'
import './form.scss'

/**
 * 自建电站：新建与编辑共用。docs/17 §一、§四
 * 经纬度三种来源：当前定位、地图选点（都是 GCJ-02）、手动输入（默认 WGS84，可切 GCJ-02）。
 * 提交带 coord，坐标转换由服务端完成，客户端不做换算；小程序里用 <Map> 预览点位。
 */

type Coord = 'wgs84' | 'gcj02'
type Origin = 'manual' | 'locate' | 'choose'
type DaysPreset = 'all' | 'weekday' | 'weekend'
interface WindowForm { start: number; end: number; limit: string; days: DaysPreset }
interface Form {
  name: string; type: StationType; capacity: string
  latitude: string; longitude: string; coord: Coord; origin: Origin
  tilt: string; azimuth: string; hub_height: string
  mode: 'none' | 'ratio' | 'schedule'; ratio: string; windows: WindowForm[]
}

const EMPTY: Form = { name: '', type: 'solar', capacity: '', latitude: '', longitude: '', coord: 'wgs84', origin: 'manual', tilt: '', azimuth: '', hub_height: '', mode: 'none', ratio: '', windows: [] }
const HOURS = Array.from({ length: 25 }, (_, i) => `${String(i).padStart(2, '0')}:00`)
const DAY_PRESETS: { value: DaysPreset; label: string; weekdays: number[] }[] = [
  { value: 'all', label: '每天', weekdays: [] }, { value: 'weekday', label: '工作日', weekdays: [1, 2, 3, 4, 5] }, { value: 'weekend', label: '周末', weekdays: [6, 7] },
]
const IS_WEAPP = process.env.TARO_ENV === 'weapp'
const LOCATION_INFO = { title: '位置与坐标系', content: '当前定位与地图选点自动按 GCJ-02 提交。手动输入请按读数来源选择坐标系：GPS 设备、谷歌地图为 WGS84；高德、腾讯、百度地图为 GCJ-02。选错不会报错，只会让电站与云图错位几百米，保存前请在地图预览里核对点位。' }
const MODEL_INFO = { title: '出力模型参数', content: '不填用默认值：光伏倾角取纬度、方位角 180° 正南；风机轮毂高度 100 m。参数影响发电估算与发电适宜度，可在电站详情看到估算口径。' }
const LIMIT_INFO = { title: '出力约束', content: '限电或检修只影响「预计上网电量」，可发电量与发电适宜度仍按气象条件估算。固定比例：全天出力按比例折减。分时段上限：时段内出力封顶为装机容量的百分比，0 表示停机；结束时刻早于开始表示跨零点；多条时段重叠取最低。规则由你自行填写，不来自电网调度。' }
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
    ...form, name: s.name, type: s.type, capacity: String(s.capacity),
    latitude: s.latitude.toFixed(5), longitude: s.longitude.toFixed(5), coord: 'gcj02', origin: 'manual',
    mode: rule ? rule.mode : 'none',
    ratio: rule?.mode === 'ratio' && rule.ratio_percent != null ? String(rule.ratio_percent) : '',
    windows: rule?.mode === 'schedule' ? (rule.windows ?? []).map((w) => ({ start: w.start_hour, end: w.end_hour, limit: String(w.limit_percent), days: presetOf(w.weekdays ?? []) })) : [],
  }
}

function validate(f: Form): string | null {
  if (!f.name.trim()) return '请填写电站名称'
  if (f.name.trim().length > 64) return '名称不超过 64 个字'
  const cap = num(f.capacity); if (cap === null || cap <= 0) return '装机容量需大于 0'
  const lat = num(f.latitude); const lon = num(f.longitude)
  if (lat === null || lon === null) return '请填写经纬度，或使用定位 / 地图选点'
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return '经纬度超出范围'
  if (f.type === 'solar') {
    const tilt = num(f.tilt); if (f.tilt && (tilt === null || tilt < 0 || tilt > 90)) return '倾角需在 0 ~ 90°'
    const az = num(f.azimuth); if (f.azimuth && (az === null || az < 0 || az > 360)) return '方位角需在 0 ~ 360°'
  } else {
    const hub = num(f.hub_height); if (f.hub_height && (hub === null || hub <= 0 || hub > 200)) return '轮毂高度需在 0 ~ 200 m'
  }
  if (f.mode === 'ratio') { const r = num(f.ratio); if (r === null || r <= 0 || r > 100) return '限电比例需在 0 ~ 100%' }
  if (f.mode === 'schedule') {
    if (!f.windows.length) return '至少添加一条限电时段'
    for (const w of f.windows) {
      const l = num(w.limit); if (l === null || l < 0 || l > 100) return '出力上限需在 0 ~ 100%'
      if (w.start === w.end) return '时段起止不能相同'
    }
  }
  return null
}

function toRule(f: Form): CurtailmentRule | null {
  if (f.mode === 'ratio') return { mode: 'ratio', ratio_percent: num(f.ratio)!, windows: [] }
  if (f.mode === 'schedule') return { mode: 'schedule', ratio_percent: null, windows: f.windows.map((w) => ({ start_hour: w.start, end_hour: w.end, limit_percent: num(w.limit)!, weekdays: DAY_PRESETS.find((d) => d.value === w.days)!.weekdays })) }
  return null
}

export default function StationForm() {
  const { params } = useRouter()
  const id = decodeRouteParam(params.id)
  const editing = !!id
  const [form, setForm] = useState<Form>(EMPTY)
  const [loading, setLoading] = useState(editing)
  const [failed, setFailed] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [locating, setLocating] = useState(false)
  const { currentId, setCurrent, remember } = useStationStore()

  useEffect(() => {
    if (!editing) return
    homeApi.detail(id).then((d) => {
      if (!d.station.is_own) { setFailed('公开电站由平台维护，不能编辑'); return }
      setForm((f) => fromStation(d.station, f))
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
    if (err) { void Taro.showToast({ title: err, icon: 'none' }); return }
    setSaving(true)
    const body = {
      name: form.name.trim(), type: form.type, capacity: num(form.capacity)!,
      latitude: num(form.latitude)!, longitude: num(form.longitude)!, coord: form.coord,
      tilt: form.type === 'solar' ? num(form.tilt) : null, azimuth: form.type === 'solar' ? num(form.azimuth) : null,
      hub_height: form.type === 'wind' ? num(form.hub_height) : null,
      curtailment: toRule(form),
    }
    try {
      if (editing) {
        const s = await stationsApi.update(id, body)
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
      void Taro.showToast({ title: e instanceof ApiError ? e.message : '保存失败', icon: 'none' })
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
  const capKw = num(form.capacity)
  const capHint = capKw && capKw > 0 ? `= ${formatPower(capKw).value} ${formatPower(capKw).unit}` : ''
  const originTag = form.origin === 'locate' ? '来自定位' : form.origin === 'choose' ? '来自地图选点' : null

  return <View className="sform">
    <PageHeader title={editing ? '编辑电站' : '添加电站'} subtitle={editing ? form.name : undefined} />
    <View className="sform__body">
      {failed && <View className="sform__card"><Text className="sform__hint">{failed}</Text></View>}
      {loading && !failed && <Skeleton height={200} lines={4} />}
      {!loading && !failed && <>
        <View className="sform__card">
          <View className="sform__row-head"><Text className="sform__card-title">基本信息</Text><InfoTip {...FORM_INFO} /></View>
          <View className="sform__field"><Text className="sform__label">电站名称</Text>
            <Input className="sform__input" value={form.name} maxlength={64} placeholder="例如：某某光伏电站" placeholderClass="sform__ph" onInput={set('name')} /></View>
          <View className="sform__field"><Text className="sform__label">类型</Text>
            <SegmentedTabs value={form.type} options={[{ value: 'solar', label: '光伏' }, { value: 'wind', label: '风电' }]} onChange={(v) => patch({ type: v as StationType })} /></View>
          <View className="sform__field"><Text className="sform__label">装机容量（kW，交流侧）</Text>
            <View className="sform__input-wrap"><Input className="sform__input sform__input--bare" type="digit" value={form.capacity} placeholder="例如 5000" placeholderClass="sform__ph" onInput={set('capacity')} />{capHint && <Text className="sform__adorn">{capHint}</Text>}</View></View>
        </View>

        <View className="sform__card">
          <View className="sform__row-head"><Text className="sform__card-title">位置</Text><InfoTip {...LOCATION_INFO} /></View>
          <View className="sform__actions">
            <View className="sform__locate" hoverClass="pressed" onClick={locating ? undefined : locate}><Icon name="crosshair" size={14} color="#1264d6" /><Text className="sform__locate-text">{locating ? '定位中…' : '当前定位'}</Text></View>
            <View className="sform__locate" hoverClass="pressed" onClick={choose}><Icon name="mapPin" size={14} color="#1264d6" /><Text className="sform__locate-text">地图选点</Text></View>
            {originTag && <Text className="sform__origin">{originTag}</Text>}
          </View>
          <View className="sform__coords">
            <View className="sform__field sform__field--half"><Text className="sform__label">纬度</Text>
              <Input className="sform__input" type="digit" value={form.latitude} placeholder="31.30000" placeholderClass="sform__ph" onInput={setCoordField('latitude')} /></View>
            <View className="sform__field sform__field--half"><Text className="sform__label">经度</Text>
              <Input className="sform__input" type="digit" value={form.longitude} placeholder="120.62000" placeholderClass="sform__ph" onInput={setCoordField('longitude')} /></View>
          </View>
          <View className="sform__field"><Text className="sform__label">读数坐标系</Text>
            <SegmentedTabs value={form.coord} options={[{ value: 'wgs84', label: 'WGS84（GPS）' }, { value: 'gcj02', label: 'GCJ-02（高德等）' }]} onChange={(v) => patch({ coord: v as Coord, origin: 'manual' })} /></View>
          {hasPoint && (IS_WEAPP
            ? <View className="sform__map-wrap"><Map className="sform__map" latitude={lat!} longitude={lon!} scale={11} enableZoom={false} enableScroll={false} showLocation={false} onError={() => undefined}
              circles={[{ latitude: lat!, longitude: lon!, radius: 400, color: '#1677ffcc', fillColor: '#1677ff33', strokeWidth: 2 }]} />
              {form.coord === 'wgs84' && <Text className="sform__map-note">预览按 GCJ-02 显示，WGS84 读数会偏移几百米</Text>}</View>
            : <View className="sform__map-placeholder"><Text>地图预览仅在小程序中显示</Text></View>)}
        </View>

        <View className="sform__card">
          <View className="sform__row-head"><Text className="sform__card-title">出力模型参数</Text><InfoTip {...MODEL_INFO} /></View>
          {form.type === 'solar' ? <View className="sform__coords">
            <View className="sform__field sform__field--half"><Text className="sform__label">组件倾角（°）</Text>
              <Input className="sform__input" type="digit" value={form.tilt} placeholder="默认 = 纬度" placeholderClass="sform__ph" onInput={set('tilt')} /></View>
            <View className="sform__field sform__field--half"><Text className="sform__label">方位角（°）</Text>
              <Input className="sform__input" type="digit" value={form.azimuth} placeholder="默认 180" placeholderClass="sform__ph" onInput={set('azimuth')} /></View>
          </View> : <View className="sform__field"><Text className="sform__label">轮毂高度（m）</Text>
            <Input className="sform__input" type="digit" value={form.hub_height} placeholder="默认 100" placeholderClass="sform__ph" onInput={set('hub_height')} /></View>}
        </View>

        <View className="sform__card">
          <View className="sform__row-head"><Text className="sform__card-title">出力约束（限电 / 检修）</Text><InfoTip {...LIMIT_INFO} /></View>
          <SegmentedTabs value={form.mode} options={[{ value: 'none', label: '不设置' }, { value: 'ratio', label: '固定比例' }, { value: 'schedule', label: '分时段上限' }]}
            onChange={(v) => patch({ mode: v as Form['mode'], windows: v === 'schedule' && !form.windows.length ? [{ start: 11, end: 14, limit: '60', days: 'all' }] : form.windows })} />
          {form.mode === 'ratio' && <View className="sform__field"><Text className="sform__label">限电比例（%）</Text>
            <Input className="sform__input" type="digit" value={form.ratio} placeholder="例如 15" placeholderClass="sform__ph" onInput={set('ratio')} /></View>}
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
                  <Input className="sform__input" type="digit" value={w.limit} placeholder="0 表示停机" placeholderClass="sform__ph" onInput={(e) => setWindow(i, { limit: e.detail.value })} /></View>
                <View className="sform__field sform__field--half"><Text className="sform__label">适用</Text>
                  <Picker mode="selector" range={DAY_PRESETS.map((d) => d.label)} value={DAY_PRESETS.findIndex((d) => d.value === w.days)} onChange={(e) => setWindow(i, { days: DAY_PRESETS[Number(e.detail.value)]!.value })}>
                    <View className="sform__select"><Text className="sform__select-value">{DAY_PRESETS.find((d) => d.value === w.days)!.label}</Text><Icon name="chevronDown" size={14} color="#64748b" /></View></Picker></View>
              </View>
            </View>)}
            {form.windows.length < 12 && <View className="sform__actions">
              <View className="sform__add-window" hoverClass="pressed" onClick={() => patch({ windows: [...form.windows, { start: 11, end: 14, limit: '60', days: 'all' }] })}><Icon name="plus" size={14} color="#1264d6" /><Text>添加时段</Text></View>
              <View className="sform__add-window" hoverClass="pressed" onClick={() => patch({ windows: [...form.windows, { start: 0, end: 24, limit: '0', days: 'all' }] })}><Icon name="sliders" size={14} color="#1264d6" /><Text>检修停机</Text></View>
            </View>}
          </>}
        </View>

        <View className={`sform__submit ${saving ? 'sform__submit--busy' : ''}`} hoverClass="pressed" onClick={saving ? undefined : submit}>
          <Text className="sform__submit-text">{saving ? '保存中…' : editing ? '保存修改' : '添加电站'}</Text>
        </View>
        {editing && <View className="sform__delete" hoverClass="pressed" onClick={remove}><Icon name="trash" size={14} color="#b91c1c" /><Text>删除电站</Text></View>}
      </>}
    </View>
  </View>
}
