/**
 * 趋势图绘制。纯函数，不依赖 Taro，可单独测试。
 *
 * 绘制规范见 docs/02 §八：折线 + 面积渐变、横向虚线网格、无纵向网格、
 * 无轴线、选中态纵向虚线 + 悬浮值气泡。
 */

export interface ChartData {
  /** 逐小时值，25 个点对应 00:00 – 24:00。缺测用 null，断线不补 0 */
  values: (number | null)[]
  unit: string
  /** 固定纵轴上限；null 表示按数据自适应。由服务端下发，见 docs/06 §八 */
  yMax: number | null
}

export interface ChartTheme {
  line: string
  areaTop: string
  areaBottom: string
  grid: string
  axisText: string
  surface: string
  tipBg: string
  tipBorder: string
  tipTitle: string
  tipValue: string
}

export const PAD_LEFT = 34
export const PAD_RIGHT = 8
const PAD_TOP = 36 // 给悬浮气泡留出空间（气泡高 30 + 指向间隙）
const PAD_BOTTOM = 20 // 横轴标签
const GRID_LINES = 4

/** 纵轴刻度：优先用服务端下发的 yMax，否则按数据取整到「好看的」上界 */
function niceMax(values: (number | null)[], yMax: number | null): number {
  if (yMax !== null) return yMax
  const max = values.reduce<number>((m, v) => (v !== null && v > m ? v : m), 0)
  if (max <= 0) return 1
  const mag = 10 ** Math.floor(Math.log10(max))
  return Math.ceil(max / mag) * mag
}

function fmtTick(v: number): string {
  return v >= 1000 ? v.toLocaleString('en-US') : String(v)
}

function hhmm(i: number): string {
  return `${String(i).padStart(2, '0')}:00`
}

export function draw(
  ctx: any,
  size: { w: number; h: number },
  data: ChartData,
  active: number,
  t: ChartTheme,
): void {
  const { w, h } = size
  if (!w || !h) return

  ctx.clearRect(0, 0, w, h)

  const n = data.values.length
  const max = niceMax(data.values, data.yMax)
  const x0 = PAD_LEFT
  const y0 = PAD_TOP
  const iw = w - PAD_LEFT - PAD_RIGHT
  const ih = h - PAD_TOP - PAD_BOTTOM

  const px = (i: number) => x0 + (i / (n - 1)) * iw
  const py = (v: number) => y0 + ih - (v / max) * ih

  // ── 横向虚线网格 + 纵轴刻度（无纵向网格、无轴线）──
  ctx.setLineDash?.([3, 3])
  ctx.lineWidth = 1
  ctx.strokeStyle = t.grid
  ctx.fillStyle = t.axisText
  ctx.font = '10px sans-serif'
  ctx.textAlign = 'right'
  ctx.textBaseline = 'middle'
  for (let g = 0; g <= GRID_LINES; g++) {
    const v = (max / GRID_LINES) * g
    const y = py(v)
    ctx.beginPath()
    ctx.moveTo(x0, y)
    ctx.lineTo(x0 + iw, y)
    ctx.stroke()
    ctx.fillText(fmtTick(Math.round(v)), x0 - 6, y)
  }
  ctx.setLineDash?.([])

  // ── 面积渐变 ──
  const segs: number[][] = []
  let cur: number[] = []
  data.values.forEach((v, i) => {
    if (v === null) {
      if (cur.length) segs.push(cur)
      cur = []
    } else cur.push(i)
  })
  if (cur.length) segs.push(cur)

  const grad = ctx.createLinearGradient(0, y0, 0, y0 + ih)
  grad.addColorStop(0, t.areaTop)
  grad.addColorStop(1, t.areaBottom)

  for (const seg of segs) {
    if (seg.length < 2) continue
    ctx.beginPath()
    ctx.moveTo(px(seg[0]!), y0 + ih)
    for (const i of seg) ctx.lineTo(px(i), py(data.values[i] as number))
    ctx.lineTo(px(seg[seg.length - 1]!), y0 + ih)
    ctx.closePath()
    ctx.fillStyle = grad
    ctx.fill()
  }

  // ── 折线 ──
  ctx.lineWidth = 2
  ctx.strokeStyle = t.line
  ctx.lineJoin = 'round'
  ctx.lineCap = 'round'
  for (const seg of segs) {
    if (seg.length < 2) continue
    ctx.beginPath()
    seg.forEach((i, k) => {
      const X = px(i)
      const Y = py(data.values[i] as number)
      k === 0 ? ctx.moveTo(X, Y) : ctx.lineTo(X, Y)
    })
    ctx.stroke()
  }

  // ── 数据点：空心圆 ──
  ctx.lineWidth = 1.5
  for (let i = 0; i < n; i++) {
    const v = data.values[i]
    if (v === null || v === undefined) continue
    if (i === active) continue
    ctx.beginPath()
    ctx.arc(px(i), py(v), 2.4, 0, Math.PI * 2)
    ctx.fillStyle = t.surface
    ctx.fill()
    ctx.strokeStyle = t.line
    ctx.stroke()
  }

  // ── 横轴刻度：每 4 小时 ──
  ctx.fillStyle = t.axisText
  ctx.font = '10px sans-serif'
  ctx.textBaseline = 'top'
  for (let i = 0; i < n; i += 4) {
    ctx.textAlign = i === 0 ? 'left' : i === n - 1 ? 'right' : 'center'
    ctx.fillText(hhmm(i), px(i), y0 + ih + 6)
  }

  // ── 选中态：纵向虚线 + 实心点 + 悬浮气泡 ──
  const av = data.values[active]
  if (av === null || av === undefined) return
  const ax = px(active)
  const ay = py(av)

  ctx.setLineDash?.([3, 3])
  ctx.lineWidth = 1
  ctx.strokeStyle = t.line
  ctx.beginPath()
  ctx.moveTo(ax, ay)
  ctx.lineTo(ax, y0 + ih)
  ctx.stroke()
  ctx.setLineDash?.([])

  ctx.beginPath()
  ctx.arc(ax, ay, 4, 0, Math.PI * 2)
  ctx.fillStyle = t.line
  ctx.fill()
  ctx.strokeStyle = t.surface
  ctx.lineWidth = 2
  ctx.stroke()

  // 气泡：两行（时刻 / 数值+单位），贴边时自动收进画布内
  const title = hhmm(active)
  const value = `${av} ${data.unit}`
  ctx.font = '10px sans-serif'
  const tw = Math.max(ctx.measureText(title).width, ctx.measureText(value).width)
  const bw = tw + 14
  const bh = 30
  let bx = ax - bw / 2
  bx = Math.max(2, Math.min(bx, w - bw - 2))
  // 点贴近顶部时气泡放不下，翻到点下方
  const above = ay - bh - 8
  const by = above >= 2 ? above : ay + 10

  ctx.fillStyle = t.tipBg
  ctx.strokeStyle = t.tipBorder
  ctx.lineWidth = 1
  if (ctx.roundRect) {
    ctx.beginPath()
    ctx.roundRect(bx, by, bw, bh, 6)
    ctx.fill()
    ctx.stroke()
  } else {
    ctx.fillRect(bx, by, bw, bh)
    ctx.strokeRect(bx, by, bw, bh)
  }

  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  ctx.fillStyle = t.tipTitle
  ctx.fillText(title, bx + bw / 2, by + 5)
  ctx.fillStyle = t.tipValue
  ctx.font = 'bold 11px sans-serif'
  ctx.fillText(value, bx + bw / 2, by + 16)
}
