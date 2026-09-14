import Taro from '@tarojs/taro'
import { useAuthStore } from '@/store/auth'

/** 需要登录的操作先检查：未登录跳登录页并返回 false，用户登录后回到原页面再次操作。 */
export function requireLogin(): boolean {
  if (useAuthStore.getState().loggedIn) return true
  void Taro.navigateTo({ url: '/pages/login/index' })
  return false
}
