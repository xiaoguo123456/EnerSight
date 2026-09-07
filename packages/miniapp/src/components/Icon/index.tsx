import { Image } from '@tarojs/components'
import { ICON_PATHS, type IconName } from './paths'

export type { IconName }

interface Props {
  name: IconName
  /** 逻辑像素，最终按 rpx 缩放 */
  size?: number
  /** 描边色，支持 CSS 变量取到的具体色值 */
  color?: string
  strokeWidth?: number
  className?: string
}

/**
 * 线性图标。
 *
 * 小程序不支持内联 <svg>，所以拼 SVG 字符串转 data URI 交给 <Image>。
 * 不用 emoji：emoji 的字形随系统变化，颜色、粗细、基线都不可控。
 */
export function Icon({
  name, size = 24, color = 'currentColor', strokeWidth = 2, className,
}: Props) {
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ` +
    `fill="none" stroke="${color}" stroke-width="${strokeWidth}" ` +
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
