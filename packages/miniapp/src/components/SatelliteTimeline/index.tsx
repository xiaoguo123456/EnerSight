import { View, Text, Slider } from '@tarojs/components'
import Taro, { useDidHide, useDidShow } from '@tarojs/taro'
import { useEffect, useRef, useState } from 'react'
import type { SatelliteCloudResponse, SatelliteHistoryResponse } from '@enersight/core/types'
import { formatBeijingTime } from '@enersight/core/format'
import { imageryApi as api } from '@/api'
import { useRequest } from '@/hooks/useRequest'
import { SatelliteCloudCard } from '../SatelliteCloudCard'
import './index.scss'

/** 三小时真实观测序列；按需下载当前/下一帧，缺失帧不复制伪造。 */
export function SatelliteTimeline({ stationId }: { stationId: string }) {
  const manifest = useRequest(() => api.get<SatelliteHistoryResponse>('/v1/satellite/cloud/history'), [])
  const times = manifest.data?.times ?? []
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(true)
  const [visible, setVisible] = useState(true)
  const [speed, setSpeed] = useState(1)
  const [loaded, setLoaded] = useState('')
  const [failed, setFailed] = useState(false)
  const cache = useRef(new Map<string, Promise<SatelliteCloudResponse>>())
  const when = times[index]
  const getFrame = (at: string) => {
    const hit = cache.current.get(at)
    if (hit) return hit
    const task = api.get<SatelliteCloudResponse>('/v1/satellite/cloud', { station_id: stationId, at }).then(async (data) => {
      // 图片缓存到本机后再切换，避免播放期间反复出现空白。
      const image = await Taro.getImageInfo({ src: data.image.url })
      return { ...data, image: { ...data.image, url: image.path } }
    }).catch((e) => { cache.current.delete(at); throw e })
    if (cache.current.size > 40) cache.current.delete(cache.current.keys().next().value!)
    cache.current.set(at, task)
    return task
  }
  const frame = useRequest(() => when ? getFrame(when) : Promise.resolve(null), [stationId, when])
  useDidHide(() => setVisible(false)); useDidShow(() => setVisible(true))
  useEffect(() => { setIndex(0) }, [manifest.data?.end_at])
  useEffect(() => { setLoaded(''); setFailed(false) }, [when])
  useEffect(() => {
    if (frame.status !== 'success' || !when) return
    const next = times[index + 1]
    if (next) void getFrame(next).catch(() => undefined)
  }, [frame.data, when])
  useEffect(() => {
    if (!playing || !visible || loaded !== when || failed || times.length < 2) return
    const timer = setTimeout(() => setIndex((i) => (i + 1) % times.length), 1000 / speed)
    return () => clearTimeout(timer)
  }, [playing, visible, loaded, when, speed, failed, times.length])
  const retry = () => { if (when) cache.current.delete(when); setFailed(false); void frame.reload() }
  return <View className="sat-timeline">
    <View className="sat-timeline__head"><Text>近 3 小时云图</Text><Text className="sat-timeline__muted">红外连续观测</Text></View>
    {manifest.status === 'error' ? <View onClick={manifest.reload}>时间轴加载失败 · 点击重试</View> : <>
      {frame.status === 'loading' || manifest.status === 'loading' ? <View className="sat-timeline__loading">正在加载历史云图…</View> : <SatelliteCloudCard satellite={frame.data ?? null} onRetry={retry}
        onLoaded={() => { setLoaded(when ?? ''); setFailed(false) }} onImageError={() => setFailed(true)} />}
      <Text className="sat-timeline__status">{manifest.status === 'loading' ? '正在获取观测时刻…' : frame.status === 'error' || failed ? '此帧加载失败，可重试或切换其他时刻' : frame.status === 'loading' ? `正在加载第 ${index + 1} 帧…` : `第 ${index + 1} / ${times.length} 帧 · ${when ? formatBeijingTime(when) : '暂无可用帧'}（北京时间）`}</Text>
      <Slider min={0} max={Math.max(1, times.length - 1)} step={1} value={index} disabled={times.length < 2}
        activeColor="#1677ff" blockSize={18} onChanging={() => setPlaying(false)} onChange={(e) => { setPlaying(false); setIndex(Math.max(0, Math.min(times.length - 1, e.detail.value))) }} />
      <View className="sat-timeline__range"><Text>{times[0] ? formatBeijingTime(times[0]) : '—'}</Text><Text>{times.length ? formatBeijingTime(times[times.length - 1]) : '—'}</Text></View>
      <View className="sat-timeline__controls">
        <View onClick={() => { setPlaying(false); setIndex((i) => Math.max(0, i - 1)) }}>上一帧</View>
        <View onClick={() => setPlaying((p) => !p)}>{playing ? '暂停' : '播放'}</View>
        <View onClick={() => { setPlaying(false); setIndex((i) => Math.max(0, Math.min(times.length - 1, i + 1))) }}>下一帧</View>
        <View onClick={() => setSpeed((s) => s === 1 ? 2 : 1)}>{speed}×</View>
      </View>
      <Text className="sat-timeline__muted">时间轴截至最新可用观测；仅播放真实帧，空缺时刻不补造。</Text>
    </>}
  </View>
}
