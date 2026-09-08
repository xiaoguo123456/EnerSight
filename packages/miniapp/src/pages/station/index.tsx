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

/** 站点只能从公开电站目录添加，不提供自建。docs/01 §六 */
function addFromCatalog() {
  void Taro.navigateTo({ url: '/pages/station/catalog' })
}

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

  const openMoreMenu = async (id: string, name: string) => {
    let tapIndex: number
    try {
      ({ tapIndex } = await Taro.showActionSheet({ itemList: ['编辑参数', '移除站点'], itemColor: '#1f2937' }))
    } catch {
      return
    }
    if (tapIndex === 0) {
      void Taro.navigateTo({ url: `/pages/station/form?id=${id}` })
      return
    }
    const { confirm } = await Taro.showModal({
      title: '移除站点',
      content: `从我的站点移除「${name}」？预警与发电记录会一并删除，之后可从公开电站里再次添加。`,
      confirmText: '移除',
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

        <View className="stations__add" hoverClass="pressed" onClick={addFromCatalog}>
          <View className="stations__add-icon">
            <Icon name="layers" size={16} color="#1677ff" />
          </View>
          <View className="stations__add-text-wrap">
            <Text className="stations__add-text">从公开电站添加</Text>
            <Text className="stations__add-sub">全国近两万座光伏 / 风电场站，按附近或名称查找</Text>
          </View>
          <Icon name="chevronRight" size={14} color="#9ca3af" />
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
            description={all.length === 0 ? '从公开电站目录里选一座，开始查看发电环境' : undefined}
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

        <FloatingActionButton text="添加电站" onTap={addFromCatalog} />
      </View>
    </View>
  )
}
