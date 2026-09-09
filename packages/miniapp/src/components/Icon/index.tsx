import { Image } from '@tarojs/components'
import { ICON_PATHS, type IconName } from './paths'

export type { IconName }

interface Props {
  name: IconName
  /** 逻辑像素 */
  size?: number
  /** 描边色 */
  color?: string
  /**
   * 主体填充。true 用描边色 20% 透明度填充，做出双色效果，
   * 比纯线性图标更有分量；传字符串则用该色 100% 填充。
   * 对开放路径（如 wind 的三条线）填充无意义，那类图标不要开。
   */
  fill?: boolean | string
  strokeWidth?: number
  className?: string
}

/**
 * 线性图标，路径来自 Lucide。
 *
 * 小程序不支持内联 <svg>，所以拼 SVG 字符串转 data URI 交给 <Image>。
 * 不用 emoji：emoji 的字形随系统变化，颜色、粗细、基线都不可控。
 */
export function Icon({
  name, size = 24, color = 'currentColor', fill = false, strokeWidth = 1.75, className,
}: Props) {
  const fillAttr =
    fill === true
      ? `fill="${color}" fill-opacity="0.22"`
      : fill
        ? `fill="${fill}"`
        : 'fill="none"'

  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" ` +
    `viewBox="0 0 24 24" ${fillAttr} stroke="${color}" stroke-width="${strokeWidth}" ` +
    `stroke-linecap="round" stroke-linejoin="round">${ICON_PATHS[name]}</svg>`

  return (
    <Image
      className={className}
      style={{ width: `${size}px`, height: `${size}px`, flexShrink: 0 }}
      src={`data:image/svg+xml,${encodeURIComponent(svg)}`}
      mode="aspectFit"
    />
  )
}
