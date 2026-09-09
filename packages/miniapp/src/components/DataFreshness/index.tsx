import { View, Text } from '@tarojs/components'
import { formatBeijingTime, isDataStale } from '@enersight/core/format'
import { useEffect, useState } from 'react'
import './index.scss'
export function DataFreshness({ time, label = '气象数据', staleMinutes = 120, refreshing, failed, onRefresh }: {
  time?: string | null; label?: string; staleMinutes?: number; refreshing?: boolean; failed?: boolean; onRefresh?: () => void
}) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 60_000); return () => clearInterval(timer) }, [])
  return <View className="data-freshness">
    <Text>{label} · {formatBeijingTime(time)}（北京时间）</Text>
    {isDataStale(time, staleMinutes, now) && <Text className="data-freshness__warning">数据较早，请谨慎参考</Text>}
    {failed && <Text className="data-freshness__warning">刷新失败，保留上次结果</Text>}
    {onRefresh && <View className="data-freshness__action" onClick={() => { if (!refreshing) onRefresh() }}>{refreshing ? '刷新中…' : failed ? '重新加载' : '刷新数据'}</View>}
  </View>
}
