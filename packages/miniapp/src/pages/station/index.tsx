import { View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useMemo, useState } from 'react'
import type { StationType } from '@enersight/core/types'
import {
  AddStationCard, AppHeader, FloatingActionButton, SegmentedTabs, StationCard,
} from '@/components'
import { mockStations } from '@/mocks'
import './index.scss'

export default function StationList() {
  const [type, setType] = useState<'all' | StationType>('all')

  const list = useMemo(
    () => (type === 'all' ? mockStations : mockStations.filter((s) => s.type === type)),
    [type],
  )

  // 计数由服务端下发，避免前端在分页数据上算总数。docs/06 §5.1
  const counts = useMemo(() => ({
    all: mockStations.length,
    solar: mockStations.filter((s) => s.type === 'solar').length,
    wind: mockStations.filter((s) => s.type === 'wind').length,
  }), [])

  return (
    <View className="stations">
      <AppHeader title="我的站点" subtitle="管理您的能源站点，实时掌握运行状态" />

      <View className="stations__body">
        <SegmentedTabs
          value={type}
          onChange={(v) => setType(v as 'all' | StationType)}
          options={[
            { value: 'all', label: '全部', count: counts.all },
            { value: 'solar', label: '光伏', count: counts.solar },
            { value: 'wind', label: '风电', count: counts.wind },
          ]}
        />

        <AddStationCard />

        {list.map((s) => (
          <StationCard
            key={s.id}
            {...s}
            onTap={() => Taro.navigateTo({ url: `/pages/station/detail?id=${s.id}` })}
          />
        ))}

        <FloatingActionButton text="新增站点" />
      </View>
    </View>
  )
}
