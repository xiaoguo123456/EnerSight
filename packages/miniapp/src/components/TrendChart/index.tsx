import { Canvas } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useCallback, useEffect, useRef, useState } from 'react'
import { PAD_LEFT, PAD_RIGHT, draw, type ChartData, type ChartTheme } from './draw'
import './index.scss'

interface Props {
  id: string
  data: ChartData
  height?: number
}

const THEME: ChartTheme = {
  line: '#1677FF',
  areaTop: 'rgba(22, 119, 255, 0.16)',
  areaBottom: 'rgba(22, 119, 255, 0)',
  grid: '#EFF3F7',
  axisText: '#526174',
  surface: '#FFFFFF',
  tipBg: '#FFFFFF',
  tipBorder: '#E5EAF0',
  tipTitle: '#526174',
  tipValue: '#1F2937',
}

/**
 * 解析 canvas node。两端行为不同：
 * - 小程序：fields({node:true}) 直接给 Canvas 实例
 * - H5：给的是 <taro-canvas-core> 自定义元素，真实 canvas 在其内部
 */
function resolveCanvas(node: any, id: string): any {
  if (typeof node?.getContext === 'function') return node
  // 小程序的 document 是运行时模拟节点，不支持浏览器 DOM 查询。
  if (process.env.TARO_ENV !== 'h5') return null
  if (typeof node?.querySelector === 'function') {
    const inner = node.querySelector('canvas')
    if (inner) return inner
  }
  if (typeof document !== 'undefined') {
    const el = document.getElementById(id)
    if (typeof (el as any)?.getContext === 'function') return el
    if (typeof el?.querySelector === 'function') return el.querySelector('canvas')
  }
  return null
}

/**
 * 24 小时趋势图。折线 + 面积渐变，点击数据点显示悬浮值与纵向虚线。docs/02 §八
 *
 * 用 Canvas 2D 自绘而非图表库：本项目只有这一种图表，
 * 引入 ECharts 小程序版要多 300KB+ 和一层适配黑盒。docs/05 §7.4
 */
export function TrendChart({ id, data, height = 150 }: Props) {
  const ctxRef = useRef<any>(null)
  const sizeRef = useRef({ w: 0, h: 0, dpr: 1 })
  const [ready, setReady] = useState(false)

  const peak = useCallback((vals: (number | null)[]) => {
    let best = 0
    let bestV = -Infinity
    vals.forEach((v, i) => {
      if (v !== null && v > bestV) { bestV = v; best = i }
    })
    return best
  }, [])

  const [active, setActive] = useState<number>(() => peak(data.values))

  // 数据换了（切 Tab）重新定位峰值
  useEffect(() => { setActive(peak(data.values)) }, [data, peak])

  useEffect(() => {
    let cancelled = false
    let tries = 0

    const init = () => {
      if (cancelled) return
      Taro.createSelectorQuery()
        .select(`#${id}`)
        .fields({ node: true, size: true })
        .exec((res) => {
          if (cancelled) return
          const item = res?.[0]
          const canvas = resolveCanvas(item?.node, id)
          const w = item?.width || canvas?.clientWidth || 0
          const h = item?.height || canvas?.clientHeight || height

          // 布局未完成时 size 为 0，退避重试
          if (!canvas || !w) {
            if (tries++ < 10) setTimeout(init, 60)
            return
          }

          const ctx = canvas.getContext('2d')
          const dpr = Taro.getWindowInfo?.().pixelRatio ?? 2
          canvas.width = w * dpr
          canvas.height = h * dpr
          ctx.scale(dpr, dpr)
          ctxRef.current = ctx
          sizeRef.current = { w, h, dpr }
          setReady(true)
        })
    }
    init()
    return () => { cancelled = true }
  }, [id, height])

  useEffect(() => {
    if (!ready || !ctxRef.current) return
    draw(ctxRef.current, sizeRef.current, data, active, THEME)
  }, [ready, data, active])

  const onTouch = (e: any) => {
    const t = e.touches?.[0] ?? e.changedTouches?.[0]
    const { w } = sizeRef.current
    if (!t || !w) return

    // 小程序给 t.x（相对 canvas）；H5 只给 clientX，需减去元素左边距
    let x = t.x
    if (x === undefined) {
      const el = typeof document !== 'undefined' ? document.getElementById(id) : null
      const left = el?.getBoundingClientRect?.().left ?? 0
      x = (t.clientX ?? 0) - left
    }

    const n = data.values.length
    const inner = w - PAD_LEFT - PAD_RIGHT
    const idx = Math.round(((x - PAD_LEFT) / inner) * (n - 1))
    setActive(Math.max(0, Math.min(n - 1, idx)))
  }

  return (
    <Canvas
      id={id}
      canvasId={id}
      type="2d"
      className="trend-chart"
      style={{ height: `${height}px` }}
      onTouchStart={onTouch}
      onTouchMove={onTouch}
    />
  )
}
