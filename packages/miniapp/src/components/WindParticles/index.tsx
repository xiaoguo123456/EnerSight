import { Canvas } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useEffect } from 'react'
import type { WindVector } from '@enersight/core/types'
import { createWindAnimation, type Region } from './draw'

export function WindParticles({ vectors, region, layoutVersion }: { layoutVersion: number; vectors: WindVector[]; region: Region }) {
  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let context: CanvasRenderingContext2D | undefined
    let width = 0, height = 0
    const init = (tries = 0) => {
      const query = Taro.createSelectorQuery().select('#wind-particles').fields({ node: true, size: true, rect: true })
      for (const selector of ['.map-page__search', '.map-page__coverage', '.map-page__data-state', '.map-page__legend', '.map-page__tools', '.map-page__sheet', '.map-page__picked', '.layer-ctrl']) query.select(selector).boundingClientRect()
      query.exec(items => {
        if (cancelled) return
        const item = items?.[0]
        let canvas = item?.node
        if (process.env.TARO_ENV === 'h5' && typeof canvas?.getContext !== 'function') canvas = canvas?.querySelector?.('canvas')
        if (!canvas?.getContext || !item.width || !item.height) {
          if (tries < 10) timer = setTimeout(() => init(tries + 1), 100)
          return
        }
        const masks = items.slice(1).filter(Boolean).map((r: { left: number; top: number; width: number; height: number }) => ({ x: r.left - item.left, y: r.top - item.top, w: r.width, h: r.height }))
        width = item.width; height = item.height
        const dpr = Taro.getWindowInfo().pixelRatio || 1
        canvas.width = width * dpr; canvas.height = height * dpr
        const ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr); context = ctx
        const frame = createWindAnimation(vectors, region, width, height, masks)
        // H5 尊重系统减少动态效果的偏好，保留静态风向箭头。
        const reduced = process.env.TARO_ENV === 'h5' && typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
        let previous = Date.now() - 40
        const draw = () => {
          if (cancelled) return
          const now = Date.now(); frame(ctx, now - previous); previous = now
          if (!reduced) timer = setTimeout(draw, 40)
        }
        draw()
      })
    }
    init()
    return () => { cancelled = true; clearTimeout(timer); context?.clearRect(0, 0, width, height) }
  }, [vectors, region, layoutVersion])
  return <Canvas type="2d" id="wind-particles" canvasId="wind-particles" style={{ position: 'absolute', zIndex: 1, left: 0, top: 0, width: '100%', height: '100%', pointerEvents: 'none' }} />
}
