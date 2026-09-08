import { View } from '@tarojs/components'
import { useMemo, useState } from 'react'
import type { AlertLevel } from '@enersight/core/types'
import {
  AlertCard, AlertRecordList, CloudMotionStats, PageTitleBar,
  SatelliteCloudCard, SectionHeader, SegmentedTabs,
} from '@/components'
import { mockAlerts } from '@/mocks'
import './index.scss'

type Filter = 'all' | Exclude<AlertLevel, 'cleared'>

export default function AlertCenter() {
  const [filter, setFilter] = useState<Filter>('all')
  const { satellite, current, cloudMotion, records } = mockAlerts

  // cleared 只在「全部」里出现，不进等级筛选结果。docs/06 §9.1
  const list = useMemo(
    () => (filter === 'all' ? records : records.filter((r) => r.level === filter)),
    [filter],
  )

  return (
    <View className="alerts">
      <PageTitleBar title="预警中心" aside={`${records.length} 条记录`} />

      <View className="alerts__body">
        {/*
          信息层级：用户最关心「还有多久受影响」。
          当前预警 + 外推数据编成一组放最上面，云图退到第二组，记录最后。
        */}
        <View className="alerts__group">
          <AlertCard
            level={current.level}
            title={current.title}
            description={current.description}
            publishedAt={current.published_at}
          />
          <CloudMotionStats
            distanceKm={cloudMotion.distance_km}
            direction={cloudMotion.direction}
            directionDetail={cloudMotion.direction_detail}
            impactInMinutes={cloudMotion.impact_in_minutes}
            impactStartTime={cloudMotion.impact_start_time}
            referenceStation={cloudMotion.reference_station}
          />
        </View>

        <SatelliteCloudCard observedAt={satellite.observed_at} />

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
          <AlertRecordList records={list} />
        </View>
      </View>
    </View>
  )
}
