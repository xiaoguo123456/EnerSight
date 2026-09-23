import Taro from '@tarojs/taro'
import { useCallback, useEffect, useRef, useState } from 'react'
import { mapRegionPhase } from '@enersight/core/map'

type Point = { latitude: number; longitude: number }
type Camera = Point & { scale: number }
type Bounds = { sw: Point; ne: Point }
type Station = Point & { id: string }
const INITIAL: Camera = { latitude: 31.3, longitude: 120.62, scale: 9 }
const validPoint = (p: any): p is Point => Number.isFinite(p?.latitude) && Math.abs(p.latitude) <= 90
  && Number.isFinite(p?.longitude) && Math.abs(p.longitude) <= 180

/** 场站只负责首次定位；手势结束后同步真实视野，异步回调不能覆盖后来的操作。 */
export function useMapViewport(mapId: string, station?: Station, selectedId?: string | null) {
  const [camera, setCamera] = useState<Camera>(INITIAL)
  const revision = useRef(0)
  const fitting = useRef(false)
  const centeredStation = useRef<string>()
  const save = useCallback((next: Camera) => {
    if (!validPoint(next) || !Number.isFinite(next.scale)) return
    setCamera(previous => Math.abs(previous.latitude - next.latitude) < 1e-7
      && Math.abs(previous.longitude - next.longitude) < 1e-7
      && Math.abs(previous.scale - next.scale) < 1e-6 ? previous : next)
  }, [])
  const moveTo = useCallback((point: Point, scale?: number) => {
    if (!validPoint(point)) return
    ++revision.current
    setCamera(previous => ({ latitude: point.latitude, longitude: point.longitude, scale: scale ?? previous.scale }))
  }, [])
  useEffect(() => {
    // 切站时 useRequest 可能还短暂保留上一站响应，不能用它触发定位。
    if (!station || (selectedId && station.id !== selectedId) || centeredStation.current === station.id) return
    centeredStation.current = station.id
    moveTo(station)
  }, [station?.id, station?.latitude, station?.longitude, selectedId, moveTo])
  useEffect(() => () => { ++revision.current }, [])

  const read = useCallback(async (detail?: any): Promise<Camera> => {
    const ctx = Taro.createMapContext(mapId)
    const [point, scale] = await Promise.all([
      validPoint(detail?.centerLocation) ? Promise.resolve(detail.centerLocation as Point)
        : new Promise<Point>((resolve, reject) => ctx.getCenterLocation({ success: resolve, fail: reject })),
      Number.isFinite(detail?.scale) ? Promise.resolve(detail.scale as number)
        : new Promise<number>((resolve, reject) => ctx.getScale({ success: r => resolve(r.scale), fail: reject })),
    ])
    return { latitude: point.latitude, longitude: point.longitude, scale }
  }, [mapId])

  const regionChanged = useCallback(async (event: any) => {
    if (fitting.current) return
    const phase = mapRegionPhase(event)
    const detail = event?.detail ?? event
    const cause = detail?.causedBy ?? event?.causedBy
    if (cause === 'update') return
    if (phase === 'begin') { ++revision.current; return }
    if (phase !== 'end') return
    const mine = ++revision.current
    try {
      const next = await read(detail)
      if (mine === revision.current) save(next)
    } catch { /* 地图尚未就绪，不用场站坐标覆盖当前视野。 */ }
  }, [read, save])
  const zoom = useCallback(async (delta: number) => {
    const mine = ++revision.current
    try {
      const current = await read()
      if (mine === revision.current) save({ ...current, scale: Math.min(18, Math.max(3, current.scale + delta)) })
    } catch { /* 读取失败时保持视野，避免只改 scale 导致中心回跳。 */ }
  }, [read, save])
  const fitBounds = useCallback(async (bounds: Bounds, padding: number[]) => {
    const mine = ++revision.current
    fitting.current = true
    try {
      await new Promise<void>((resolve, reject) => {
        const result = Taro.createMapContext(mapId).includePoints({
          points: [bounds.sw, bounds.ne], padding,
          success: () => resolve(), fail: reject,
        })
        result?.then?.(() => resolve(), reject)
      })
      if (mine !== revision.current) return
      // 程序改变视野会触发 causedBy=update；主动读取并同步受控属性。
      await new Promise(resolve => setTimeout(resolve, 100))
      const actual = await read()
      if (mine === revision.current) save(actual)
    } finally {
      fitting.current = false
    }
  }, [mapId, read, save])
  return { camera, moveTo, fitBounds, regionChanged, zoom }
}
