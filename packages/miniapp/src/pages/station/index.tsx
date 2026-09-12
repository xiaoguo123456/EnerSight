import { View, Text, Input, Picker, ScrollView, Button } from '@tarojs/components'
import Taro, { useReachBottom, useDidShow } from '@tarojs/taro'
import { useEffect, useRef, useState } from 'react'
import { formatPower, formatCoordinate, thousands } from '@enersight/core/format'
import type { StationSummary, StationType } from '@enersight/core/types'
import { stationsApi } from '@/api/stations'
import { useStationStore } from '@/store'
import { EmptyState, Icon, PageTitleBar, Skeleton } from '@/components'
import './catalog.scss'

type TypeFilter = 'all' | StationType | 'mine'
type Filter = { keyword: string; type: TypeFilter; province: string; sort: 'capacity' | 'name'; offset: number }
const INITIAL: Filter = { keyword: '', type: 'all', province: '', sort: 'capacity', offset: 0 }
const PAGE_SIZE = 20
const TYPE_OPTIONS: { value: TypeFilter; label: string }[] = [
  { value: 'all', label: '全部' }, { value: 'solar', label: '光伏' }, { value: 'wind', label: '风电' }, { value: 'mine', label: '我的站点' },
]

/** 列表先展示可核实的中文地区，完整原始地名仍在详情保留。 */
function regionLabel(address: string | null) {
  if (!address) return '地区信息待补充'
  const chinese = address.match(/[㐀-鿿]+/g)
  return chinese?.join(' · ') || address
}

function curtailmentLabel(s: StationSummary) {
  if (!s.curtailment) return null
  return s.curtailment.mode === 'ratio' ? `限电 ${s.curtailment.ratio_percent}%` : `分时段限电 ${s.curtailment.windows?.length ?? 0} 条`
}

