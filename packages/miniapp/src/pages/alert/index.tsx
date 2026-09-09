import { View, Text } from '@tarojs/components'
import { useState } from 'react'
import Taro, { usePullDownRefresh } from '@tarojs/taro'
import type { AlertLevel } from '@enersight/core/types'
import { alertsApi } from '@/api/alerts'
import {
  AlertCard, AlertRecordList, CloudMotionStats, EmptyState, ErrorState, PageTitleBar,
  SectionHeader, SegmentedTabs, Skeleton,
} from '@/components'
import { useRequest } from '@/hooks/useRequest'
import { useStationStore } from '@/store'
import { DataFreshness } from '@/components/DataFreshness'
import { formatBeijingTime } from '@enersight/core/format'
import './index.scss'
import { SatelliteTimeline } from '@/components/SatelliteTimeline'

type Filter = 'all' | Exclude<AlertLevel, 'cleared'>

/**
 * 信息层级：用户最关心「还有多久受影响」。
 * 当前预警 + 外推数据编成一组放最上面，云图退到第二组，记录最后。
 */
export default function AlertCenter() {
  const currentId = useStationStore((s) => s.currentId)
  const [filter, setFilter] = useState<Filter>('all')

  const cur = useRequest(() => alertsApi.current(currentId ?? undefined), [currentId])
  // cleared 只在「全部」里出现，等级筛选由服务端做。docs/06 §9.1
  const list = useRequest(() => alertsApi.list(currentId ?? undefined, filter), [currentId, filter])

  usePullDownRefresh(async () => { await Promise.all([cur.reload(), list.reload()]); Taro.stopPullDownRefresh() })
  const noStation = cur.status === 'error' && cur.error.status === 404
  return (
    <View className="alerts">
      <PageTitleBar
        title="预警中心"
        aside={list.status === 'success' ? `${list.data.alerts.length} 条记录` : undefined}
      />

      <View className="alerts__body">
        {cur.status === 'success' && cur.data.station && <View className="alerts__context" onClick={() => Taro.switchTab({ url: '/pages/station/index' })}>
          <Text className="alerts__station">{cur.data.station.name}</Text>
          <Text className="alerts__checked">切换电站 · {formatBeijingTime(cur.data.checked_at)} 检查（北京时间）</Text>
        </View>}
        {cur.status === 'success' && <DataFreshness label="规则检查" time={cur.data.checked_at} staleMinutes={15} refreshing={cur.refreshing || list.refreshing} failed={!!cur.refreshError || !!list.refreshError} onRefresh={() => { void cur.reload(); void list.reload() }} />}
        {cur.status === 'loading' && <Skeleton height={120} lines={3} />}

        {noStation && (
          <EmptyState icon="mapPin" title="目录暂无电站" description="平台更新目录后即可查看预警" />
        )}
        {cur.status === 'error' && !noStation && <ErrorState error={cur.error} onRetry={cur.reload} />}

        {cur.status === 'success' && (
          cur.data.alert ? (
            <View className="alerts__group">
              <DataFreshness label="预警发布" time={cur.data.alert.published_at} staleMinutes={30} />
              <AlertCard
                level={cur.data.alert.level}
                title={cur.data.alert.title}
                description={cur.data.alert.description}
                publishedAt={`${formatBeijingTime(cur.data.alert.published_at)}（北京时间）`}
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
              <Text className="alerts__calm-note">本次检查未命中监测规则，不代表发电条件良好。请结合气象趋势判断。</Text>
            </View>
          )
        )}

        {cur.status === 'success' && cur.data.station && <SatelliteTimeline key={cur.data.station.id} stationId={cur.data.station.id} />}
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
              <Text className="alerts__calm-note">暂无匹配记录，触发预警后将在这里展示。</Text>
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
