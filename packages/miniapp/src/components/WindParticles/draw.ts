import { interpolateWind } from '@enersight/core/map'
import type { WindVector } from '@enersight/core/types'

export type Region = { southwest: { latitude: number; longitude: number }; northeast: { latitude: number; longitude: number } }
export type Mask = { x: number; y: number; w: number; h: number }

/** 预计算风场；每帧清屏画带箭头的短轨迹，避免低风速时只剩难以辨认的小点。 */
export function createWindAnimation(vectors: WindVector[], region: Region, w: number, h: number, masks: Mask[], random = Math.random) {
  const merc = (lat: number) => Math.log(Math.tan(Math.PI / 4 + Math.max(-85, Math.min(85, lat)) * Math.PI / 360))
  const north = merc(region.northeast.latitude), south = merc(region.southwest.latitude)
  const span = region.northeast.longitude - region.southwest.longitude
  const points = vectors.filter(p => [p.longitude, p.latitude, p.u, p.v].every(Number.isFinite)).map(p => ({
    x: (p.longitude - region.southwest.longitude) / span * w,
    y: (north - merc(p.latitude)) / (north - south) * h, u: p.u, v: p.v,
  }))
  const left = Math.max(0, Math.min(...points.map(p => p.x))), right = Math.min(w, Math.max(...points.map(p => p.x)))
  const top = Math.max(0, Math.min(...points.map(p => p.y))), bottom = Math.min(h, Math.max(...points.map(p => p.y)))
  const cols = 24, rows = 32
  const field = Array.from({ length: cols * rows }, (_, i) => {
    const x = i % cols / (cols - 1) * w, y = Math.floor(i / cols) / (rows - 1) * h
    return x < left || x > right || y < top || y > bottom || span <= 0 || north <= south ? { u: 0, v: 0 } : interpolateWind(points, x, y)
  })
  const reset = (p: { x: number; y: number; age: number }) => {
    p.x = left + random() * Math.max(0, right - left); p.y = top + random() * Math.max(0, bottom - top); p.age = random() * 3
  }
  const particles = Array.from({ length: Math.min(140, Math.round(w * h / 2400)) }, () => {
    const p = { x: 0, y: 0, age: 0 }; reset(p); return p
  })
  return (ctx: CanvasRenderingContext2D, elapsedMs = 40) => {
    ctx.clearRect(0, 0, w, h)
    if (!points.length || right <= left || bottom <= top) return
    ctx.lineWidth = 1.5; ctx.lineCap = 'round'; ctx.lineJoin = 'round'
    const dt = Math.min(80, Math.max(0, elapsedMs)) / 1000
    for (const p of particles) {
      p.age += dt
      if (p.age > 5 || p.x < left || p.x >= right || p.y < top || p.y >= bottom) reset(p)
      const f = field[Math.min(rows - 1, Math.max(0, Math.floor(p.y / h * rows))) * cols + Math.min(cols - 1, Math.max(0, Math.floor(p.x / w * cols)))]
      if (!f) continue
      const speed = Math.hypot(f.u, f.v)
      if (speed < .2) continue
      const ux = f.u / speed, uy = -f.v / speed
      p.x += f.u * 8 * dt; p.y -= f.v * 8 * dt
      const length = Math.min(13, 7 + speed * .6)
      ctx.strokeStyle = 'rgba(18,64,120,.82)'
      ctx.beginPath(); ctx.moveTo(p.x - ux * length, p.y - uy * length); ctx.lineTo(p.x, p.y)
      ctx.moveTo(p.x - ux * 3.5 - uy * 2.6, p.y - uy * 3.5 + ux * 2.6)
      ctx.lineTo(p.x, p.y); ctx.lineTo(p.x - ux * 3.5 + uy * 2.6, p.y - uy * 3.5 - ux * 2.6); ctx.stroke()
    }
    for (const r of masks) ctx.clearRect(r.x - 2, r.y - 2, r.w + 4, r.h + 4)
  }
}
