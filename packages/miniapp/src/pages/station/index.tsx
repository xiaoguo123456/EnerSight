import { View, Text, Input, Button } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useEffect, useState } from 'react'
import { formatPower } from '@enersight/core/format'
import type { StationSummary, StationType } from '@enersight/core/types'
import { stationsApi } from '@/api/stations'
import { useStationStore } from '@/store'
import { EmptyState, ErrorState, Icon, PageTitleBar, SegmentedTabs, Skeleton } from '@/components'
import { useRequest } from '@/hooks/useRequest'
import './catalog.scss'

type TypeFilter = 'all' | StationType
const PAGE_SIZE = 50

/** 所有用户共享目录；只记住当前选择，不创建个人站点。 */
export default function Stations() {
  const [keyword, setKeyword] = useState('')
  const [query, setQuery] = useState({ keyword: '', type: 'all' as TypeFilter, offset: 0 })
  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery((q) => q.keyword === keyword.trim() ? q : { ...q, keyword: keyword.trim(), offset: 0 })
    }, 300)
    return () => clearTimeout(timer)
  }, [keyword])
  const req = useRequest(() => stationsApi.list({
    keyword: query.keyword || undefined,
    type: query.type === 'all' ? undefined : query.type,
    offset: query.offset, limit: PAGE_SIZE,
  }), [query.keyword, query.type, query.offset])
  const setCurrent = useStationStore((s) => s.setCurrent)
  const currentId = useStationStore((s) => s.currentId)
  const pick = (p: StationSummary) => {
    setCurrent(p.id)
    void Taro.navigateTo({ url: `/pages/station/detail?id=${encodeURIComponent(p.id)}` })
  }
  const turnPage = (offset: number) => {
    setQuery((q) => ({ ...q, offset }))
    void Taro.pageScrollTo({ scrollTop: 0, duration: 0 })
  }
  const counts = req.data?.counts

  return (
    <View className="catalog">
      <PageTitleBar title="电站目录" />
      <View className="catalog__body">
        <Text className="catalog__intro">全部电站均可直接查看，无需添加</Text>
        <View className="catalog__search">
          <Icon name="search" size={15} color="#9ca3af" />
          <Input className="catalog__search-input" placeholder="搜索电站名称 / 省市 / 业主"
            placeholderClass="catalog__ph" value={keyword} maxlength={64}
            onInput={(e) => setKeyword(e.detail.value)} />
          {keyword && <View className="catalog__clear" onClick={() => setKeyword('')}>
            <Icon name="minus" size={12} color="#9ca3af" />
          </View>}
        </View>
        <SegmentedTabs value={query.type}
          onChange={(v) => setQuery((q) => ({ ...q, type: v as TypeFilter, offset: 0 }))}
          options={[
            { value: 'all', label: `全部${counts ? ` ${counts.all}` : ''}` },
            { value: 'solar', label: `光伏${counts ? ` ${counts.solar}` : ''}` },
            { value: 'wind', label: `风电${counts ? ` ${counts.wind}` : ''}` },
          ]} />
        {req.status === 'loading' && <Skeleton height={72} lines={3} />}
        {req.status === 'error' && <ErrorState error={req.error} onRetry={req.reload} />}
        {req.status === 'success' && <>
          <Text className="catalog__intro">共 {req.data.total} 座 · 按装机容量排序</Text>
          {req.data.stations.length === 0 && <EmptyState icon="search"
            title={query.keyword ? '没有匹配的电站' : '目录暂无电站'}
            description={query.keyword ? '试试电站名称或所在省市' : '平台更新目录后将在这里展示'} />}
          <View className="catalog__list">
            {req.data.stations.map((p) => {
              const cap = formatPower(p.capacity)
              return <View className="catalog__item" key={p.id} hoverClass="pressed" onClick={() => pick(p)}>
                <View className={`catalog__thumb catalog__thumb--${p.type}`}>
                  <Icon name={p.type === 'solar' ? 'sun' : 'wind'} size={16} color="#fff" fill={p.type === 'solar'} />
                </View>
                <View className="catalog__text">
                  <Text className="catalog__name">{p.name}</Text>
                  <Text className="catalog__meta">{p.type === 'solar' ? '光伏' : '风电'} · {cap.value} {cap.unit}{p.address ? ` · ${p.address}` : ''}</Text>
                </View>
                <Text className="catalog__view">{currentId === p.id ? '当前' : '查看'}</Text>
              </View>
            })}
          </View>
          {(req.data.total > PAGE_SIZE || query.offset > 0) && <View className="catalog__pagination">
            <Button className="catalog__page-button" disabled={query.offset === 0}
              onClick={() => turnPage(Math.max(0, query.offset - PAGE_SIZE))}>上一页</Button>
            <Text>{Math.floor(query.offset / PAGE_SIZE) + 1} / {Math.max(1, Math.ceil(req.data.total / PAGE_SIZE))}</Text>
            <Button className="catalog__page-button" disabled={!req.data.has_more}
              onClick={() => turnPage(query.offset + PAGE_SIZE)}>下一页</Button>
          </View>}
          <Text className="catalog__credit">数据来源：Global Energy Monitor、WRI Global Power Plant Database（CC BY 4.0）</Text>
        </>}
      </View>
    </View>
  )
}
