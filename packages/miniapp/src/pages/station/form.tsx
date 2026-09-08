import { View, Text, Input } from '@tarojs/components'
import Taro, { useRouter } from '@tarojs/taro'
import { useEffect, useState } from 'react'
import type { CreateStationRequest, StationType } from '@enersight/core/types'
import { ApiError } from '@enersight/core/api'
import { homeApi } from '@/api/home'
import { stationsApi } from '@/api/stations'
import { Icon, PageHeader, SegmentedTabs } from '@/components'
import { useStationStore } from '@/store'
import './form.scss'

/**
 * 站点表单：新增 / 编辑共用。docs/01 §六
 *
 * 入口参数（都可选）：
 *   id         编辑已有站点，从详情接口回填
 *   lat, lng   预填坐标（GPS 或地图选点，客户端坐标系）
 *   name/type/capacity/catalog  从公开电站目录预填
 * 必填：名称、类型、经纬度、装机容量；倾角 / 方位角 / 轮毂高度折叠在高级设置。
 */

interface Form {
  name: string
  type: StationType
  latitude: string
  longitude: string
  capacity: string
  tilt: string
  azimuth: string
  hub_height: string
  catalog_id: string | null
}

const EMPTY: Form = {
  name: '', type: 'solar', latitude: '', longitude: '', capacity: '',
  tilt: '', azimuth: '', hub_height: '', catalog_id: null,
}

function num(s: string): number | null {
  const v = Number(s)
  return s.trim() === '' || Number.isNaN(v) ? null : v
}

function validate(f: Form): string | null {
  if (!f.name.trim()) return '请输入站点名称'
  const lat = num(f.latitude); const lng = num(f.longitude)
  if (lat === null || lat < -90 || lat > 90) return '纬度需在 -90 ~ 90 之间'
  if (lng === null || lng < -180 || lng > 180) return '经度需在 -180 ~ 180 之间'
  const cap = num(f.capacity)
  if (cap === null || cap <= 0) return '装机容量需大于 0'
  const tilt = num(f.tilt); if (f.tilt && (tilt === null || tilt < 0 || tilt > 90)) return '倾角需在 0 ~ 90°'
  const az = num(f.azimuth); if (f.azimuth && (az === null || az < 0 || az > 360)) return '方位角需在 0 ~ 360°'
  const hub = num(f.hub_height); if (f.hub_height && (hub === null || hub <= 0 || hub > 200)) return '轮毂高度需在 0 ~ 200 m'
  return null
}