export default function Stations() {
  const [keyword, setKeyword] = useState('')
  const [query, setQuery] = useState<Filter>(INITIAL)
  const [retry, setRetry] = useState(0)
  const [result, setResult] = useState({ items: [] as StationSummary[], total: 0, hasMore: false, regions: [] as string[], loading: true, error: false })
  // 自建站点：一次拉全量，本机按关键词过滤；从表单返回时重新拉取。docs/17 §一
  const [mine, setMine] = useState({ items: [] as StationSummary[], loading: false, error: false, loaded: false })
  const [mineVersion, setMineVersion] = useState(0)
  const pending = useRef(false)
  const { currentId, recent, favorites, remember } = useStationStore()

  useDidShow(() => {
    const province = Taro.getStorageSync('enersight_prediction_province')
    if (province) { Taro.removeStorageSync('enersight_prediction_province'); setKeyword(''); setQuery({ ...INITIAL, province }) }
    const tab = Taro.getStorageSync('enersight_station_tab')
    if (tab === 'mine') { Taro.removeStorageSync('enersight_station_tab'); setQuery((q) => ({ ...q, type: 'mine', offset: 0 })) }
    setMineVersion((v) => v + 1)
  })

  useEffect(() => {
    const timer = setTimeout(() => setQuery((q) => q.keyword === keyword.trim() ? q : { ...q, keyword: keyword.trim(), offset: 0 }), 300)
    return () => clearTimeout(timer)
  }, [keyword])

  useEffect(() => {
    if (query.type === 'mine') return
    let cancelled = false
    pending.current = true
    setResult((r) => ({ ...r, items: query.offset === 0 ? [] : r.items, loading: true, error: false }))
    stationsApi.list({ keyword: query.keyword, province: query.province, sort: query.sort, offset: query.offset, type: query.type === 'all' ? undefined : query.type, limit: PAGE_SIZE })
      .then((data) => {
        if (cancelled) return
        setResult((r) => ({ items: query.offset === 0 ? data.stations : [...r.items, ...data.stations.filter((s) => !r.items.some((old) => old.id === s.id))],
          total: data.total, hasMore: data.has_more, regions: data.regions ?? [], loading: false, error: false }))
      })
      .catch(() => { if (!cancelled) setResult((r) => ({ ...r, loading: false, error: true })) })
      .finally(() => { if (!cancelled) pending.current = false })
    return () => { cancelled = true }
  }, [query, retry])

  useEffect(() => {
    if (query.type !== 'mine') return
    let cancelled = false
    setMine((m) => ({ ...m, loading: true, error: false }))
    stationsApi.mine()
      .then((data) => { if (!cancelled) setMine({ items: data.stations, loading: false, error: false, loaded: true }) })
      .catch(() => { if (!cancelled) setMine((m) => ({ ...m, loading: false, error: true })) })
    return () => { cancelled = true }
  }, [query.type, mineVersion])

  const change = (patch: Partial<Filter>) => {
    setQuery((q) => ({ ...q, ...patch, offset: 0 }))
    void Taro.pageScrollTo({ scrollTop: 0, duration: 0 })
  }
  const loadMore = () => {
    if (query.type === 'mine' || pending.current || result.loading || result.error || !result.hasMore) return
    pending.current = true
    setQuery((q) => ({ ...q, offset: q.offset + PAGE_SIZE }))
  }
  useReachBottom(loadMore)
  const pick = (station: StationSummary) => {
    remember(station)
    void Taro.navigateTo({ url: `/pages/station/detail?id=${encodeURIComponent(station.id)}` })
  }
  const addStation = () => void Taro.navigateTo({ url: '/pages/station/form' })
  const editStation = (id: string) => void Taro.navigateTo({ url: `/pages/station/form?id=${encodeURIComponent(id)}` })
  const regions = ['全部地区', ...result.regions]
  const isMine = query.type === 'mine'
  const unfiltered = !query.keyword && query.type === 'all' && !query.province
  const mineItems = mine.items.filter((s) => !query.keyword || s.name.includes(query.keyword) || (s.address ?? '').includes(query.keyword))

  return <View className="catalog">
    <View className="catalog__header">
      <PageTitleBar title="电站" />
      <View className="catalog__controls">
        <View className="catalog__search">
          <Icon name="search" size={19} color="#64748b" />
          <Input className="catalog__search-input" placeholder={isMine ? '搜索我的场站' : '搜索电站、地区或业主'}
            placeholderClass="catalog__ph" value={keyword} maxlength={64}
            onInput={(e) => setKeyword(e.detail.value)} />
          {keyword && <View className="catalog__clear" onClick={() => setKeyword('')}><Icon name="x" size={17} color="#64748b" /></View>}
        </View>
        <View className="catalog__filters">
          <View className="catalog__types">
            {TYPE_OPTIONS.map((opt) => <View key={opt.value}
              className={`catalog__type ${query.type === opt.value ? 'catalog__type--active' : ''}`}
              onClick={() => change({ type: opt.value })}><Text>{opt.label}</Text></View>)}
          </View>
          {!isMine && <Picker mode="selector" range={regions} value={Math.max(0, regions.indexOf(query.province))}
            onChange={(e) => change({ province: regions[Number(e.detail.value)] === '全部地区' ? '' : regions[Number(e.detail.value)] })}>
            <View className="catalog__region"><Text>{query.province || '全部地区'}</Text><Icon name="chevronDown" size={14} color="#64748b" /></View>
          </Picker>}
        </View>
      </View>
    </View>

    {isMine ? <View className="catalog__mine">
      <View className="catalog__add" hoverClass="pressed" onClick={addStation}>
        <View className="catalog__add-icon"><Icon name="plus" size={18} color="#1264d6" /></View>
        <View className="catalog__add-text"><Text className="catalog__add-title">添加场站</Text><Text className="catalog__add-sub">不在公开目录里的场站，填名称、位置和装机容量即可</Text></View>
        <Icon name="chevronRight" size={16} color="#94a3b8" />
      </View>
      <View className="catalog__results-head"><Text>{mine.loading && !mine.loaded ? '正在读取' : `我的场站 ${mine.items.length} 座`}</Text><Text>仅本账号可见</Text></View>
      <View className="catalog__list">
        {mineItems.map((s) => {
          const cap = formatPower(s.capacity)
          const limit = curtailmentLabel(s)
          const daily = s.metrics.daily_generation
          return <View key={s.id} className={`catalog__item ${currentId === s.id ? 'catalog__item--current' : ''}`} hoverClass="pressed" onClick={() => pick(s)}>
            <View className="catalog__item-title"><Text className="catalog__name">{s.name}</Text><Icon name="chevronRight" size={17} color="#94a3b8" /></View>
            <Text className="catalog__address">{s.address ? regionLabel(s.address) : formatCoordinate(s.latitude, s.longitude)}</Text>
            <View className="catalog__meta">
              <View className={`catalog__kind catalog__kind--${s.type}`}><Icon name={s.type === 'solar' ? 'sun' : 'wind'} size={14} color={s.type === 'solar' ? '#a16207' : '#2563eb'} /><Text>{s.type === 'solar' ? '光伏' : '风电'}</Text></View>
              {limit && <Text className="catalog__tag">{limit}</Text>}
              {currentId === s.id && <Text className="catalog__current">当前查看</Text>}
              <Text className="catalog__capacity">{cap.value.replace(/\.0$/, '')}<Text className="catalog__unit"> {cap.unit}</Text></Text>
            </View>
            <View className="catalog__own-row">
              <Text className="catalog__own-metric">{daily == null ? '今日发电估算待定时任务写入' : `今日估算 ${thousands(daily, 0)} kWh${s.metrics.grid_generation != null ? ` · 上网 ${thousands(s.metrics.grid_generation, 0)} kWh` : ''}`}</Text>
              <View className="catalog__edit" hoverClass="pressed" onClick={(e) => { e.stopPropagation(); editStation(s.id) }}><Icon name="pencil" size={14} color="#1264d6" /><Text>编辑</Text></View>
            </View>
          </View>
        })}
      </View>
      {mine.loading && !mine.loaded && <View className="catalog__loading"><Skeleton height={100} lines={3} /></View>}
      {mine.error && <EmptyState icon="alertTriangle" title="自建场站加载失败" actionText="重新加载" onAction={() => setMineVersion((v) => v + 1)} />}
      {!mine.loading && !mine.error && mine.loaded && mine.items.length === 0 && <EmptyState icon="factory" title="还没有自建场站" description="公开目录里找不到的场站可以自己添加，参与预测、预警与报告" actionText="添加场站" onAction={addStation} />}
      {!mine.loading && !mine.error && mine.items.length > 0 && mineItems.length === 0 && <EmptyState icon="search" title="没有匹配的场站" actionText="清除关键词" onAction={() => setKeyword('')} />}
      {mine.loaded && mine.items.length > 0 && <View className="catalog__footer"><Text className="catalog__credit">自建场站仅本账号可见，参与预警、报告与卫星归档，不进入全目录汇总</Text></View>}
    </View> : <>
      {unfiltered && favorites.length > 0 && <View className="catalog__recent">
        <Text className="catalog__eyebrow">常看电站 · 本机收藏</Text>
        <ScrollView scrollX className="catalog__recent-scroll">{favorites.map((s) => <View key={s.id} className="catalog__recent-item" onClick={() => pick(s)}><Text>{s.name}</Text></View>)}</ScrollView>
      </View>}
      {unfiltered && recent.length > 0 && <View className="catalog__recent">
        <Text className="catalog__eyebrow">最近浏览</Text>
        <ScrollView scrollX className="catalog__recent-scroll">
          {recent.map((s) => <View key={s.id} className="catalog__recent-item" onClick={() => pick(s)}>
            <Icon name={s.type === 'wind' ? 'wind' : 'sun'} size={15} color="#64748b" /><Text>{s.name}</Text>
          </View>)}
        </ScrollView>
      </View>}

      {(query.keyword || query.province || query.type !== 'all') && <View className="catalog__active-filters">
        {query.keyword && <Text onClick={() => setKeyword('')}>关键词：{query.keyword} ×</Text>}
        {query.province && <Text onClick={() => change({ province: '' })}>{query.province} ×</Text>}
        {query.type !== 'all' && <Text onClick={() => change({ type: 'all' })}>{query.type === 'solar' ? '光伏' : '风电'} ×</Text>}
      </View>}
      <View className="catalog__results-head">
        <Text>{result.loading && !result.items.length ? '正在查找电站' : `共 ${thousands(result.total)} 座电站`}</Text>
        <Picker mode="selector" range={['容量优先', '名称排序']} value={query.sort === 'capacity' ? 0 : 1}
          onChange={(e) => change({ sort: Number(e.detail.value) === 0 ? 'capacity' : 'name' })}>
          <View className="catalog__sort"><Text>{query.sort === 'capacity' ? '容量优先' : '名称排序'}</Text><Icon name="chevronDown" size={13} color="#64748b" /></View>
        </Picker>
      </View>
      <View className="catalog__list">
        {result.items.map((s) => {
          const cap = formatPower(s.capacity)
          return <View key={s.id} className={`catalog__item ${currentId === s.id ? 'catalog__item--current' : ''}`} hoverClass="pressed" onClick={() => pick(s)}>
            <View className="catalog__item-title"><Text className="catalog__name">{s.name}</Text><Icon name="chevronRight" size={17} color="#94a3b8" /></View>
            <Text className="catalog__address">{regionLabel(s.address)}</Text>
            <View className="catalog__meta">
              <View className={`catalog__kind catalog__kind--${s.type}`}><Icon name={s.type === 'solar' ? 'sun' : 'wind'} size={14} color={s.type === 'solar' ? '#a16207' : '#2563eb'} /><Text>{s.type === 'solar' ? '光伏' : '风电'}</Text></View>
              {currentId === s.id && <Text className="catalog__current">当前查看</Text>}
              <Text className="catalog__capacity">{cap.value.replace(/\.0$/, '')}<Text className="catalog__unit"> {cap.unit}</Text></Text>
            </View>
          </View>
        })}
      </View>
      {result.loading && <View className="catalog__loading"><Skeleton height={100} lines={3} /></View>}
      {result.error && <EmptyState icon="alertTriangle" title="电站目录加载失败" description="已加载的电站仍可查看，请重试" actionText="重新加载" onAction={() => setRetry((v) => v + 1)} />}
      {!result.loading && !result.error && !result.items.length && <EmptyState icon="search" title="没有找到匹配电站" description="试试更短的名称，或清除地区和类型筛选" actionText="清除筛选" onAction={() => { setKeyword(''); setQuery(INITIAL) }} />}
      {!result.loading && !result.error && result.items.length > 0 && <View className="catalog__footer">
        {result.hasMore ? <Button className="catalog__more" onClick={loadMore}>继续加载</Button> : <Text>已显示全部结果</Text>}
        <Text className="catalog__credit">公开数据 · Global Energy Monitor / WRI</Text>
      </View>}
    </>}
  </View>
}
