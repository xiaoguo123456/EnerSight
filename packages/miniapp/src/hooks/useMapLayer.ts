import Taro from '@tarojs/taro'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { LayerResponse, LayerType } from '@enersight/core/types'
import { clientLog } from '@/api/debug'
import { layersApi } from '@/api/layers'
import { projectLayerImage } from '@enersight/core/map'

type Region = { southwest: { latitude: number; longitude: number }; northeast: { latitude: number; longitude: number } }
type Preview = { id: number; images: { url: string; style: Record<string, string> }[] }

/** 原生端贴图；模拟器按地图视野投影预览图片，加载完成才显示图例。 */
export function useMapLayer(mapId: string, layer: LayerType | null, active: boolean) {
  const [legend, setLegend] = useState<LayerResponse['legend'] | null>(null)
  const [observedAt, setObservedAt] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)
  const [errorMessage, setErrorMessage] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [isPreview] = useState(() => Taro.getDeviceInfo().platform === 'devtools')
  const overlayIds = useRef<number[]>([])
  const nextId = useRef(1000)
  const seq = useRef(0)
  const pending = useRef<{ id: number; remaining: Set<number>; response: LayerResponse } | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout>>()

  const clear = useCallback(() => {
    if (timer.current) clearTimeout(timer.current)
    pending.current = null
    setPreview(null)
    const ctx = Taro.createMapContext(mapId)
    for (const id of overlayIds.current) {
      try { void Promise.resolve(ctx.removeGroundOverlay({ id })).catch(() => undefined) } catch { /* 已移除 */ }
    }
    overlayIds.current = []
    setLegend(null); setObservedAt(null)
  }, [mapId])
  const invalidate = useCallback(() => { ++seq.current; clear(); setLoading(false) }, [clear])
  const fail = useCallback((id: number, message: string) => {
    if (id !== seq.current) return
    ++seq.current
    clear(); setLoading(false); setError(true); setErrorMessage(message)
    clientLog('map.overlay', message)
  }, [clear])
  const finish = useCallback((id: number, response: LayerResponse) => {
    if (id !== seq.current) return
    if (timer.current) clearTimeout(timer.current)
    pending.current = null
    setLegend(response.legend); setObservedAt(response.observed_at); setLoading(false)
  }, [])
  const imageLoaded = useCallback((id: number, index: number) => {
    const p = pending.current
    if (!p || p.id !== id || id !== seq.current) return
    p.remaining.delete(index)
    if (!p.remaining.size) finish(id, p.response)
  }, [finish])

  const refresh = useCallback(async () => {
    const mine = ++seq.current
    clear(); setError(false); setErrorMessage('')
    if (!layer || !active) { setLoading(false); return }
    setLoading(true)
    timer.current = setTimeout(() => fail(mine, '图层加载超时，请重试'), 45_000)
    const ctx = Taro.createMapContext(mapId)
    try {
      const region = await new Promise<Region>((resolve, reject) => ctx.getRegion({ success: resolve, fail: reject }))
      if (mine !== seq.current) return
      const response = await layersApi.get(layer, {
        west: region.southwest.longitude, south: region.southwest.latitude,
        east: region.northeast.longitude, north: region.northeast.latitude,
      }, 8)
      if (mine !== seq.current) return
      const images = response.frames[0]?.images
      if (!images?.length) throw new Error('当前视野暂无图层数据')
      if (isPreview) {
        const projected = images.map((img) => ({ url: img.url, style: projectLayerImage(region, img.bounds) }))
        pending.current = { id: mine, remaining: new Set(images.map((_, i) => i)), response }
        setPreview({ id: mine, images: projected })
      } else {
        await Promise.all(images.map((img) => {
          const id = nextId.current++
          overlayIds.current.push(id)
          return new Promise<void>((resolve, reject) => {
            const result = ctx.addGroundOverlay({
            id, src: img.url, bounds: { southwest: img.bounds.sw, northeast: img.bounds.ne },
            opacity: 0.65, zIndex: 1,
            success: () => resolve(), fail: reject,
            })
            result?.then?.(() => resolve(), reject)
          }).then(() => {
            if (mine !== seq.current) void Promise.resolve(ctx.removeGroundOverlay({ id })).catch(() => undefined)
          })
        }))
        finish(mine, response)
      }
    } catch (e) {
      fail(mine, String((e as any)?.errMsg ?? (e as Error)?.message ?? '图层加载失败'))
    }
  }, [mapId, layer, active, isPreview, clear, fail, finish])

  useEffect(() => { void refresh(); return invalidate }, [refresh, invalidate])
  return { legend, observedAt, refresh, loading, error, errorMessage, preview, isPreview, imageLoaded, imageError: fail, invalidate }
}
