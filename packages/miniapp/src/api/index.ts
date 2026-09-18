/**
 * 小程序端的 API client 实例。
 * 平台差异只在这一个文件里 —— 注入 Taro.request 与 Storage。
 */
import Taro from '@tarojs/taro'
import { ApiError, createClient, type HttpAdapter, type TokenStore } from '@enersight/core/api'

import { servedModel, useWeatherModel } from '@/store/weatherModel'

export const TOKEN_KEY = 'enersight_token'
/** 用户主动登录过才允许后台续登；游客浏览公开数据不自动登录。docs/09 §4.3 */
export const LOGGED_IN_KEY = 'enersight_logged_in'

const adapter: HttpAdapter = {
  async request({ method, url, query, body, headers, timeoutMs }) {
    const qs = Object.entries(query ?? {})
      .filter(([, v]) => v !== undefined)
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
      .join('&')

    // X-Resolution-Minutes：取 15 分钟曲线；不带时服务端按线上旧版返回逐小时，见 docs/06 §2.8
    // X-Weather-Model 只带真实模型名：三模式只有 7 天预测认，由 stationsApi.outlook 用查询参数单独带。
    // 头里带 ensemble 的话，后端还没部署到新版时每个接口都会回 400 INVALID_MODEL。docs/19 §一
    const res = await Taro.request({
      url: qs ? `${url}?${qs}` : url,
      method,
      data: body as Record<string, unknown> | undefined,
      header: { 'Content-Type': 'application/json', ...headers, 'X-Weather-Model': servedModel(useWeatherModel.getState().model), 'X-Resolution-Minutes': '15' },
      timeout: timeoutMs,
    })
    return { status: res.statusCode, body: res.data }
  },
}

const tokenStore: TokenStore = {
  async get() {
    try {
      return Taro.getStorageSync(TOKEN_KEY) || null
    } catch {
      return null
    }
  },
  async set(token) {
    Taro.setStorageSync(TOKEN_KEY, token)
  },
  async clear() {
    Taro.removeStorageSync(TOKEN_KEY)
  },
}

export function isLoggedIn(): boolean {
  try {
    return Taro.getStorageSync(LOGGED_IN_KEY) === 1
  } catch {
    return false
  }
}

/** 微信登录：wx.login 换 code，服务端 code2session 签发令牌。H5 调试没有 wx.login，用开发态凭证。 */
export async function wechatLogin(): Promise<string> {
  const code = process.env.TARO_ENV === 'h5' ? 'h5-dev' : (await Taro.login()).code
  const res = await Taro.request({
    url: `${process.env.TARO_APP_API_BASE}/v1/auth/login`,
    method: 'POST',
    data: { code },
    header: { 'Content-Type': 'application/json' },
  })
  const body = res.data as { data?: { token?: string }; error?: { code?: string; message?: string } } | undefined
  if (res.statusCode !== 200 || !body?.data?.token) {
    throw new ApiError(body?.error?.code ?? 'LOGIN_UNAVAILABLE', body?.error?.message ?? '登录失败，请稍后重试', res.statusCode)
  }
  return body.data.token
}

/** 令牌失效时的续登：只对主动登录过的用户静默进行，游客交给页面引导去登录页。 */
async function relogin(): Promise<string> {
  if (!isLoggedIn()) throw new ApiError('LOGIN_REQUIRED', '请先登录', 401)
  return wechatLogin()
}

export const api = createClient({
  baseUrl: process.env.TARO_APP_API_BASE ?? '',
  // 小程序底图是腾讯地图，固定用 GCJ-02。转换由服务端完成，
  // 客户端不做坐标换算。见 docs/06 §2.2
  coord: 'gcj02',
  adapter,
  tokenStore,
  relogin,
})

/** 图层与历史影像允许较长计算时间，不自动重试，防止冷请求倍增。 */
export const imageryApi = createClient({
  baseUrl: process.env.TARO_APP_API_BASE ?? '', coord: 'gcj02', adapter, tokenStore, relogin,
  timeoutMs: 40_000, maxRetries: 0,
})
