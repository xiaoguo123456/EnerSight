/**
 * API client。平台差异通过注入 HttpAdapter 隔离 —— 小程序注入 Taro.request，
 * App 注入 fetch。见 docs/05 §四、docs/06 §十四。
 */
import type { ApiErrorBody, Coord } from '../types/index'

export interface RequestOptions {
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  url: string
  query?: Record<string, string | number | undefined>
  body?: unknown
  headers?: Record<string, string>
  timeoutMs: number
}

export interface RawResponse {
  status: number
  body: unknown
}

/** 各端实现：小程序 Taro.request，App fetch */
export interface HttpAdapter {
  request(opts: RequestOptions): Promise<RawResponse>
}

/** token 读写。小程序用 Storage，App 用各自的安全存储 */
export interface TokenStore {
  get(): Promise<string | null>
  set(token: string): Promise<void>
  clear(): Promise<void>
}

export interface ClientOptions {
  baseUrl: string
  adapter: HttpAdapter
  /** 本端固定的坐标系。小程序传 gcj02，见 docs/06 §2.2 */
  coord: Coord
  tokenStore: TokenStore
  /** token 失效时重新登录，返回新 token */
  relogin: () => Promise<string>
  timeoutMs?: number
  maxRetries?: number
}

export class ApiError extends Error {
  // 不用 TS 的构造器参数属性写法 —— Taro 的 babel 链路不认，会在构建时报错
  readonly code: string
  readonly status: number

  constructor(code: string, message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }

  /** 4xx 与 503 不重试；502 与网络错误可重试。docs/06 §十四 */
  get retryable(): boolean {
    return this.status === 502 || this.status === 0
  }
}

const DEFAULT_TIMEOUT_MS = 10_000
const DEFAULT_MAX_RETRIES = 2

export interface ApiClient {
  get<T>(url: string, query?: RequestOptions['query']): Promise<T>
  post<T>(url: string, body?: unknown): Promise<T>
  patch<T>(url: string, body?: unknown): Promise<T>
  delete(url: string): Promise<void>
}

export function createClient(opts: ClientOptions): ApiClient {
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS
  const maxRetries = opts.maxRetries ?? DEFAULT_MAX_RETRIES
  let loginInFlight: Promise<string> | null = null

  /** 并发请求共用同一次续登：令牌同时失效时，首页、预测等请求不能各自登录一遍。 */
  function login(): Promise<string> {
    if (!loginInFlight) {
      loginInFlight = (async () => {
        try {
          const fresh = await opts.relogin()
          await opts.tokenStore.set(fresh)
          return fresh
        } finally {
          loginInFlight = null
        }
      })()
    }
    return loginInFlight
  }

  async function send<T>(
    method: RequestOptions['method'],
    url: string,
    query?: RequestOptions['query'],
    body?: unknown,
    attempt = 0,
    didRelogin = false,
  ): Promise<T> {
    // 游客不带令牌直接请求公开数据，不主动登录。docs/09 §4.3
    const token = await opts.tokenStore.get()
    let res: RawResponse
    try {
      res = await opts.adapter.request({
        method,
        url: opts.baseUrl + url,
        query: { ...query, coord: opts.coord },
        body,
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        timeoutMs,
      })
    } catch {
      // 网络层失败，status 记 0，可重试
      if (attempt < maxRetries) {
        return send<T>(method, url, query, body, attempt + 1, didRelogin)
      }
      throw new ApiError('NETWORK_ERROR', '网络连接失败', 0)
    }

    if (res.status >= 200 && res.status < 300) {
      return (res.body as { data: T }).data
    }

    const errBody = res.body as ApiErrorBody | undefined
    const code = errBody?.error?.code ?? 'UNKNOWN'
    const message = errBody?.error?.message ?? '请求失败'
    const err = new ApiError(code, message, res.status)

    // 令牌失效或访问需登录的数据时续登并重试一次，避免认证失败死循环。
    // 游客能否续登由各端 relogin 决定：小程序未登录时抛 LOGIN_REQUIRED，由页面引导去登录页。
    if (res.status === 401 && ['UNAUTHORIZED', 'TOKEN_EXPIRED', 'LOGIN_REQUIRED'].includes(code) && !didRelogin) {
      await login()
      return send<T>(method, url, query, body, attempt, true)
    }

    if (err.retryable && attempt < maxRetries) {
      return send<T>(method, url, query, body, attempt + 1, didRelogin)
    }
    throw err
  }

  return {
    get: (url, query) => send('GET', url, query),
    post: (url, body) => send('POST', url, undefined, body),
    patch: (url, body) => send('PATCH', url, undefined, body),
    delete: async (url) => {
      await send('DELETE', url)
    },
  }
}