export default function StationForm() {
  const { params } = useRouter()
  const editId = params.id
  const [form, setForm] = useState<Form>({
    ...EMPTY,
    name: params.name ? decodeURIComponent(params.name) : '',
    type: (params.type as StationType) || 'solar',
    latitude: params.lat ?? '',
    longitude: params.lng ?? '',
    capacity: params.capacity ?? '',
    catalog_id: params.catalog ?? null,
  })
  const [advanced, setAdvanced] = useState(false)
  const [loading, setLoading] = useState(!!editId)
  const [saving, setSaving] = useState(false)
  const setCurrent = useStationStore((s) => s.setCurrent)

  useEffect(() => {
    if (!editId) return
    homeApi.detail(editId).then((d) => {
      const s = d.station
      setForm((f) => ({
        ...f,
        name: s.name, type: s.type,
        latitude: String(s.latitude), longitude: String(s.longitude),
        capacity: String(s.capacity),
      }))
    }).catch(() => Taro.showToast({ title: '加载站点失败', icon: 'none' }))
      .finally(() => setLoading(false))
  }, [editId])

  const set = (k: keyof Form) => (e: { detail: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.detail.value }))

  const locate = async () => {
    try {
      const r = await Taro.getLocation({ type: 'gcj02' })
      setForm((f) => ({ ...f, latitude: r.latitude.toFixed(5), longitude: r.longitude.toFixed(5) }))
    } catch {
      Taro.showToast({ title: '定位失败，请检查定位权限', icon: 'none' })
    }
  }

  const submit = async () => {
    const err = validate(form)
    if (err) { Taro.showToast({ title: err, icon: 'none' }); return }
    setSaving(true)
    try {
      const body = {
        name: form.name.trim(),
        type: form.type,
        latitude: num(form.latitude)!,
        longitude: num(form.longitude)!,
        capacity: num(form.capacity)!,
        tilt: form.type === 'solar' ? num(form.tilt) : null,
        azimuth: form.type === 'solar' ? num(form.azimuth) : null,
        hub_height: form.type === 'wind' ? num(form.hub_height) : null,
      }
      if (editId) {
        // 编辑时经纬度同样按客户端坐标系提交，服务端转 WGS84
        await stationsApi.update(editId, { ...body, coord: 'gcj02' })
      } else {
        // 入参坐标是 GCJ-02（GPS / 地图 / 目录都按客户端坐标系），服务端转 WGS84 存
        const created = await stationsApi.create({
          ...body, coord: 'gcj02', catalog_id: form.catalog_id,
        } as CreateStationRequest)
        setCurrent(created.id)
      }
      Taro.showToast({ title: editId ? '已保存' : '已添加', icon: 'success' })
      setTimeout(() => Taro.navigateBack(), 600)
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : '保存失败'
      Taro.showToast({ title: msg, icon: 'none' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <View className="sform">
      <PageHeader title={editId ? '编辑站点' : '新增站点'} />

      <View className="sform__body">
        {form.catalog_id && (
          <View className="sform__source">
            <Icon name="layers" size={13} color="#1677ff" />
            <Text className="sform__source-text">来自公开电站目录，信息可修改</Text>
          </View>
        )}

        <View className="sform__card">
          <View className="sform__field">
            <Text className="sform__label">站点名称</Text>
            <Input className="sform__input" value={form.name} placeholder="如 苏州光伏站"
              placeholderClass="sform__ph" maxlength={64} onInput={set('name')} disabled={loading} />
          </View>
          <View className="sform__field">
            <Text className="sform__label">站点类型</Text>
            <SegmentedTabs
              value={form.type}
              onChange={(v) => setForm((f) => ({ ...f, type: v as StationType }))}
              options={[{ value: 'solar', label: '光伏' }, { value: 'wind', label: '风电' }]}
            />
          </View>
          <View className="sform__field">
            <Text className="sform__label">装机容量（kW）</Text>
            <Input className="sform__input" type="digit" value={form.capacity} placeholder="如 500"
              placeholderClass="sform__ph" onInput={set('capacity')} />
          </View>
        </View>

        <View className="sform__card">
          <View className="sform__row-head">
            <Text className="sform__card-title">位置</Text>
            <View className="sform__locate" hoverClass="pressed" onClick={locate}>
              <Icon name="crosshair" size={13} color="#1677ff" />
              <Text className="sform__locate-text">获取当前位置</Text>
            </View>
          </View>
          <View className="sform__coords">
            <View className="sform__field sform__field--half">
              <Text className="sform__label">纬度</Text>
              <Input className="sform__input" type="digit" value={form.latitude} placeholder="31.2304"
                placeholderClass="sform__ph" onInput={set('latitude')} />
            </View>
            <View className="sform__field sform__field--half">
              <Text className="sform__label">经度</Text>
              <Input className="sform__input" type="digit" value={form.longitude} placeholder="120.5853"
                placeholderClass="sform__ph" onInput={set('longitude')} />
            </View>
          </View>
        </View>

        <View className="sform__card">
          <View className="sform__row-head" onClick={() => setAdvanced((v) => !v)}>
            <Text className="sform__card-title">高级设置</Text>
            <Text className="sform__hint">不填用默认值</Text>
            <Icon name={advanced ? 'chevronDown' : 'chevronRight'} size={14} color="#9ca3af" />
          </View>
          {advanced && form.type === 'solar' && (
            <View className="sform__coords">
              <View className="sform__field sform__field--half">
                <Text className="sform__label">组件倾角（°）</Text>
                <Input className="sform__input" type="digit" value={form.tilt} placeholder="默认 = 纬度"
                  placeholderClass="sform__ph" onInput={set('tilt')} />
              </View>
              <View className="sform__field sform__field--half">
                <Text className="sform__label">方位角（°）</Text>
                <Input className="sform__input" type="digit" value={form.azimuth} placeholder="默认 180 正南"
                  placeholderClass="sform__ph" onInput={set('azimuth')} />
              </View>
            </View>
          )}
          {advanced && form.type === 'wind' && (
            <View className="sform__field">
              <Text className="sform__label">轮毂高度（m）</Text>
              <Input className="sform__input" type="digit" value={form.hub_height} placeholder="默认按容量 70 / 85 / 100"
                placeholderClass="sform__ph" onInput={set('hub_height')} />
            </View>
          )}
        </View>

        <View className={`sform__submit ${saving ? 'sform__submit--busy' : ''}`} hoverClass="pressed" onClick={saving ? undefined : submit}>
          <Text className="sform__submit-text">{saving ? '保存中…' : editId ? '保存修改' : '添加站点'}</Text>
        </View>
      </View>
    </View>
  )
}
