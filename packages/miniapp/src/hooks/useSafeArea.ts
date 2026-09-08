import Taro from '@tarojs/taro'

export interface SafeArea {
  /** 状态栏高度 */
  statusBarHeight: number
  /** 自定义导航栏总高度（状态栏 + 胶囊区），内容应从此高度之下开始 */
  navBarHeight: number
  /** 导航栏内容区高度（不含状态栏），用于垂直居中标题 */
  navContentHeight: number
  /** 右侧需要避开胶囊按钮的宽度 */
  menuGuardRight: number
}

// H5 与非微信环境下的兜底值
const FALLBACK: SafeArea = {
  statusBarHeight: 44,
  navBarHeight: 88,
  navContentHeight: 44,
  menuGuardRight: 0,
}

let cached: SafeArea | null = null

/**
 * 自定义导航栏的安全区。
 *
 * 用了 navigationStyle: 'custom' 就必须自己避开两样东西：
 *   1. 状态栏（刘海/时间信号）
 *   2. 右上角的胶囊按钮（··· ⊙），它是系统绘制的，盖不掉也移不走
 *
 * 导航栏高度按胶囊上下对称留白算，这是官方推荐做法。
 */
export function getSafeArea(): SafeArea {
  if (cached) return cached

  try {
    const win = Taro.getWindowInfo()
    const menu = Taro.getMenuButtonBoundingClientRect?.()
    if (!menu || !menu.height) {
      cached = FALLBACK
      return cached
    }
    const statusBarHeight = win.statusBarHeight ?? FALLBACK.statusBarHeight
    // 胶囊上方留白 = menu.top - statusBarHeight，下方留同样的白
    const gap = menu.top - statusBarHeight
    const navContentHeight = menu.height + gap * 2
    cached = {
      statusBarHeight,
      navBarHeight: statusBarHeight + navContentHeight,
      navContentHeight,
      // 胶囊左边界到屏幕右侧的距离，再留 8px 呼吸
      menuGuardRight: win.windowWidth - menu.left + 8,
    }
    return cached
  } catch {
    cached = FALLBACK
    return cached
  }
}
