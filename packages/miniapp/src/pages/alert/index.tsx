import { View, Text } from '@tarojs/components'
import { useState } from 'react'
import Taro from '@tarojs/taro'
import type { AlertLevel } from '@enersight/core/types'
import { alertsApi } from '@/api/alerts'
import {
  AlertCard, AlertRecordList, CloudMotionStats, EmptyState, ErrorState, PageTitleBar,
  SatelliteCloudCard, SectionHeader, SegmentedTabs, Skeleton,
} from '@/components'
import { useRequest } from '@/hooks/useRequest'
import { useMapStore, useStationStore } from '@/store'
import './index.scss'

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

  const noStation = cur.status === 'error' && cur.error.status === 404
  const setActiveLayer = useMapStore((s) => s.setActiveLayer)

  /** 全屏 = 去地图页看云图层，地图页会自动定位到当前站点 */
  const openCloudMap = () => {
    setActiveLayer('cloud')
    Taro.switchTab({ url: '/pages/map/index' })
  }

  return (
    <View className="alerts">
      <PageTitleBar
        title="预警中心"
        aside={list.status === 'success' ? `${list.data.alerts.length} 条记录` : undefined}
      />

      <View className="alerts__body">
        {cur.status === 'success' && cur.data.station && <View className="alerts__context" onClick={() => Taro.switchTab({ url: '/pages/station/index' })}>
          <Text className="alerts__station">{cur.data.station.name}</Text>
          <Text className="alerts__checked">切换电站 · {new Date(cur.data.checked_at).toLocaleTimeString('zh-CN', { hour12: false })} 检查</Text>
        </View>}
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
                publishedAt={cur.data.alert.published_at.slice(0, 16).replace('T', ' ')}
              />
              {cur.data.cloud_motion && (
                <CloudMotionStats
                  distanceKm={cur.data.cloud_motion.distance_km}
                  direction={cur.data.cloud_motion.direction}
                  directionDetail={cur.data.cloud_motion.direction_detail}
                  impactInMinutes={cur.data.cloud_motion.impact_in_minutes}
                  impactStartTime={cur.data.cloud_motion.impact_start_time}
                  referenceStation={cur.data.cloud_motion.reference_station}
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

        {cur.status === 'success' && (
          /* 无预警时云图仍展示；夜间是红外，satellite 为 null 只有上游故障一种情况。docs/06 §9.2 */
          <SatelliteCloudCard
            satellite={cur.data.satellite}
            onFullscreen={cur.data.satellite ? openCloudMap : undefined}
          />
        )}

        {!noStation && (
          <View className="alerts__card">
            <SectionHeader icon="clipboard" title="预警记录" />
            <SegmentedTabs
              value={filter}
              onChange={(v) => setFilter(v as Filter)}
              options={[
                { value: 'all', label: '全部' },
                { value: 'minor', label: '轻度', dotColor: '#16a34a' },
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
                  published_at: a.published_at.slice(0, 16).replace('T', ' '),
                }))}
              />
            )}
          </View>
        )}
      </View>
    </View>
  )
}
