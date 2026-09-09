import { useCallback, useEffect, useRef, useState } from 'react'
import { useDidShow } from '@tarojs/taro'
import { ApiError } from '@enersight/core/api'

export type RequestState<T> =
  | { status: 'loading'; data: null; error: null }
  | { status: 'success'; data: T; error: null }
  | { status: 'error'; data: null; error: ApiError }

/** 切换查询时清空旧结果；刷新失败保留已成功的数据，并明确暴露失败状态。 */
export function useRequest<T>(fetcher: () => Promise<T>, deps: unknown[] = []) {
  const [state, setState] = useState<RequestState<T>>({ status: 'loading', data: null, error: null })
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState<ApiError | null>(null)
  const seq = useRef(0)
  const checked = useRef(0)
  const run = useCallback(async (preserve = false) => {
    const id = ++seq.current
    checked.current = Date.now()
    setRefreshing(true); setRefreshError(null)
    setState((s) => preserve && s.status === 'success' ? s : { status: 'loading', data: null, error: null })
    try {
      const data = await fetcher()
      if (id === seq.current) setState({ status: 'success', data, error: null })
    } catch (e) {
      if (id !== seq.current) return
      const error = e instanceof ApiError ? e : new ApiError('UNKNOWN', '请求失败', 0)
      setRefreshError(error)
      setState((s) => preserve && s.status === 'success' ? s : { status: 'error', data: null, error })
    } finally { if (id === seq.current) setRefreshing(false) }
    // 查询参数由调用方声明。
  }, deps)
  const reload = useCallback(() => run(true), [run])
  useEffect(() => { void run(); return () => { ++seq.current } }, [run])
  useDidShow(() => { if (checked.current && Date.now() - checked.current > 60_000) void reload() })
  return { ...state, reload, refreshing, refreshError }
}
