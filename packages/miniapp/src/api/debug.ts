/** 开发态：把客户端错误上报到服务端日志，模拟器里看不到控制台时用 */
import Taro from '@tarojs/taro'

export function clientLog(tag: string, message: string, extra?: Record<string, unknown>) {
  const base = process.env.TARO_APP_API_BASE ?? ''
  // 只在连本地后端时上报，生产域名不带这个接口
  if (!/127\.0\.0\.1|localhost/.test(base)) return
  Taro.request({
    url: `${base}/v1/debug/log`,
    method: 'POST',
    data: { level: 'error', tag, message, extra },
    header: { 'Content-Type': 'application/json' },
  }).catch(() => undefined)
}
