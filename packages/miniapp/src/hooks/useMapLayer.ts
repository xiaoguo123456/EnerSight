import Taro from '@tarojs/taro'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { LayerResponse, LayerType } from '@enersight/core/types'
import { clientLog } from '@/api/debug'
import { layersApi } from '@/api/layers'

/**
 * 把服务端渲染的图层贴到 map 上。docs/05 §6.2
 *
 * - 客户端不做坐标转换：请求带 coord=gcj02，服务端返回的 bounds 直接贴
 * - 客户端不用自己请求的 bbox：用服务端对齐到量化块后的 bounds
 * - 图例随图层由接口下发，不硬编码
 *
 * ⚠️ 开发者工具的模拟器不渲染 ground-overlay（调用后既不回调也不拉图），
 * 贴图效果只能在真机预览/调试里看。见 docs/05 §6.7
 */
export function useMapLayer(mapId: string, layer: LayerType | null, active: boolean) {
  const [legend, setLegend] = useState<LayerResponse['legend'] | null>(null)
  const [observedAt, setObservedAt] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)
  const overlayIds = useRef<number[]>([])
  const seq = useRef(0)

  const clear = useCallback(() => {
    const ctx = Taro.createMapContext(mapId)
    for (const id of overlayIds.current) {
      ctx.removeGroundOverlay({ id }).catch?.(() => undefined)
    }
    overlayIds.current = []
  }, [mapId])

  const refresh = useCallback(async () => {
    if (!layer || !active) { ++seq.current; clear(); setLegend(null); setObservedAt(null); setLoading(false); setError(false); return }
    const ctx = Taro.createMapContext(mapId)
    const mine = ++seq.current
    setLoading(true); setError(false); setLegend(null); setObservedAt(null)
    let region: { southwest: { latitude: number; longitude: number }; northeast: { latitude: number; longitude: number } }
    let scale = 8
    try {
      region = await new Promise((resolve, reject) => ctx.getRegion({ success: resolve, fail: reject }))
      scale = await new Promise<number>((resolve) =>
        ctx.getScale({ success: (r) => resolve(r.scale), fail: () => resolve(8) }))
    } catch (e) {
      if (mine === seq.current) { setLoading(false); setError(true) }
      clientLog('map.getRegion', String((e as any)?.errMsg ?? e))
      return
    }
    let res: LayerResponse
    try {
      res = await layersApi.get(layer, {
        west: region.southwest.longitude, south: region.southwest.latitude,
        east: region.northeast.longitude, north: region.northeast.latitude,
      }, Math.round(scale))
    } catch (e) {
      if (mine === seq.current) { setLoading(false); setError(true); clear(); setLegend(null); setObservedAt(null) }
      clientLog('map.layersApi', String((e as any)?.message ?? e))
      return
    }
    if (mine !== seq.current) return

    clear()
    const frame = res.frames[0]
    if (!frame) { setLoading(false); setError(true); return }
    let timeout: ReturnType<typeof setTimeout> | undefined
    try {
      const drawing = frame.images.map((img, i) => {
        const id = 1000 + i
        overlayIds.current.push(id)
        return new Promise<void>((resolve, reject) => ctx.addGroundOverlay({
          id, src: img.url,
          bounds: { southwest: { latitude: img.bounds.sw.latitude, longitude: img.bounds.sw.longitude }, northeast: { latitude: img.bounds.ne.latitude, longitude: img.bounds.ne.longitude } },
          opacity: 0.75, zIndex: 1, success: () => resolve(), fail: reject,
        } as any))
      })
      if (!drawing.length) throw new Error('没有可绘制的图层图片')
      await Promise.race([
        Promise.all(drawing),
        new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error('图层绘制超时')), 10_000) }),
      ])
      if (mine !== seq.current) return
      setLegend(res.legend); setObservedAt(res.observed_at)
    } catch (e) {
      if (mine !== seq.current) return
      clear(); setLegend(null); setObservedAt(null); setError(true)
      clientLog('map.overlay', String((e as any)?.errMsg ?? e))
    } finally {
      if (timeout) clearTimeout(timeout)
      if (mine === seq.current) setLoading(false)
    }

  }, [mapId, layer, active, clear])

  useEffect(() => { void refresh() }, [refresh])

  return { legend, observedAt, refresh, loading, error }
}
