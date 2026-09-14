import { Button, View, Text } from '@tarojs/components'
import { formatBeijingTime, isDataStale } from '@enersight/core/format'
import { useEffect, useState } from 'react'
import { InfoTip } from '../InfoTip'
import './index.scss'
export function DataFreshness({ time, label = '气象数据', timeLabel = '', staleMinutes = 120, refreshing, failed, onRefresh, compact = false, detail }: {
  time?: string | null; label?: string; timeLabel?: string; staleMinutes?: number; refreshing?: boolean; failed?: boolean; onRefresh?: () => void; compact?: boolean; detail?: string
}) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 60_000); return () => clearInterval(timer) }, [])
  if (compact) return <View className="data-freshness data-freshness--compact">
    <View className="data-freshness__row"><Text>{timeLabel || '更新于 '}{formatBeijingTime(time)}</Text><InfoTip title="数据时间与口径" content={[label, `${timeLabel}${formatBeijingTime(time)}（北京时间）`, detail].filter(Boolean).join('\n')} />
      {onRefresh && <Button className="data-freshness__action" disabled={refreshing} onClick={onRefresh}>{refreshing ? '刷新中…' : failed ? '重试' : '刷新'}</Button>}
    </View>
    {isDataStale(time, staleMinutes, now) && <Text className="data-freshness__warning">数据较早，请谨慎参考</Text>}
    {failed && <Text className="data-freshness__warning">刷新失败，保留上次结果</Text>}
  </View>
  return <View className="data-freshness">
    <Text>{`${label} · ${timeLabel}${formatBeijingTime(time)}（北京时间）`}</Text>
    {isDataStale(time, staleMinutes, now) && <Text className="data-freshness__warning">数据较早，请谨慎参考</Text>}
    {failed && <Text className="data-freshness__warning">刷新失败，保留上次结果</Text>}
    {onRefresh && <View className="data-freshness__action" onClick={() => { if (!refreshing) onRefresh() }}>{refreshing ? '刷新中…' : failed ? '重新加载' : '刷新数据'}</View>}
  </View>
}
