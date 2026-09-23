import Taro from '@tarojs/taro'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { LayerResponse, LayerType } from '@enersight/core/types'
import { useWeatherModel } from '@/store/weatherModel'
import { clientLog } from '@/api/debug'
import { layersApi } from '@/api/layers'
import { sameMapRegion, projectLayerImage } from '@enersight/core/map'

type Region = { southwest: { latitude: number; longitude: number }; northeast: { latitude: number; longitude: number } }
type Bounds = { sw: Region['southwest']; ne: Region['northeast'] }
type Preview = { id: number; images: { url: string; style: Record<string, string> }[] }

/** 原生端贴图；模拟器按地图视野投影预览图片，加载完成才显示图例。 */
export function useMapLayer(mapId: string, layer: LayerType | null, active: boolean, cloudMode: 'auto' | 'satellite' = 'auto', satelliteScope: Bounds | null = null) {
  const model = useWeatherModel(s => s.model)
  const [legend, setLegend] = useState<LayerResponse['legend'] | null>(null)
  const [coverage, setCoverage] = useState('')
  const [attribution, setAttribution] = useState('')
  const [modelName, setModelName] = useState('')
  const [sourceLabel, setSourceLabel] = useState('')
  const [observedAt, setObservedAt] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)
  const [errorMessage, setErrorMessage] = useState('')
  const [wind, setWind] = useState<{ vectors: NonNullable<LayerResponse["wind_vectors"]>; region: Region } | null>(null)
  const [samples, setSamples] = useState<{ left: string; top: string; text: string }[]>([])
  const regionRef = useRef<Region>()
  const requestedRegion = useRef<Region>()
  const regionCheck = useRef(0)
  const [stale, setStale] = useState(false)
  const debounce = useRef<ReturnType<typeof setTimeout>>()
  const [preview, setPreview] = useState<Preview | null>(null)
  const [isPreview] = useState(() => Taro.getDeviceInfo().platform === 'devtools')
  const overlayIds = useRef<number[]>([])
  const stagedIds = useRef<number[]>([])
  const nextId = useRef(1000)
  const seq = useRef(0)
  const pending = useRef<{ id: number; remaining: Set<number>; response: LayerResponse } | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout>>()

  const discardStaged = useCallback(() => {
    const ctx = Taro.createMapContext(mapId)
    for (const id of stagedIds.current) {
      try { void Promise.resolve(ctx.removeGroundOverlay({ id })).catch(() => undefined) } catch { /* 已移除 */ }
    }
    stagedIds.current = []
  }, [mapId])
  const clear = useCallback(() => {
    discardStaged()
    if (timer.current) clearTimeout(timer.current)
    pending.current = null
    setPreview(null); setWind(null); setSamples([])
    const ctx = Taro.createMapContext(mapId)
    for (const id of overlayIds.current) {
      try { void Promise.resolve(ctx.removeGroundOverlay({ id })).catch(() => undefined) } catch { /* 已移除 */ }
    }
    overlayIds.current = []
    setLegend(null); setObservedAt(null); setSourceLabel(''); setAttribution(''); setModelName(''); setCoverage('')
  }, [mapId, discardStaged])
  const invalidate = useCallback(() => { if (debounce.current) clearTimeout(debounce.current); ++seq.current; ++regionCheck.current; requestedRegion.current = undefined; clear(); setLoading(false) }, [clear])
  const fail = useCallback((id: number, message: string) => {
    if (id !== seq.current) return
    ++seq.current
    if (timer.current) clearTimeout(timer.current)
    discardStaged()
    if (isPreview) clear()
    setLoading(false); setError(true); setErrorMessage(message)
    clientLog('map.overlay', message)
  }, [clear, discardStaged, isPreview])
  const finish = useCallback((id: number, response: LayerResponse) => {
    if (id !== seq.current) return
    if (timer.current) clearTimeout(timer.current)
    pending.current = null
    setStale(!!response.stale)
    setCoverage(response.coverage || '')
    setAttribution(response.source || '')
    setModelName(response.model || '')
    setSourceLabel(response.model ? `${response.model} · ${response.resolution_km} km 预报` : response.source ? '卫星实况' : '')
    const region = regionRef.current
    if (region && response.samples?.length) {
      const merc = (lat: number) => Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360))
      const north = merc(region.northeast.latitude), south = merc(region.southwest.latitude)
      const selected: { x: number; y: number; text: string }[] = []
      for (const p of response.samples) {
        const x = (p.longitude - region.southwest.longitude) / (region.northeast.longitude - region.southwest.longitude) * 100
        const y = (north - merc(p.latitude)) / (north - south) * 100
        if (x < 8 || x > 88 || y < 20 || y > 92 || selected.some(q => Math.abs(q.x-x)<23 && Math.abs(q.y-y)<12)) continue
        selected.push({ x, y, text: `${Math.round(p.value)}${response.unit === '℃' ? '℃' : ' W/m²'}` })
      }
      setSamples(selected.map(p => ({ left: `${p.x}%`, top: `${p.y}%`, text: p.text })))
    }
    if (response.wind_vectors?.length && regionRef.current) setWind({ vectors: response.wind_vectors, region: regionRef.current })
    setLegend(response.legend.colors.length ? response.legend : null); setObservedAt(response.observed_at); setLoading(false)
  }, [])
  const imageLoaded = useCallback((id: number, index: number) => {
    const p = pending.current
    if (!p || p.id !== id || id !== seq.current) return
    p.remaining.delete(index)
    if (!p.remaining.size) finish(id, p.response)
  }, [finish])

  const load = useCallback(async () => {
    const mine = ++seq.current
    if (timer.current) clearTimeout(timer.current)
    discardStaged()
    if (isPreview || !layer || !active) clear()
    setError(false); setErrorMessage('')
    if (!layer || !active) { setLoading(false); return }
    setLoading(true)
    timer.current = setTimeout(() => fail(mine, '图层加载超时，请重试'), 45_000)
    const ctx = Taro.createMapContext(mapId)
    try {
      const region = await new Promise<Region>((resolve, reject) => ctx.getRegion({ success: resolve, fail: reject }))
      if (mine !== seq.current) return
      const requestRegion = layer === 'cloud' && cloudMode === 'satellite' && satelliteScope
        ? { southwest: satelliteScope.sw, northeast: satelliteScope.ne } : region
      if (layer === 'cloud' && cloudMode === 'auto' && (requestRegion.northeast.longitude - requestRegion.southwest.longitude > 23.5 || requestRegion.northeast.latitude - requestRegion.southwest.latitude > 23.5)) throw new Error('当前视野过大，请放大地图查看气象分布')
      if (layer === 'cloud' && cloudMode === 'satellite' && (requestRegion.northeast.longitude - requestRegion.southwest.longitude > 89 || requestRegion.northeast.latitude - requestRegion.southwest.latitude > 69)) throw new Error('卫星视野过大，请缩小地区范围')
      requestedRegion.current = region
      regionRef.current = region
      const response = await layersApi.get(layer, {
        west: requestRegion.southwest.longitude, south: requestRegion.southwest.latitude,
        east: requestRegion.northeast.longitude, north: requestRegion.northeast.latitude,
      }, 8, cloudMode)
      if (mine !== seq.current) return
      const images = response.frames[0]?.images
      if (!images?.length) throw new Error('当前视野暂无图层数据')
      if (isPreview) {
        const projected = images.map((img) => ({ url: img.url, style: projectLayerImage(region, img.bounds) }))
        pending.current = { id: mine, remaining: new Set(images.map((_, i) => i)), response }
        setPreview({ id: mine, images: projected })
      } else {
        // 真机覆盖层本身会跟随地图。新一批全部就绪后再移除旧批，不能先清空。
        const previous = overlayIds.current
        const next: number[] = []
        stagedIds.current = next
        await Promise.all(images.map((img) => {
          const id = nextId.current++
          next.push(id)
          return new Promise<void>((resolve, reject) => {
            const result = ctx.addGroundOverlay({
            id, src: img.url, bounds: { southwest: img.bounds.sw, northeast: img.bounds.ne },
            opacity: layer === 'cloud' ? (cloudMode === 'satellite' ? 0.85 : 0.65) : 1, zIndex: 1,
            success: () => resolve(), fail: reject,
            })
            result?.then?.(() => resolve(), reject)
          }).then(() => {
            if (mine !== seq.current) void Promise.resolve(ctx.removeGroundOverlay({ id })).catch(() => undefined)
          })
        }))
        if (mine !== seq.current) return
        overlayIds.current = next
        stagedIds.current = []
        for (const id of previous) {
          try { void Promise.resolve(ctx.removeGroundOverlay({ id })).catch(() => undefined) } catch { /* 已移除 */ }
        }
        finish(mine, response)
      }
    } catch (e) {
      fail(mine, String((e as any)?.errMsg ?? (e as Error)?.message ?? '图层加载失败'))
    }
  }, [mapId, layer, active, isPreview, clear, discardStaged, fail, finish, model, cloudMode, satelliteScope?.sw.latitude, satelliteScope?.sw.longitude, satelliteScope?.ne.latitude, satelliteScope?.ne.longitude])

  const refresh = useCallback(() => { if (debounce.current) clearTimeout(debounce.current); debounce.current = setTimeout(() => void load(), 600) }, [load])
  // 原生贴图更新也会发出 end；比较实际视野，既兼容无 causedBy 的模拟器事件，也避免循环请求。
  const viewportChanged = useCallback(async () => {
    if (!active || !layer) return
    const check = ++regionCheck.current
    try {
      const region = await new Promise<Region>((resolve, reject) => Taro.createMapContext(mapId).getRegion({ success: resolve, fail: reject }))
      if (check !== regionCheck.current || sameMapRegion(requestedRegion.current, region)) return
      // 屏幕预览需撤下；真机保留地理覆盖层，只取消过期请求及屏幕采样。
      if (isPreview) invalidate()
      else {
        ++seq.current
        if (timer.current) clearTimeout(timer.current)
        discardStaged()
        setWind(null); setSamples([])
      }
      requestedRegion.current = region
      refresh()
    } catch { /* 地图尚未就绪，首次加载仍由 refresh 负责 */ }
  }, [active, layer, mapId, invalidate, refresh, isPreview, discardStaged])
  // 模拟器缩放事件存在缺失；轻量读取视野兜底，视野不变时不发网络请求。
  useEffect(() => {
    if (!isPreview || !active || !layer) return
    const watch = setInterval(() => void viewportChanged(), 400)
    return () => clearInterval(watch)
  }, [isPreview, active, layer, viewportChanged])
  useEffect(() => { void refresh(); return invalidate }, [refresh, invalidate])
  return { viewportChanged, coverage, modelName, attribution, sourceLabel, samples, wind, stale, legend, observedAt, refresh, loading, error, errorMessage, preview, isPreview, imageLoaded, imageError: fail, invalidate }
}
