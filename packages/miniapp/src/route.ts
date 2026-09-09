/** 小程序路由保留 URL 编码；进入业务状态前只解码一次。 */
export function decodeRouteParam(value: string | undefined): string {
  if (!value) return ''
  try { return decodeURIComponent(value) } catch { return value }
}
