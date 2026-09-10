interface Point { latitude: number; longitude: number }
interface Region { southwest: Point; northeast: Point }
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
