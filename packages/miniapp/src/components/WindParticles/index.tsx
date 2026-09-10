import { Canvas, View, Text } from '@tarojs/components'
import Taro, { useDidHide, useDidShow } from '@tarojs/taro'
import { useEffect, useState } from 'react'
import { interpolateWind } from '@enersight/core/map'
import type { WindVector } from '@enersight/core/types'

/** 使用真实东西/南北风速驱动粒子；画面速度经过放大，供观察方向与相对强弱。 */
export function WindParticles({ vectors, region, layoutVersion }: { layoutVersion: number; vectors: WindVector[]; region: { southwest: WindVector | { latitude: number; longitude: number }; northeast: { latitude: number; longitude: number } } }) {
  const [playing, setPlaying] = useState(true)
  const [visible, setVisible] = useState(true)
  useDidHide(() => setVisible(false)); useDidShow(() => setVisible(true))
  useEffect(() => {
    if (!playing || !visible) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    const init = (tries = 0) => {
      const query = Taro.createSelectorQuery().select('#wind-particles').fields({ node: true, size: true, rect: true })
      for (const selector of ['.map-page__search', '.map-page__coverage', '.map-page__data-state', '.map-page__legend', '.map-page__tools', '.map-page__sheet', '.map-page__picked', '.map-page__wind-play', '.layer-ctrl']) query.select(selector).boundingClientRect()
      query.exec((items) => {
      if (cancelled) return
      const item = items?.[0], canvas = item?.node
      if (!canvas || !item.width || !item.height) { if (tries < 10) timer = setTimeout(() => init(tries + 1), 100); return }
      const masks = items.slice(1).filter(Boolean).map((r: { left: number; top: number; width: number; height: number }) => ({ x: r.left - item.left, y: r.top - item.top, w: r.width, h: r.height }))
      const w = item.width, h = item.height, dpr = Taro.getWindowInfo().pixelRatio || 1
      canvas.width = w * dpr; canvas.height = h * dpr
      const ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr)
      const merc = (lat: number) => Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360))
      const north = merc(region.northeast.latitude), south = merc(region.southwest.latitude)
      const points = vectors.map((v) => ({ x: (v.longitude - region.southwest.longitude) / (region.northeast.longitude - region.southwest.longitude) * w, y: (north - merc(v.latitude)) / (north - south) * h, u: v.u, v: v.v }))
      // 插值场预先计算，逐帧只做 O(粒子数) 更新。
      const cols = 24, rows = 32
      const field = Array.from({ length: cols * rows }, (_, k) => {
        const x = (k % cols) / (cols - 1) * w, y = Math.floor(k / cols) / (rows - 1) * h
        return interpolateWind(points, x, y)
      })
      const particles = Array.from({ length: Math.min(180, Math.round(w * h / 1600)) }, () => ({ x: Math.random() * w, y: Math.random() * h, age: Math.random() * 80 }))
      const draw = () => {
        if (cancelled) return
        ctx.globalCompositeOperation = 'destination-in'; ctx.fillStyle = 'rgba(0,0,0,.88)'; ctx.fillRect(0, 0, w, h)
        ctx.globalCompositeOperation = 'source-over'; ctx.strokeStyle = 'rgba(18,64,120,.85)'; ctx.lineWidth = 1.2
        particles.forEach((p) => {
          if (++p.age > 90 || p.x < 0 || p.x >= w || p.y < 0 || p.y >= h) { p.x = Math.random() * w; p.y = Math.random() * h; p.age = 0 }
          const f = field[Math.min(rows - 1, Math.floor(p.y / h * rows)) * cols + Math.min(cols - 1, Math.floor(p.x / w * cols))]
          if (!f || Math.hypot(f.u, f.v) < .2) return
          const x = p.x + f.u * .25, y = p.y - f.v * .25
          ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(x, y); ctx.stroke(); p.x = x; p.y = y
        })
        for (const r of masks) ctx.clearRect(r.x - 1, r.y - 1, r.w + 2, r.h + 2)
        timer = setTimeout(draw, 40)
      }
      draw()
      })
    }
    init()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [vectors, region, playing, visible, layoutVersion])
  return <>
    <Canvas type="2d" id="wind-particles" style={{ position: 'absolute', zIndex: 1, inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }} />
    <View className="map-page__wind-play" onClick={() => setPlaying((v) => !v)}><Text>{playing ? '暂停风场' : '播放风场'} · 风向与相对风速</Text></View>
  </>
}
