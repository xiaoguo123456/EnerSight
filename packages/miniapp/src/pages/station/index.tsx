import { View, Text } from '@tarojs/components'
import Taro, { useDidShow } from '@tarojs/taro'
import { useRef, useState } from 'react'
import type { StationType } from '@enersight/core/types'
import { stationsApi } from '@/api/stations'
import {
  EmptyState, ErrorState, FloatingActionButton, Icon, PageTitleBar,
  SegmentedTabs, Skeleton, StationCard,
} from '@/components'
import { useRequest } from '@/hooks/useRequest'
import { useStationStore } from '@/store'
import './index.scss'

type Filter = 'all' | StationType

/** 三种录入方式。docs/01 §六：定位 / 手输经纬度 / 从公开电站目录选 */
const ADD_WAYS = ['获取当前位置添加', '输入经纬度添加', '从公开电站选择'] as const

async function addByLocation() {
  try {
    const r = await Taro.getLocation({ type: 'gcj02' })
    await Taro.navigateTo({
      url: `/pages/station/form?lat=${r.latitude.toFixed(5)}&lng=${r.longitude.toFixed(5)}`,
    })
  } catch {
    Taro.showToast({ title: '定位失败，可改为手动输入经纬度', icon: 'none' })
  }
}

function addManually() {
  void Taro.navigateTo({ url: '/pages/station/form' })
}

function addFromCatalog() {
  void Taro.navigateTo({ url: '/pages/station/catalog' })
}

const ADD_ACTIONS = [addByLocation, addManually, addFromCatalog]

export default function StationList() {
  const [type, setType] = useState<Filter>('all')
  const currentId = useStationStore((s) => s.currentId)
  const setCurrent = useStationStore((s) => s.setCurrent)

  // 全量拉取、前端筛选：站点数量小，省一次请求；计数由服务端给
  const req = useRequest(() => stationsApi.list(), [])
  // 从表单页返回时刷新；首次 didShow 与 useRequest 的首次请求重叠，跳过
  const shown = useRef(false)
  useDidShow(() => {
    if (shown.current) void req.reload()
    shown.current = true
  })

  const all = req.data?.stations ?? []
  const list = type === 'all' ? all : all.filter((s) => s.type === type)
  const counts = req.data?.counts ?? { all: 0, solar: 0, wind: 0 }

  const openAddMenu = async () => {
    try {
      const { tapIndex } = await Taro.showActionSheet({ itemList: [...ADD_WAYS] })
      await ADD_ACTIONS[tapIndex]?.()
    } catch {
      // 用户取消
    }
  }

  const openMoreMenu = async (id: string, name: string) => {
    let tapIndex: number
    try {
      ({ tapIndex } = await Taro.showActionSheet({ itemList: ['编辑站点', '删除站点'], itemColor: '#1f2937' }))
    } catch {
      return
    }
    if (tapIndex === 0) {
      void Taro.navigateTo({ url: `/pages/station/form?id=${id}` })
      return
    }
    const { confirm } = await Taro.showModal({
      title: '删除站点',
      content: `确定删除「${name}」？预警与发电记录会一并删除。`,
      confirmText: '删除',
      confirmColor: '#ef4444',
    })
    if (!confirm) return
    try {
      await stationsApi.remove(id)
      if (currentId === id) {
        const rest = all.filter((s) => s.id !== id)
        if (rest[0]) setCurrent(rest[0].id)
      }
      Taro.showToast({ title: '已删除', icon: 'success' })
      void req.reload()
    } catch {
      Taro.showToast({ title: '删除失败', icon: 'none' })
    }
  }

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
          <View className="stations__add-item" hoverClass="pressed" onClick={() => void addByLocation()}>
            <Icon name="mapPin" size={15} color="#1677ff" fill />
            <Text className="stations__add-text">当前位置</Text>
          </View>
          <View className="stations__add-divider" />
          <View className="stations__add-item" hoverClass="pressed" onClick={addManually}>
            <Icon name="navigation" size={15} color="#16a34a" fill />
            <Text className="stations__add-text">输入经纬度</Text>
          </View>
          <View className="stations__add-divider" />
          <View className="stations__add-item" hoverClass="pressed" onClick={addFromCatalog}>
            <Icon name="layers" size={15} color="#7c3aed" />
            <Text className="stations__add-text">公开电站</Text>
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
            description={all.length === 0 ? '添加第一个站点，或从公开电站目录里选一个' : undefined}
            actionText={all.length === 0 ? '选择公开电站' : undefined}
            onAction={all.length === 0 ? addFromCatalog : undefined}
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
              onMore={() => void openMoreMenu(s.id, s.name)}
            />
          ))}

        <FloatingActionButton text="新增站点" onTap={() => void openAddMenu()} />
      </View>
    </View>
  )
}
