import { describe, expect, it, vi, type Mock } from 'vitest'
import { ApiError, createClient, type RawResponse, type RequestOptions } from './index'

type Request = Mock<(opts: RequestOptions) => Promise<RawResponse>>

const ok = (data: unknown = { ok: true }): RawResponse => ({ status: 200, body: { data } })
const denied = (code = 'UNAUTHORIZED'): RawResponse => ({ status: 401, body: { error: { code, message: '未登录' } } })
const requestMock = (impl?: (opts: RequestOptions) => Promise<RawResponse>): Request =>
  vi.fn<(opts: RequestOptions) => Promise<RawResponse>>(impl)

function setup(request: Request, relogin: () => Promise<string>, initial: string | null = null) {
  let token = initial
  const client = createClient({
    baseUrl: 'https://example.com', coord: 'gcj02', adapter: { request }, relogin,
    tokenStore: { get: async () => token, set: async (v) => { token = v }, clear: async () => { token = null } },
  })
  return { client, token: () => token }
}

describe('登录恢复', () => {
  it.each(['UNAUTHORIZED', 'TOKEN_EXPIRED'])('令牌失效返回 %s 后重新登录并携带新令牌重试', async (code) => {
    const request = requestMock().mockResolvedValueOnce(denied(code)).mockResolvedValueOnce(ok())
    const relogin = vi.fn(async () => 'new-token')
    const { client } = setup(request, relogin, 'stale-token')
    await expect(client.get('/v1/home')).resolves.toEqual({ ok: true })
    expect(relogin).toHaveBeenCalledTimes(1)
    expect(request.mock.calls[1]?.[0].headers?.Authorization).toBe('Bearer new-token')
  })

  it('登录后仍未授权时停止重试', async () => {
    const request = requestMock(async () => denied())
    const relogin = vi.fn(async () => 'invalid-token')
    const { client } = setup(request, relogin, 'stale-token')
    await expect(client.get('/v1/home')).rejects.toMatchObject({ code: 'UNAUTHORIZED' })
    expect(relogin).toHaveBeenCalledTimes(1)
    expect(request).toHaveBeenCalledTimes(2)
  })

  it('游客不主动登录，直接请求公开数据', async () => {
    const request = requestMock(async () => ok())
    const relogin = vi.fn(async () => 'fresh-token')
    const { client } = setup(request, relogin)
    await client.get('/v1/stations/public')
    expect(relogin).not.toHaveBeenCalled()
    expect(request.mock.calls[0]?.[0].headers).toEqual({})
  })

  it('游客访问需登录的数据时抛出 LOGIN_REQUIRED，交给页面引导登录', async () => {
    const request = requestMock(async () => denied('LOGIN_REQUIRED'))
    const relogin = vi.fn(async (): Promise<string> => { throw new ApiError('LOGIN_REQUIRED', '请先登录', 401) })
    const { client } = setup(request, relogin)
    await expect(client.get('/v1/stations')).rejects.toMatchObject({ code: 'LOGIN_REQUIRED' })
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('已登录但本地令牌丢失时续登后重试', async () => {
    const request = requestMock().mockResolvedValueOnce(denied('LOGIN_REQUIRED')).mockResolvedValueOnce(ok())
    const relogin = vi.fn(async () => 'fresh-token')
    const { client, token } = setup(request, relogin)
    await expect(client.get('/v1/stations')).resolves.toEqual({ ok: true })
    expect(relogin).toHaveBeenCalledTimes(1)
    expect(token()).toBe('fresh-token')
  })

  it('令牌同时失效的并发请求共用一次重新登录', async () => {
    const request = requestMock(async (opts) =>
      opts.headers?.Authorization === 'Bearer new-token' ? ok() : denied('TOKEN_EXPIRED'))
    const relogin = vi.fn(async () => 'new-token')
    const { client } = setup(request, relogin, 'stale-token')
    await Promise.all([client.get('/v1/home'), client.get('/v1/trends'), client.get('/v1/alerts')])
    expect(relogin).toHaveBeenCalledTimes(1)
  })
})
