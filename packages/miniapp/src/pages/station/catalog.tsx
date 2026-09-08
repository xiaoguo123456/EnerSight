import { View, Text, Input } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useEffect, useRef, useState } from 'react'
import { formatPower } from '@enersight/core/format'
import type { CatalogPlant, StationType } from '@enersight/core/types'
import { catalogApi } from '@/api/catalog'
import { stationsApi } from '@/api/stations'
import { useStationStore } from '@/store'
import { EmptyState, ErrorState, Icon, PageHeader, SegmentedTabs, Skeleton } from '@/components'
import { useRequest } from '@/hooks/useRequest'
import './catalog.scss'

type TypeFilter = 'all' | StationType

/**
 * 从公开电站目录选择。默认按当前位置列出附近场站；输入关键词按名称 / 地区搜索。
 * 点「添加」直接复制为自己的站点，装机容量与模型参数之后可在站点里改。docs/01 §六、docs/06 §5.5
 */
export default function CatalogPicker() {
  const [keyword, setKeyword] = useState('')
  const [type, setType] = useState<TypeFilter>('all')
  const [near, setNear] = useState<string | null>(null)
  const [located, setLocated] = useState<'pending' | 'ok' | 'fail'>('pending')
  const timer = useRef<ReturnType<typeof setTimeout>>()
  const [debounced, setDebounced] = useState('')

  useEffect(() => {
    Taro.getLocation({ type: 'gcj02' })
      .then((r) => { setNear(`${r.latitude.toFixed(4)},${r.longitude.toFixed(4)}`); setLocated('ok') })
      .catch(() => setLocated('fail'))
  }, [])

  useEffect(() => {
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setDebounced(keyword.trim()), 300)
    return () => clearTimeout(timer.current)
  }, [keyword])

  const req = useRequest(
    () => catalogApi.search({
      keyword: debounced || undefined,
      near: debounced ? undefined : near ?? undefined,
      type: type === 'all' ? undefined : type,
      limit: 30,
    }),
    [debounced, near, type, located],
  )

  const setCurrent = useStationStore((s) => s.setCurrent)
  const [adding, setAdding] = useState<string | null>(null)

  // 直接落库：服务端从目录复制名称/类型/坐标/容量，重复添加返回已有的
  const pick = async (p: CatalogPlant) => {
    if (adding) return
    setAdding(p.id)
    try {
      const s = await stationsApi.addFromCatalog(p.id)
      setCurrent(s.id)
      Taro.showToast({ title: '已添加到我的站点', icon: 'success' })
      setTimeout(() => Taro.navigateBack(), 600)
    } catch {
      Taro.showToast({ title: '添加失败，请稍后再试', icon: 'none' })
    } finally {
      setAdding(null)
    }
  }

  const subtitle = debounced
    ? `搜索「${debounced}」`
    : located === 'ok' ? '按距离排序' : located === 'fail' ? '未获取定位，按装机容量排序' : '定位中…'

  return (
    <View className="catalog">
      <PageHeader title="选择公开电站" subtitle={subtitle} />

      <View className="catalog__body">
        <View className="catalog__search">
          <Icon name="search" size={15} color="#9ca3af" />
          <Input
            className="catalog__search-input"
            placeholder="搜索电站名称 / 省市 / 业主"
            placeholderClass="catalog__ph"
            value={keyword}
            onInput={(e) => setKeyword(e.detail.value)}
          />
          {keyword && (
            <View className="catalog__clear" onClick={() => setKeyword('')}>
              <Icon name="minus" size={12} color="#9ca3af" />
            </View>
          )}
        </View>

        <SegmentedTabs
          value={type}
          onChange={(v) => setType(v as TypeFilter)}
          options={[
            { value: 'all', label: '全部' },
            { value: 'solar', label: '光伏', dotColor: '#f59e0b' },
            { value: 'wind', label: '风电', dotColor: '#1677ff' },
          ]}
        />

        {req.status === 'loading' && <Skeleton height={72} lines={3} />}
        {req.status === 'error' && <ErrorState error={req.error} onRetry={req.reload} />}
        {req.status === 'success' && req.data.plants.length === 0 && (
          <EmptyState icon="search" title="没有匹配的电站" description="换个关键词，或直接输入经纬度添加" />
        )}
        {req.status === 'success' && req.data.plants.length > 0 && (
          <View className="catalog__list">
            {req.data.plants.map((p) => {
              const cap = formatPower(p.capacity)
              return (
                <View className="catalog__item" key={p.id} hoverClass="pressed" onClick={() => void pick(p)}>
                  <View className={`catalog__thumb catalog__thumb--${p.type}`}>
                    <Icon name={p.type === 'solar' ? 'sun' : 'wind'} size={16} color="#fff" fill={p.type === 'solar'} />
                  </View>
                  <View className="catalog__text">
                    <Text className="catalog__name">{p.name}</Text>
                    <Text className="catalog__meta">
                      {cap.value} {cap.unit}
                      {p.address ? ` · ${p.address}` : ''}
                      {p.distance_km != null ? ` · ${p.distance_km} km` : ''}
                      {p.commissioning_year ? ` · ${p.commissioning_year} 年` : ''}
                    </Text>
                  </View>
                  <Text className="catalog__add">{adding === p.id ? '添加中' : '添加'}</Text>
                </View>
              )
            })}
            <Text className="catalog__credit">
              数据：{req.data.plants.some((p) => p.source === 'gem') ? 'Global Energy Monitor、' : ''}WRI Global Power Plant Database（CC BY 4.0），共收录 {req.data.total} 座
            </Text>
          </View>
        )}
      </View>
    </View>
  )
}
