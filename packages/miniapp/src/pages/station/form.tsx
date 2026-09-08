import { View, Text, Input } from '@tarojs/components'
import Taro, { useRouter } from '@tarojs/taro'
import { useEffect, useState } from 'react'
import { formatCoordinate, formatPower } from '@enersight/core/format'
import type { StationDetailResponse, StationType } from '@enersight/core/types'
import { ApiError } from '@enersight/core/api'
import { homeApi } from '@/api/home'
import { stationsApi } from '@/api/stations'
import { Icon, PageHeader, Skeleton } from '@/components'
import './form.scss'

/**
 * 站点参数编辑。站点来自公开电站目录，名称 / 类型 / 位置只读；
 * 可改的是装机容量（目录口径与实际可能不同）和出力模型参数。docs/01 §六、docs/07 §2.3
 */

interface Form {
  capacity: string
  tilt: string
  azimuth: string
  hub_height: string
}

function num(s: string): number | null {
  const v = Number(s)
  return s.trim() === '' || Number.isNaN(v) ? null : v
}

function validate(f: Form, type: StationType): string | null {
  const cap = num(f.capacity)
  if (cap === null || cap <= 0) return '装机容量需大于 0'
  if (type === 'solar') {
    const tilt = num(f.tilt); if (f.tilt && (tilt === null || tilt < 0 || tilt > 90)) return '倾角需在 0 ~ 90°'
    const az = num(f.azimuth); if (f.azimuth && (az === null || az < 0 || az > 360)) return '方位角需在 0 ~ 360°'
  } else {
    const hub = num(f.hub_height); if (f.hub_height && (hub === null || hub <= 0 || hub > 200)) return '轮毂高度需在 0 ~ 200 m'
  }
  return null
}

export default function StationForm() {
  const { params } = useRouter()
  const id = params.id ?? ''
  const [station, setStation] = useState<StationDetailResponse['station'] | null>(null)
  const [form, setForm] = useState<Form>({ capacity: '', tilt: '', azimuth: '', hub_height: '' })
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!id) { setFailed(true); return }
    homeApi.detail(id).then((d) => {
      setStation(d.station)
      setForm((f) => ({ ...f, capacity: String(d.station.capacity) }))
    }).catch(() => setFailed(true))
  }, [id])

  const set = (k: keyof Form) => (e: { detail: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.detail.value }))

  const submit = async () => {
    if (!station) return
    const err = validate(form, station.type)
    if (err) { Taro.showToast({ title: err, icon: 'none' }); return }
    setSaving(true)
    try {
      await stationsApi.update(id, {
        capacity: num(form.capacity)!,
        tilt: station.type === 'solar' ? num(form.tilt) : null,
        azimuth: station.type === 'solar' ? num(form.azimuth) : null,
        hub_height: station.type === 'wind' ? num(form.hub_height) : null,
        coord: 'gcj02',
      })
      Taro.showToast({ title: '已保存', icon: 'success' })
      setTimeout(() => Taro.navigateBack(), 600)
    } catch (e) {
      Taro.showToast({ title: e instanceof ApiError ? e.message : '保存失败', icon: 'none' })
    } finally {
      setSaving(false)
    }
  }

  const cap = station ? formatPower(station.capacity) : null

  return (
    <View className="sform">
      <PageHeader title="站点参数" subtitle={station?.name} />

      <View className="sform__body">
        {failed && (
          <View className="sform__card"><Text className="sform__hint">站点加载失败</Text></View>
        )}
        {!station && !failed && <Skeleton height={160} lines={3} />}

        {station && (
          <>
            <View className="sform__card">
              <View className="sform__row-head">
                <Text className="sform__card-title">基本信息</Text>
                <Text className="sform__hint">来自公开电站目录，随目录同步</Text>
              </View>
              <View className="sform__ro">
                <Text className="sform__ro-label">名称</Text>
                <Text className="sform__ro-value">{station.name}</Text>
              </View>
              <View className="sform__ro">
                <Text className="sform__ro-label">类型</Text>
                <Text className="sform__ro-value">{station.type === 'solar' ? '光伏' : '风电'}</Text>
              </View>
              <View className="sform__ro">
                <Text className="sform__ro-label">位置</Text>
                <Text className="sform__ro-value">
                  {station.address ? `${station.address} · ` : ''}
                  {formatCoordinate(station.latitude, station.longitude)}
                </Text>
              </View>
              <View className="sform__ro">
                <Text className="sform__ro-label">目录容量</Text>
                <Text className="sform__ro-value">{cap!.value} {cap!.unit}</Text>
              </View>
            </View>

            <View className="sform__card">
              <View className="sform__row-head">
                <Text className="sform__card-title">出力模型参数</Text>
                <Text className="sform__hint">不填用默认值</Text>
              </View>
              <View className="sform__field">
                <Text className="sform__label">装机容量（kW）</Text>
                <Input className="sform__input" type="digit" value={form.capacity}
                  placeholderClass="sform__ph" onInput={set('capacity')} />
              </View>
              {station.type === 'solar' ? (
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
              ) : (
                <View className="sform__field">
                  <Text className="sform__label">轮毂高度（m）</Text>
                  <Input className="sform__input" type="digit" value={form.hub_height} placeholder="默认按容量 70 / 85 / 100"
                    placeholderClass="sform__ph" onInput={set('hub_height')} />
                </View>
              )}
              <View className="sform__note">
                <Icon name="helpCircle" size={12} color="#9ca3af" />
                <Text className="sform__note-text">参数影响发电估算与环境指数，见站点详情的说明</Text>
              </View>
            </View>

            <View className={`sform__submit ${saving ? 'sform__submit--busy' : ''}`} hoverClass="pressed" onClick={saving ? undefined : submit}>
              <Text className="sform__submit-text">{saving ? '保存中…' : '保存'}</Text>
            </View>
          </>
        )}
      </View>
    </View>
  )
}
