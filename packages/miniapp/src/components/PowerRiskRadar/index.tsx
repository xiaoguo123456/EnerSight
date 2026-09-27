import { Text, View } from '@tarojs/components'
import { formatPower } from '@enersight/core/format'
import type { AlertSummary, DailyOutlook } from '@enersight/core/types'
import { Icon } from '../Icon'
import './index.scss'

function time(value: string) { return value.slice(11, 16) }

/** 只用站点已有的 15 分钟曲线和预警，给出未来四小时的功率风险摘要。 */
export function PowerRiskRadar({ day, alert, capacityKw }: { day: DailyOutlook | null | undefined; alert?: AlertSummary | null; capacityKw: number }) {
  if (!day?.power_kw?.length) return null
  const points = day.power_kw.filter(p => p.value != null).slice(0, 16)
  if (!points.length) return null
  const values = points.map(p => p.value as number)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const spread = capacityKw > 0 ? (max - min) / capacityKw * 100 : 0
  const level = alert ? '高风险' : spread >= 25 ? '需关注' : '平稳'
  const color = level === '高风险' ? '#ef4444' : level === '需关注' ? '#f59e0b' : '#16a34a'
  const peak = points.reduce((best, p) => (p.value! > best.value! ? p : best), points[0]!)
  const trough = points.reduce((best, p) => (p.value! < best.value! ? p : best), points[0]!)
  const peakPower = formatPower(peak.value)
  const troughPower = formatPower(trough.value)
  return <View className="power-risk">
    <View className="power-risk__head"><View className="power-risk__title"><Icon name="zap" size={17} color={color} /><Text>未来 4 小时功率风险</Text></View><Text className="power-risk__level" style={{ color }}>{level}</Text></View>
    <View className="power-risk__track">
      {points.map((point, index) => <View className="power-risk__bar" key={point.time} style={{ height: `${Math.max(12, ((point.value ?? 0) / Math.max(max, 1)) * 100)}%`, backgroundColor: index === 0 ? color : '#9fc4f7' }} />)}
    </View>
    <View className="power-risk__range"><Text>{time(points[0]!.time)} 起</Text><Text>峰值 {peakPower.value} {peakPower.unit} · {time(peak.time)}</Text><Text>低点 {troughPower.value} {troughPower.unit} · {time(trough.time)}</Text></View>
    {alert ? <Text className="power-risk__note">{alert.title} · {alert.description}</Text> : <Text className="power-risk__note">区间变化约 {spread.toFixed(1)}% 装机，曲线来自当前预测批次</Text>}
  </View>
}
