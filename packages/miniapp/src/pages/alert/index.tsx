import { useAppShare } from '@/hooks/useAppShare'
import { View, Text } from '@tarojs/components'
import { useEffect, useState } from 'react'
import Taro, { usePullDownRefresh } from '@tarojs/taro'
import type { AlertLevel } from '@enersight/core/types'
import { alertsApi } from '@/api/alerts'
import {
  AlertCard, AlertRecordList, CloudMotionStats, EmptyState, ErrorState, PageTitleBar,
  SectionHeader, SegmentedTabs, Skeleton, Icon,
} from '@/components'
import { useRequest } from '@/hooks/useRequest'
import { useStationStore } from '@/store'
import { formatBeijingTime, isDataStale } from '@enersight/core/format'
import './index.scss'
import { SatelliteTimeline } from '@/components/SatelliteTimeline'

type Filter = 'all' | Exclude<AlertLevel, 'cleared'>

/**
 * 信息层级：用户最关心「还有多久受影响」。
 * 当前预警 + 外推数据编成一组放最上面，云图退到第二组，记录最后。
 */
export default function AlertCenter() {
  useAppShare()
  const currentId = useStationStore((s) => s.currentId)
  const [filter, setFilter] = useState<Filter>('all')
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 60_000); return () => clearInterval(timer) }, [])

  const cur = useRequest(() => alertsApi.current(currentId ?? undefined), [currentId])
  // cleared 只在「全部」里出现，等级筛选由服务端做。docs/06 §9.1
  const list = useRequest(() => alertsApi.list(currentId ?? undefined, filter), [currentId, filter])

  usePullDownRefresh(async () => { await Promise.all([cur.reload(), list.reload()]); Taro.stopPullDownRefresh() })
  const noStation = cur.status === 'error' && cur.error.status === 404
  const refreshing = cur.refreshing || list.refreshing
  const refresh = () => { if (!refreshing) { void cur.reload(); void list.reload() } }
  const checkStale = cur.status === 'success' && isDataStale(cur.data.checked_at, 15, now)
  const refreshFailed = !!cur.refreshError || !!list.refreshError
  return (
    <View className="alerts">
      <PageTitleBar
        title="预警中心"
      />

      <View className="alerts__body">
        {cur.status === 'success' && <View className="alerts__context">
          <View className="alerts__station" onClick={() => Taro.switchTab({ url: '/pages/station/index' })}>
            <Text>{cur.data.station?.name ?? '选择电站'}</Text><Icon name="chevronDown" size={14} />
          </View>
          <View className="alerts__tools">
            <View className="alerts__refresh" onClick={refresh}><Text>{refreshing ? '刷新中' : '刷新'}</Text></View>
          </View>
        </View>}
        {(refreshFailed || checkStale) && <View className="alerts__status" onClick={refresh}><Text>{refreshFailed ? '刷新失败 · 点击重试' : '检查已过期 · 点击刷新'}</Text></View>}
        {cur.status === 'loading' && <Skeleton height={120} lines={3} />}

        {noStation && (
          <EmptyState icon="mapPin" title="目录暂无电站" description="平台更新目录后即可查看预警" />
        )}
        {cur.status === 'error' && !noStation && <ErrorState error={cur.error} onRetry={cur.reload} />}

        {cur.status === 'success' && (
          cur.data.alert ? (
            <View className="alerts__group">
              <AlertCard
                level={cur.data.alert.level}
                title={cur.data.alert.title}
                description={cur.data.alert.description}
                publishedAt={formatBeijingTime(cur.data.alert.published_at)}
              />
              {cur.data.cloud_motion && (
                <CloudMotionStats
                  distanceKm={cur.data.cloud_motion.distance_km}
                  direction={cur.data.cloud_motion.direction}
                  directionDetail={cur.data.cloud_motion.direction_detail}
                  impactInMinutes={cur.data.cloud_motion.impact_in_minutes}
                  impactStartTime={cur.data.cloud_motion.impact_start_time}
                  referenceStation={cur.data.cloud_motion.reference_station}
                  impactStartAt={cur.data.cloud_motion.impact_start_at}
                />
              )}
            </View>
          ) : (
            <View className="alerts__calm">
              <Text className="alerts__calm-title">当前未触发预警</Text>
            </View>
          )
        )}

        {/* 直接展示，与当前预警并行请求，不等待其冷启动。 */}
        {!noStation && <SatelliteTimeline key={currentId ?? 'default'} stationId={currentId ?? undefined} />}
        {!noStation && (
          <View className="alerts__card">
            <SectionHeader icon="clipboard" title="预警记录" />
            <SegmentedTabs
              value={filter}
              onChange={(v) => setFilter(v as Filter)}
              options={[
                { value: 'all', label: '全部' },
                { value: 'minor', label: '轻度', dotColor: '#b45309' },
                { value: 'moderate', label: '中度', dotColor: '#f59e0b' },
                { value: 'severe', label: '重度', dotColor: '#ef4444' },
              ]}
            />
            {list.status === 'loading' && <Skeleton height={80} lines={2} />}
            {list.status === 'error' && <ErrorState error={list.error} onRetry={list.reload} />}
            {list.status === 'success' && list.data.alerts.length === 0 && (
              <Text className="alerts__calm-note">暂无预警记录</Text>
            )}
            {list.status === 'success' && list.data.alerts.length > 0 && (
              <AlertRecordList
                records={list.data.alerts.map((a) => ({
                  id: a.id, level: a.level, title: a.title, description: a.description,
                  published_at: formatBeijingTime(a.published_at),
                }))}
              />
            )}
          </View>
        )}
      </View>
    </View>
  )
}
