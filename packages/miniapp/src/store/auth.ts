/**
 * 登录状态。游客可浏览公开数据；我的电站、数据导出需要登录。docs/09 §4.3
 * 只保存令牌与「主动登录过」标记，不采集手机号、头像与昵称。
 */
import { create } from 'zustand'
import Taro from '@tarojs/taro'
import { isLoggedIn, LOGGED_IN_KEY, TOKEN_KEY, wechatLogin } from '@/api'
import { useStationStore } from './station'

interface AuthState {
  loggedIn: boolean
  restore: () => void
  login: () => Promise<void>
  logout: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  loggedIn: false,
  restore: () => set({ loggedIn: isLoggedIn() }),
  login: async () => {
    const token = await wechatLogin()
    Taro.setStorageSync(TOKEN_KEY, token)
    Taro.setStorageSync(LOGGED_IN_KEY, 1)
    set({ loggedIn: true })
  },
  logout: () => {
    try {
      Taro.removeStorageSync(TOKEN_KEY)
      Taro.removeStorageSync(LOGGED_IN_KEY)
    } catch {
      // 存储不可用时仍切换为游客
    }
    // 自建电站只对本账号可见，退出后不能留在本机的当前电站、收藏与最近浏览里
    useStationStore.getState().forgetOwn()
    set({ loggedIn: false })
  },
}))
