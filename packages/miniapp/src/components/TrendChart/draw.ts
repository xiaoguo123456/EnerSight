import { thousands } from '@enersight/core/format'

/**
 * 趋势图绘制。纯函数，不依赖 Taro，可单独测试。
 *
 * 绘制规范见 docs/02 §八：折线 + 面积渐变、横向虚线网格、无纵向网格、
 * 无轴线、选中态纵向虚线 + 悬浮值气泡。
 */

export interface ChartData {
  /** 等间隔序列：逐小时 25 点对应 00:00 – 24:00，或 15 分钟 96 点。缺测用 null，断线不补 0 */
  values: (number | null)[]
  comparison?: (number | null)[]
  /** 两条线在气泡里的名字；不传按「可发 / 上网」。卫星实况曲线传「预报 / 卫星实况」 */
  labels?: { value: string; comparison: string }
  /**
   * 对比线低于主线的时段打底纹。只有「可发 vs 上网」是这个语义（被限掉的部分），
   * 预报与卫星实况的差不是损失，不能打 —— 阴天整条都会被涂上。
   */
  shortfallShade?: boolean
  /**
   * 三模式的逐点包络，与 values 等长同序。两端任一为 null 的点不画，按段断开。
   * 只是视觉范围，不参与任何求和 —— 主数字来自 values 所属的那一家。docs/19 §一
   */
  band?: { low: (number | null)[]; high: (number | null)[] }
  times?: string[]
  unit: string
  /** 固定纵轴上限；null 表示按数据自适应。由服务端下发，见 docs/06 §八 */
  yMax: number | null
  /** 点间隔分钟数，默认 60；横轴标签与数据点密度据此计算 */
  stepMinutes?: number
}

/** 接口的 TrendSeries → 图表输入 */
export function fromTrendSeries(t: {
  points: { time?: string; value: number | null }[]
  unit: string
  y_max: number | null
  resolution_minutes?: number
  /** 卫星实况，与 points 等长同序；只有今日辐射曲线有。docs/19 §四 */
  satellite?: { time?: string; value: number | null }[] | null
}): ChartData {
  // 服务端保证等长同序，这里再核一次长度：对不上就不画，错位的曲线比没有更糟
  const satellite = t.satellite && t.satellite.length === t.points.length ? t.satellite : null
  return {
    values: t.points.map((p) => p.value),
    comparison: satellite ? satellite.map((p) => p.value) : undefined,
    labels: satellite ? { value: '预报', comparison: '卫星实况' } : undefined,
    times: t.points.every(p => p.time) ? t.points.map(p => p.time!) : undefined,
    unit: t.unit, yMax: t.y_max, stepMinutes: t.resolution_minutes ?? 60,
  }
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
  /** 三模式区间带填充色 */
  band: string
}

export const PAD_LEFT = 34
export const PAD_RIGHT = 8
const PAD_TOP_SINGLE = 40 // 单曲线两行提示
const PAD_TOP_COMPARE = 54 // 双曲线三行提示
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

/** 连续可画的下标段：某点在任一序列里缺测就断开，不跨缺口连线。 */
function segments(n: number, present: (i: number) => boolean): number[][] {
  const out: number[][] = []
  let cur: number[] = []
  for (let i = 0; i < n; i++) {
    if (present(i)) cur.push(i)
    else {
      if (cur.length) out.push(cur)
      cur = []
    }
  }
  if (cur.length) out.push(cur)
  return out
}

function fmtTick(v: number): string {
  return v >= 1000 ? thousands(v) : String(v)
}

