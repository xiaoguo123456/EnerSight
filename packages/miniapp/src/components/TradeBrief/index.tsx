import { Button, Text, View } from '@tarojs/components'
import { formatBeijingTime, formatEnergy } from '@enersight/core/format'
import type { FleetSignalResponse } from '@enersight/core/types'
import { Icon } from '../Icon'
import { InfoTip } from '../InfoTip'
import './index.scss'

function energy(value: number | null | undefined) {
  const result = formatEnergy(value)
  return `${result.value} ${result.unit}`
}

function change(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '暂无可比变化'
  if (Math.abs(value) < 0.05) return '基本持平'
  return `${value > 0 ? '上升' : '下降'} ${Math.abs(value).toFixed(1)}%`
}

function headline(data: FleetSignalResponse) {
  if (data.reason) return data.reason
  const combined = data.combined?.change_percent
  const direction = combined == null || Math.abs(combined) < 0.05 ? '基本稳定' : combined > 0 ? '预计上升' : '预计下降'
  const window = data.top_windows[0]
  if (window) {
    const start = window.start_hour.slice(11, 16)
    const end = window.end_hour.slice(11, 16)
    return `目录预测出力${direction}，${start}–${end}变化最明显`
  }
  return `目录预测出力${direction}，等待更多时段变化`
}

/**
 * 不需要合同或价格数据的交易预测简报：只描述新能源供给、预报分歧和变化时段。
 * 数字均来自全目录已有签发快照，避免给出没有结算依据的金额结论。
 */
export function TradeBrief({
  data, date, scopeLabel, onShare, onSupplyMap,
}: {
  data: FleetSignalResponse
  date: string
  scopeLabel?: string
  onShare?: () => void
  onSupplyMap?: () => void
}) {
  const range = data.model_range
  const latest = data.evolution[data.evolution.length - 1]
  const previous = data.evolution[data.evolution.length - 2]
  const revision = latest && previous ? latest.energy_kwh - previous.energy_kwh : null
  return <View className="trade-brief">
    <View className="trade-brief__head">
      <View className="trade-brief__title"><Icon name="barChart" size={17} color="#1677ff" /><Text>交易预测简报 · {date.slice(5).replace('-', '/')}</Text><InfoTip title="交易预测简报口径" content="交易预测简报只比较平台公开电站目录对同一目标日的预测变化。预测电量是目录样本合计；较上一轮是同一批场站、同一模型的最新签发变化；模型分歧是可比模型之间的电量范围。重点时段的百分比按目录装机容量归一化。它不代表实际并网电量、限电结果、成交电价或报价建议。" /></View>
      <View className="trade-brief__actions">
        {onShare && <Button className="trade-brief__action" onClick={onShare}>分享</Button>}
        {onSupplyMap && <Button className="trade-brief__action" onClick={onSupplyMap}>供给地图</Button>}
      </View>
    </View>
    <Text className="trade-brief__scope">{scopeLabel ? `${scopeLabel} · ` : ''}平台公开目录预测</Text>
    <Text className="trade-brief__headline">{headline(data)}</Text>
    <View className="trade-brief__metrics">
      <View><Text className="trade-brief__label">预测电量</Text><Text className="trade-brief__value">{energy(data.combined?.current_kwh)}</Text></View>
      <View><Text className="trade-brief__label">较上一轮</Text><Text className="trade-brief__value">{change(data.combined?.change_percent)}</Text></View>
      <View><Text className="trade-brief__label">模型分歧</Text><Text className="trade-brief__value">{range?.spread_percent == null ? '暂无' : `${range.spread_percent.toFixed(1)}%`}</Text></View>
    </View>
    {(data.top_windows.length > 0 || revision != null) && <View className="trade-brief__signals">
      {data.top_windows.slice(0, 2).map(item => <View className="trade-brief__signal" key={item.start_hour}>
        <Icon name={item.change_capacity_percent > 0 ? 'arrowUp' : 'arrowDown'} size={14} color={item.change_capacity_percent > 0 ? '#dc2626' : '#15803d'} />
        <Text>{item.start_hour.slice(11, 16)}–{item.end_hour.slice(11, 16)} · 预测功率{item.change_capacity_percent > 0 ? '上升' : '下降'} {Math.abs(item.change_capacity_percent).toFixed(1)}%（按装机）</Text>
      </View>)}
      {revision != null && <View className="trade-brief__signal"><Icon name="trendingUp" size={14} color="#64748b" /><Text>最新一轮修订 {revision >= 0 ? '+' : ''}{energy(revision)}</Text></View>}
    </View>}
    <View className="trade-brief__foot">
      <Text>{data.generated_at ? `${formatBeijingTime(data.generated_at)} 更新` : '等待今日签发'}</Text>
      <Text>预测参考，不代表实际并网电量</Text>
    </View>
  </View>
}
