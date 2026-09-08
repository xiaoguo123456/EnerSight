import { View, Text } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useState } from 'react'
import type { StationType } from '@enersight/core/types'
import { stationsApi } from '@/api/stations'
import {
  EmptyState, ErrorState, FloatingActionButton, Icon, PageTitleBar,
  SegmentedTabs, Skeleton, StationCard,
} from '@/components'
import { useRequest } from '@/hooks/useRequest'
import './index.scss'

type Filter = 'all' | StationType

export default function StationList() {
  const [type, setType] = useState<Filter>('all')

  // 全量拉取、前端筛选：站点数量小，省一次请求；计数由服务端给
  const req = useRequest(() => stationsApi.list(), [])

  const all = req.data?.stations ?? []
  const list = type === 'all' ? all : all.filter((s) => s.type === type)
  const counts = req.data?.counts ?? { all: 0, solar: 0, wind: 0 }

  return (
    <View className="stations">
      <PageTitleBar
        title="我的站点"
        aside={req.status === 'success' ? `共 ${counts.all} 个` : undefined}
      />

      <View className="stations__body">
        <SegmentedTabs
          value={type}
          onChange={(v) => setType(v as Filter)}
          options={[
            { value: 'all', label: '全部', count: counts.all },
            { value: 'solar', label: '光伏', count: counts.solar },
            { value: 'wind', label: '风电', count: counts.wind },
          ]}
        />

        <View className="stations__add">
          <View className="stations__add-item" hoverClass="pressed">
            <Icon name="mapPin" size={15} color="#1677ff" fill />
            <Text className="stations__add-text">获取当前位置添加</Text>
          </View>
          <View className="stations__add-divider" />
          <View className="stations__add-item" hoverClass="pressed">
            <Icon name="navigation" size={15} color="#16a34a" fill />
            <Text className="stations__add-text">输入经纬度添加</Text>
          </View>
        </View>

        {req.status === 'loading' && (
          <>
            <Skeleton height={150} />
            <Skeleton height={150} />
          </>
        )}

        {req.status === 'error' && <ErrorState error={req.error} onRetry={req.reload} />}

        {req.status === 'success' && list.length === 0 && (
          <EmptyState
            icon="mapPin"
            title={all.length === 0 ? '还没有站点' : '该类型下没有站点'}
            description={all.length === 0 ? '添加第一个站点，开始查看发电环境' : undefined}
          />
        )}

        {req.status === 'success' &&
          list.map((s) => (
            <StationCard
              key={s.id}
              name={s.name}
              type={s.type}
              status={s.status}
              capacity={s.capacity}
              address={s.address ?? '—'}
              latitude={s.latitude}
              longitude={s.longitude}
              metrics={s.metrics}
              onTap={() => Taro.navigateTo({ url: `/pages/station/detail?id=${s.id}` })}
            />
          ))}

        <FloatingActionButton text="新增站点" />
      </View>
    </View>
  )
}