function hhmm(minutes: number): string {
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

/** 手画圆角矩形。不用 ctx.roundRect —— 小程序 canvas 上它存在但行为不一致 */
function roundedRect(ctx: any, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.lineTo(x + w - r, y)
  ctx.arcTo(x + w, y, x + w, y + r, r)
  ctx.lineTo(x + w, y + h - r)
  ctx.arcTo(x + w, y + h, x + w - r, y + h, r)
  ctx.lineTo(x + r, y + h)
  ctx.arcTo(x, y + h, x, y + h - r, r)
  ctx.lineTo(x, y + r)
  ctx.arcTo(x, y, x + r, y, r)
  ctx.closePath()
}

export function draw(
  ctx: any,
  size: { w: number; h: number; dpr?: number },
  data: ChartData,
  active: number,
  t: ChartTheme,
): void {
  const { w, h } = size
  if (!w || !h) return

  // 按物理像素清空整块画布，再恢复逻辑坐标，避免高分屏触摸重绘残留气泡。
  ctx.save()
  ctx.setTransform(1, 0, 0, 1, 0, 0)
  ctx.clearRect(0, 0, w * (size.dpr ?? 1), h * (size.dpr ?? 1))
  ctx.restore()

  const n = data.values.length
  const step = data.stepMinutes ?? 60
  const timeLabel = (i: number) => data.times?.[i]?.slice(11, 16) ?? hhmm(i * step)
  // 带的上沿可能高过主曲线，纵轴要把它算进去，否则带会被画到画布外
  const max = niceMax(data.band ? [...data.values, ...data.band.high] : data.values, data.yMax)
  const x0 = PAD_LEFT
  const y0 = data.comparison ? PAD_TOP_COMPARE : PAD_TOP_SINGLE
  const iw = w - PAD_LEFT - PAD_RIGHT
  const ih = h - y0 - PAD_BOTTOM

  const px = (i: number) => x0 + (i / (n - 1)) * iw
  const py = (v: number) => y0 + ih - (v / max) * ih

  // 出力约束区间使用浅色底，不把缺测画成限电。
  if (data.comparison && data.shortfallShade) {
    ctx.fillStyle = 'rgba(245, 158, 11, 0.10)'
    data.comparison.forEach((v, i) => {
      const base = data.values[i]
      if (v != null && base != null && v < base && i < n - 1) ctx.fillRect(px(i), y0, iw / Math.max(1, n - 1), ih)
    })
  }

  // ── 横向虚线网格 + 纵轴刻度（无纵向网格、无轴线）──
  ctx.setLineDash?.([3, 3])
  ctx.lineWidth = 1
  ctx.strokeStyle = t.grid
  ctx.fillStyle = t.axisText
  ctx.font = '11px sans-serif'
  ctx.textAlign = 'right'
  ctx.textBaseline = 'middle'
  for (let g = 0; g <= GRID_LINES; g++) {
    const v = (max / GRID_LINES) * g
    const y = py(v)
    ctx.beginPath()
    ctx.moveTo(x0, y)
    ctx.lineTo(x0 + iw, y)
    ctx.stroke()
    ctx.fillText(fmtTick(Number(v.toFixed(max < 10 ? 2 : 0))), x0 - 6, y)
  }
  ctx.setLineDash?.([])

  // ── 三模式区间带：垫在最底层，主曲线压在上面。docs/19 §一 ──
  if (data.band) {
    const { low, high } = data.band
    ctx.fillStyle = t.band
    for (const seg of segments(n, (i) => low[i] != null && high[i] != null)) {
      if (seg.length < 2) continue
      ctx.beginPath()
      seg.forEach((i, k) => {
        const X = px(i)
        const Y = py(high[i] as number)
        k === 0 ? ctx.moveTo(X, Y) : ctx.lineTo(X, Y)
      })
      for (let k = seg.length - 1; k >= 0; k--) {
        const i = seg[k]!
        ctx.lineTo(px(i), py(low[i] as number))
      }
      ctx.closePath()
      ctx.fill()
    }
  }

  // ── 面积渐变 ──
  const segs = segments(n, (i) => data.values[i] !== null && data.values[i] !== undefined)

  const grad = ctx.createLinearGradient(0, y0, 0, y0 + ih)
  grad.addColorStop(0, t.areaTop)
  grad.addColorStop(1, t.areaBottom)

  // 有区间带时不画面积：两层浅蓝叠在一起，带的下沿就看不出来了，区间也就没了意义
  for (const seg of data.band ? [] : segs) {
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

  // ── 数据点：空心圆；96 点太密，只在逐小时以下的密度画 ──
  if (data.comparison) {
    ctx.beginPath()
    ctx.strokeStyle = '#15803d'
    ctx.lineWidth = 2
    let connected = false
    data.comparison.forEach((v, i) => {
      if (v == null) { connected = false; return }
      if (connected) ctx.lineTo(px(i), py(v))
      else ctx.moveTo(px(i), py(v))
      connected = true
    })
    ctx.stroke()
  }
  ctx.lineWidth = 1.5
  for (let i = 0; i < n; i++) {
    if (n > 40) break
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

  // ── 横轴刻度：窄画布每 6 小时，避免端点标签重叠 ──
  ctx.fillStyle = t.axisText
  ctx.font = '11px sans-serif'
  ctx.textBaseline = 'top'
  const every = Math.max(1, Math.round((iw < 250 ? 360 : 240) / step))
  for (let i = 0; i < n; i += every) {
    ctx.textAlign = i === 0 ? 'left' : i === n - 1 ? 'right' : 'center'
    ctx.fillText(timeLabel(i), px(i), y0 + ih + 6)
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
  const title = timeLabel(active)
  const names = data.labels ?? { value: '可发', comparison: '上网' }
  const value = `${data.comparison ? `${names.value} ` : ''}${Number(av.toFixed(2))} ${data.unit}`
  const cv = data.comparison?.[active]
  const compareValue = data.comparison ? `${names.comparison} ${cv == null ? '—' : Number(cv.toFixed(2))} ${data.unit}` : ''
  ctx.font = '11px sans-serif'
  const tw = Math.max(ctx.measureText(title).width, ctx.measureText(value).width, ctx.measureText(compareValue).width)
  const bw = tw + 14
  const bh = compareValue ? 46 : 30
  let bx = ax - bw / 2
  bx = Math.max(2, Math.min(bx, w - bw - 2))
  // 点贴近顶部时气泡放不下，翻到点下方
  const above = ay - bh - 8
  const by = above >= 2 ? above : ay + 10

  // 先画一层偏移的淡色当阴影，再画气泡本体，白卡片上也能浮起来
  roundedRect(ctx, bx, by + 1.5, bw, bh, 7)
  ctx.fillStyle = 'rgba(17, 24, 39, 0.08)'
  ctx.fill()
  roundedRect(ctx, bx, by, bw, bh, 7)
  ctx.fillStyle = t.tipBg
  ctx.fill()
  ctx.strokeStyle = t.tipBorder
  ctx.lineWidth = 1
  ctx.stroke()

  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  ctx.fillStyle = t.tipTitle
  ctx.fillText(title, bx + bw / 2, by + 5)
  ctx.fillStyle = t.tipValue
  ctx.font = 'bold 11px sans-serif'
  ctx.fillText(value, bx + bw / 2, by + 16)
  if (compareValue) {
    ctx.fillStyle = '#15803d'
    ctx.fillText(compareValue, bx + bw / 2, by + 31)
  }
}
