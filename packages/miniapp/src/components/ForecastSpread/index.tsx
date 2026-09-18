import { View, Text } from '@tarojs/components'
import { useState } from 'react'
import { thousands } from '@enersight/core/format'
import type { DailyOutlook, EnsembleSummary, Issuance } from '@enersight/core/types'
import { useRequest } from '@/hooks/useRequest'
import { stationsApi } from '@/api/stations'
import { energyUnit } from '../OutlookStrip'
import './index.scss'

const SPREAD_TEXT: Record<string, string> = {
  agree: '模式一致',
  diverge: '模式分歧',
  strong: '分歧很大',
}
const CONVERGENCE_TEXT: Record<string, string> = {
  stable: '已收敛',
  wobble: '仍在波动',
  swing: '来回摇摆',
}
const MODEL_TEXT: Record<string, string> = {
  ecmwf_ifs: 'ECMWF',
  icon_global: 'ICON',
  gfs_global: 'GFS',
  cma_grapes_global: 'GRAPES',
  best_match: '自动',
}
const SPREAD_HELP =
  '三家模式对同一天各算一遍，取居中那家作主数字，最低到最高就是区间。区间说的是几家意见差多少，不是误差范围，也不是概率区间。'
const EVOLUTION_HELP =
  '气象模式每 6 小时重新起报一轮。把同一天历次预报排开，最近几轮几乎不动就是收敛，可以多信几分；来回跳说明模式自己也没拿准。固定看 ECMWF，混着不同模式比的是模式差异，不是预报变化。'

const label = (model: string) => MODEL_TEXT[model] ?? model

/** 起报时刻只留到分钟；同一天的只显示时刻。 */
function issuedText(i: Issuance) {
  const stamp = i.issued_at ?? i.generated_at
  if (!stamp) return '—'
  const t = stamp.slice(11, 16)
  return i.issued_at ? `${stamp.slice(5, 10)} ${t}` : `${stamp.slice(5, 10)} ${t}（拉取）`
}

/**
 * 预测卡上的两行：三模式区间与预报演变。docs/19 §一、§二
 *
 * 两者都在描述「这个数该信几分」，不评价天气好坏，所以不套优良中差那套配色。
 * 演变只读留档，没有历史就整行不出现 —— 公开电站不定时签发，有多少显示多少。
 */
export function ForecastSpread({ day, ensemble, stationId }: {
  day: DailyOutlook
  ensemble: EnsembleSummary | null | undefined
  stationId: string
}) {
  const [open, setOpen] = useState(false)
  const history = useRequest(
    () => stationsApi.history(stationId, day.date),
    [stationId, day.date],
  )
  const members = day.member_energy_kwh ?? []
  const hasBand = day.energy_kwh_low != null && day.energy_kwh_high != null
  const unit = energyUnit(Math.max(day.energy_kwh_high ?? 0, day.energy_kwh ?? 0))
  const fmt = (v: number | null | undefined) =>
    v == null ? '—' : thousands(v / unit.divisor, v / unit.divisor < 10 ? 1 : 0)
  const issuances = history.data?.issuances ?? []
  const convergence = history.data?.convergence
  const evoUnit = energyUnit(Math.max(...issuances.map(i => i.energy_kwh ?? 0), 0))

  if (!hasBand && issuances.length < 2) return null
  return (
    <View className="spread">
      {hasBand && (
        <View className="spread__row">
          <Text className="spread__range">
            {`区间 ${fmt(day.energy_kwh_low)}–${fmt(day.energy_kwh_high)} ${unit.label}`}
          </Text>
          {day.spread_level && (
            <Text className={`spread__tag spread__tag--${day.spread_level}`}>
              {SPREAD_TEXT[day.spread_level]}
            </Text>
          )}
        </View>
      )}
      {members.length > 0 && (
        <View className="spread__members">
          {members.map(m => (
            <Text
              key={m.model}
              className={`spread__member ${m.model === day.median_model ? 'spread__member--median' : ''}`}
            >
              {`${label(m.model)} ${fmt(m.energy_kwh)}`}
            </Text>
          ))}
        </View>
      )}
      {issuances.length >= 2 && (
        <View
          className="spread__evolution"
          hoverClass="pressed"
          onClick={() => setOpen(v => !v)}
          ariaLabel={`${label(history.data?.model ?? '')} 预报演变，${issuances.length} 次起报，${convergence ? CONVERGENCE_TEXT[convergence] : ''}`}
        >
          {/* 与区间行同一格式：文字 + 灰标签，标签不会从词中间折行。
              演变固定看一个模式，最新值可能与中位成员的大数字不同，所以写明是哪家 */}
          <View className="spread__row spread__evolution-main">
            <Text className="spread__range spread__evolution-line">
              {`${label(history.data?.model ?? '')} 演变 ${issuances
                .slice(-3)
                .map(i => fmt(i.energy_kwh))
                .join(' → ')} ${unit.label}`}
            </Text>
            {convergence && (
              <Text className={`spread__tag spread__tag--${convergence}`}>
                {CONVERGENCE_TEXT[convergence]}
              </Text>
            )}
          </View>
          <Text className="spread__toggle">{open ? '收起' : '详情'}</Text>
        </View>
      )}
      {open && (
        <View className="spread__detail">
          {issuances.map(i => (
            <View key={`${i.issued_at ?? i.generated_at}`} className="spread__detail-row">
              <Text className="spread__detail-time">{issuedText(i)} 起报</Text>
              <Text className="spread__detail-value">
                {`${i.energy_kwh == null ? '—' : thousands(i.energy_kwh / evoUnit.divisor, i.energy_kwh / evoUnit.divisor < 10 ? 1 : 0)} ${evoUnit.label}`}
              </Text>
            </View>
          ))}
          <Text className="spread__help">{EVOLUTION_HELP}</Text>
          {hasBand && <Text className="spread__help">{SPREAD_HELP}</Text>}
        </View>
      )}
    </View>
  )
}
