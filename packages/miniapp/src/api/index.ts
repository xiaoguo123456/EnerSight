/**
 * 小程序端的 API client 实例。
 * 平台差异只在这一个文件里 —— 注入 Taro.request 与 Storage。
 */
import Taro from '@tarojs/taro'
import { createClient, type HttpAdapter, type TokenStore } from '@enersight/core/api'

const TOKEN_KEY = 'enersight_token'

const adapter: HttpAdapter = {
  async request({ method, url, query, body, headers, timeoutMs }) {
    const qs = Object.entries(query ?? {})
      .filter(([, v]) => v !== undefined)
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
      .join('&')

    const res = await Taro.request({
      url: qs ? `${url}?${qs}` : url,
      method,
      data: body as Record<string, unknown> | undefined,
      header: { 'Content-Type': 'application/json', ...headers },
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

async function relogin(): Promise<string> {
  const { code } = await Taro.login()
  const res = await Taro.request({
    url: `${process.env.TARO_APP_API_BASE}/v1/auth/login`,
    method: 'POST',
    data: { code },
    header: { 'Content-Type': 'application/json' },
  })
  return (res.data as { data: { token: string } }).data.token
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
