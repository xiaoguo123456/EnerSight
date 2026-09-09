import { describe, expect, it, vi } from 'vitest'
import { createClient, type RawResponse } from './index'

describe('登录恢复', () => {
  it.each(['UNAUTHORIZED', 'TOKEN_EXPIRED'])('%s 后重新登录并携带新令牌重试', async (code) => {
    let token: string | null = null
    const request = vi.fn()
      .mockResolvedValueOnce({ status: 401, body: { error: { code, message: '未登录' } } })
      .mockResolvedValueOnce({ status: 200, body: { data: { ok: true } } })
    const relogin = vi.fn(async () => 'new-token')
    const client = createClient({
      baseUrl: 'https://example.com', coord: 'gcj02', adapter: { request }, relogin,
      tokenStore: { get: async () => token, set: async (v) => { token = v }, clear: async () => { token = null } },
    })
    await expect(client.get('/v1/home')).resolves.toEqual({ ok: true })
    expect(relogin).toHaveBeenCalledTimes(1)
    expect(request.mock.calls[1]?.[0].headers.Authorization).toBe('Bearer new-token')
  })

  it('登录后仍未授权时停止重试', async () => {
    const response: RawResponse = { status: 401, body: { error: { code: 'UNAUTHORIZED', message: '未登录' } } }
    const request = vi.fn(async () => response)
    const relogin = vi.fn(async () => 'invalid-token')
    const client = createClient({
      baseUrl: 'https://example.com', coord: 'gcj02', adapter: { request }, relogin,
      tokenStore: { get: async () => null, set: async () => {}, clear: async () => {} },
    })
    await expect(client.get('/v1/home')).rejects.toMatchObject({ code: 'UNAUTHORIZED' })
    expect(relogin).toHaveBeenCalledTimes(1)
    expect(request).toHaveBeenCalledTimes(2)
  })
})
