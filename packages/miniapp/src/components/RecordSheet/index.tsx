import { Button, Input, Picker, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useEffect, useState } from 'react'
import { thousands } from '@enersight/core/format'
import type { MeasuredSummary } from '@enersight/core/types'
import { ApiError } from '@enersight/core/api'
import { measuredApi } from '@/api/measured'
import { Icon } from '../Icon'
import { SegmentedTabs } from '../SegmentedTabs'
import './index.scss'

/** 回算最多 92 天：更早的没有模型同期值可比，服务端会拒，这里干脆不让选。docs/19 §三 */
const MAX_BACK_DAYS = 92

/** 北京时间的「今天往前 n 天」，YYYY-MM-DD。 */
function beijingDay(back: number) {
  return new Date(Date.now() + 8 * 3600_000 - back * 86400_000).toISOString().slice(0, 10)
}
function lastMonth() {
  const [y, m] = beijingDay(0).split('-').map(Number) as [number, number]
  return m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, '0')}`
}
function mwh(kwh: number) {
  return `${thousands(kwh / 1000, kwh < 10_000 ? 2 : 1)} MWh`
}

/**
 * 记一笔实测电量。docs/19 §三
 *
 * 贴顶弹出而不是贴底：软键盘从底部升起会盖住贴底的输入框和提交按钮，真机上表单就交不出去
 * （CLAUDE.md「已知的环境坑」）。提交按钮放在弹层内容流里，不押在键盘事件上。
 */
export function RecordSheet({ stationId, capacityKw, visible, onClose, onDone }: {
  stationId: string
  capacityKw: number
  visible: boolean
  onClose: () => void
  onDone?: (summary: MeasuredSummary) => void
}) {
  const [kind, setKind] = useState<'day' | 'month'>('day')
  const [day, setDay] = useState(() => beijingDay(1))
  const [month, setMonth] = useState(lastMonth)
  const [value, setValue] = useState('')
  const [basis, setBasis] = useState<'generation' | 'grid'>('generation')
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (visible) { setValue(''); setDay(beijingDay(1)); setMonth(lastMonth()) } }, [visible])
  if (!visible) return null

  const n = Number(value)
  const kwh = value.trim() && Number.isFinite(n) && n >= 0 ? n * 1000 : null
  const date = kind === 'day' ? day : month
  const submit = async () => {
    if (kwh == null) { void Taro.showToast({ title: '请填写电量', icon: 'none' }); return }
    setBusy(true)
    try {
      const s = await measuredApi.record(stationId, { entries: [{ kind, date, kwh }], basis })
      const entry = s.entries.find(e => e.date === date)
      void Taro.showToast({
        title: entry?.model_kwh != null ? `已记录 · 模型同期 ${mwh(entry.model_kwh)}` : '已记录，模型同期电量回算中',
        icon: 'none',
        duration: 2500,
      })
      onDone?.(s)
      onClose()
    } catch (e) {
      void Taro.showToast({ title: e instanceof ApiError ? e.message : '记录失败，请稍后再试', icon: 'none', duration: 3000 })
    } finally { setBusy(false) }
  }

  // 一天满发的上限，用来提示单位填错（服务端也会挡）
  const limitHint = kind === 'day' ? `满发约 ${mwh(capacityKw * 24)}` : ''
  return (
    <View className="record" catchMove onClick={onClose}>
      <View className="record__panel" onClick={e => e.stopPropagation()}>
        <View className="record__head">
          <Text className="record__title">记一笔实测电量</Text>
          <View className="record__close" hoverClass="pressed" onClick={onClose} ariaLabel="关闭"><Icon name="x" size={18} color="#64748b" /></View>
        </View>
        <SegmentedTabs value={kind} options={[{ value: 'day', label: '日电量' }, { value: 'month', label: '月电量' }]} onChange={v => setKind(v as 'day' | 'month')} />
        <View className="record__field">
          <Text className="record__label">{kind === 'day' ? '日期' : '月份'}</Text>
          {kind === 'day'
            ? <Picker mode="date" value={day} start={beijingDay(MAX_BACK_DAYS)} end={beijingDay(1)} onChange={e => setDay(e.detail.value)}>
                <View className="record__select"><Icon name="calendar" size={15} color="#64748b" /><Text>{day}</Text></View>
              </Picker>
            : <Picker mode="date" fields="month" value={month} start={beijingDay(MAX_BACK_DAYS).slice(0, 7)} end={lastMonth()} onChange={e => setMonth(e.detail.value)}>
                <View className="record__select"><Icon name="calendar" size={15} color="#64748b" /><Text>{month}</Text></View>
              </Picker>}
        </View>
        <View className="record__field">
          <Text className="record__label">电量（MWh）</Text>
          <View className="record__input-wrap">
            <Input className="record__input" type="digit" value={value} placeholder={kind === 'day' ? '例如 12.5' : '例如 380'} placeholderClass="record__ph" onInput={e => setValue(e.detail.value)} />
            <Text className="record__adorn">{kwh != null ? `= ${thousands(kwh)} kWh` : limitHint}</Text>
          </View>
        </View>
        <View className="record__field">
          <Text className="record__label">口径</Text>
          <SegmentedTabs value={basis} options={[{ value: 'generation', label: '发电量' }, { value: 'grid', label: '上网电量' }]} onChange={v => setBasis(v as 'generation' | 'grid')} />
        </View>
        <Text className="record__hint">
          {`逆变器或电表读数记「发电量」；结算单上的数记「上网电量」，线损与厂用电会一起算进订正系数。只能记最近 ${MAX_BACK_DAYS} 天内已经过完的日子或月份。`}
        </Text>
        <Button className={`record__submit ${busy ? 'record__submit--busy' : ''}`} hoverClass="pressed" loading={busy} onClick={() => { if (!busy) void submit() }}>记录</Button>
      </View>
    </View>
  )
}
