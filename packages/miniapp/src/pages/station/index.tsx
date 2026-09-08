import { View, Text } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useMemo, useState } from 'react'
import type { StationType } from '@enersight/core/types'
import {
  FloatingActionButton, Icon, PageTitleBar, SegmentedTabs, StationCard,
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
      <PageTitleBar title="我的站点" aside={`共 ${counts.all} 个`} />

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

        {/*
          有站点后「添加」不是主操作，缩成一行入口；悬浮按钮承担主入口。
          首次无站点时应改为大面积引导（空态），待接口后处理。
        */}
        <View className="stations__add">
          <View className="stations__add-item">
            <Icon name="mapPin" size={15} color="#1677ff" fill />
            <Text className="stations__add-text">获取当前位置添加</Text>
          </View>
          <View className="stations__add-divider" />
          <View className="stations__add-item">
            <Icon name="navigation" size={15} color="#16a34a" fill />
            <Text className="stations__add-text">输入经纬度添加</Text>
          </View>
        </View>

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
