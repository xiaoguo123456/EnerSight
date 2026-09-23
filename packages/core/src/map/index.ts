interface Point { latitude: number; longitude: number }
interface Region { southwest: Point; northeast: Point }

/** 地图中心和整数缩放：按墨卡托投影把行政范围放进扣除浮层后的可见区域。 */
export function fitMapBounds(
  bounds: { sw: Point; ne: Point },
  viewport: { width: number; height: number },
  padding: [number, number, number, number],
): Point & { scale: number } {
  const [top, right, bottom, left] = padding
  const availableWidth = viewport.width - left - right
  const availableHeight = viewport.height - top - bottom
  const { sw, ne } = bounds
  if (!Number.isFinite(availableWidth) || !Number.isFinite(availableHeight)
    || availableWidth <= 0 || availableHeight <= 0
    || !Number.isFinite(sw.latitude) || !Number.isFinite(sw.longitude)
    || !Number.isFinite(ne.latitude) || !Number.isFinite(ne.longitude)
    || sw.latitude >= ne.latitude || sw.longitude >= ne.longitude) {
    throw new Error('省域范围或地图尺寸无效')
  }
  const mercatorY = (lat: number) => (1 - Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)) / Math.PI) / 2
  const west = (sw.longitude + 180) / 360
  const east = (ne.longitude + 180) / 360
  const north = mercatorY(ne.latitude)
  const south = mercatorY(sw.latitude)
  const scale = Math.max(3, Math.min(18, Math.floor(Math.min(
    Math.log2(availableWidth / (256 * (east - west))),
    Math.log2(availableHeight / (256 * (south - north))),
  ))))
  const worldPixels = 256 * 2 ** scale
  const centerX = (west + east) / 2 - (left - right) / (2 * worldPixels)
  const centerY = (north + south) / 2 - (top - bottom) / (2 * worldPixels)
  return {
    longitude: centerX * 360 - 180,
    latitude: Math.atan(Math.sinh(Math.PI * (1 - 2 * centerY))) * 180 / Math.PI,
    scale,
  }
}

/** 北向、无倾斜地图的墨卡托视野坐标；超出视野部分由容器裁切。 */
export function projectLayerImage(region: Region, bounds: { sw: Point; ne: Point }): Record<string, string> {
  const y = (lat: number) => Math.log(Math.tan(Math.PI / 4 + Math.max(-85, Math.min(85, lat)) * Math.PI / 360))
  const width = region.northeast.longitude - region.southwest.longitude
  const north = y(region.northeast.latitude)
  const height = north - y(region.southwest.latitude)
  if (!(width > 0 && height > 0)) throw new Error('地图视野无效，请缩放后重试')
  return {
    left: `${(bounds.sw.longitude - region.southwest.longitude) / width * 100}%`,
    top: `${(north - y(bounds.ne.latitude)) / height * 100}%`,
    width: `${(bounds.ne.longitude - bounds.sw.longitude) / width * 100}%`,
    height: `${(y(bounds.ne.latitude) - y(bounds.sw.latitude)) / height * 100}%`,
  }
}

/** 只维护最近四点，避免密集风场在每个像素格创建并排序整份数组。 */
export function interpolateWind(points: { x: number; y: number; u: number; v: number }[], x: number, y: number) {
  const nearest: { d: number; u: number; v: number }[] = []
  for (const p of points) {
    if (!Number.isFinite(p.x) || !Number.isFinite(p.y) || !Number.isFinite(p.u) || !Number.isFinite(p.v)) continue
    const d = (p.x - x) ** 2 + (p.y - y) ** 2
    if (d === 0) return { u: p.u, v: p.v }
    if (nearest.length === 4 && d >= nearest[3]!.d) continue
    let i = 0
    while (i < nearest.length && nearest[i]!.d <= d) i++
    nearest.splice(i, 0, { d, u: p.u, v: p.v })
    if (nearest.length > 4) nearest.pop()
  }
  let u = 0, v = 0, weight = 0
  for (const p of nearest) {
    const a = 1 / Math.max(p.d, 1)
    weight += a; u += p.u * a; v += p.v * a
  }
  return weight ? { u: u / weight, v: v / weight } : { u: 0, v: 0 }
}

/** 忽略浮点抖动，识别真实视野变化，供原生事件和程序缩放共用。 */
export function mapRegionKey(region: Region): string {
  return [region.southwest.latitude, region.southwest.longitude, region.northeast.latitude, region.northeast.longitude]
    .map(v => v.toFixed(6)).join(',')
}

/** 开发工具可能把阶段放在顶层，而真机通常放在 detail。 */
export function mapRegionPhase(event: { type?: string; detail?: { type?: string } }): string | undefined {
  return event.detail?.type === 'begin' || event.detail?.type === 'end' ? event.detail.type : event.type
}

/** 原生地图读取范围有轻微抖动；不足视野千分之一的变化不重载瓦片。 */
export function sameMapRegion(a: Region | undefined, b: Region): boolean {
  if (!a) return false
  const latTolerance = Math.max(1e-6, Math.abs(a.northeast.latitude - a.southwest.latitude) * .001)
  const lonTolerance = Math.max(1e-6, Math.abs(a.northeast.longitude - a.southwest.longitude) * .001)
  return Math.abs(a.southwest.latitude - b.southwest.latitude) < latTolerance
    && Math.abs(a.northeast.latitude - b.northeast.latitude) < latTolerance
    && Math.abs(a.southwest.longitude - b.southwest.longitude) < lonTolerance
    && Math.abs(a.northeast.longitude - b.northeast.longitude) < lonTolerance
}
