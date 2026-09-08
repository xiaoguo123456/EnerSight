import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '@enersight/core/api'

export type RequestState<T> =
  | { status: 'loading'; data: null; error: null }
  | { status: 'success'; data: T; error: null }
  | { status: 'error'; data: null; error: ApiError }

/**
 * 请求状态机。每个用到接口的页面都要处理三种态，不要只写 success。
 *
 * - loading：骨架屏，不要转圈
 * - error：区分 503 无数据（展示空态，不给重试）与其他（给重试）
 */
export function useRequest<T>(fetcher: () => Promise<T>, deps: unknown[] = []) {
  const [state, setState] = useState<RequestState<T>>({ status: 'loading', data: null, error: null })
  const seq = useRef(0)

  const run = useCallback(async () => {
    const id = ++seq.current
    setState({ status: 'loading', data: null, error: null })
    try {
      const data = await fetcher()
      if (id === seq.current) setState({ status: 'success', data, error: null })
    } catch (e) {
      if (id !== seq.current) return
      const err = e instanceof ApiError ? e : new ApiError('UNKNOWN', '请求失败', 0)
      setState({ status: 'error', data: null, error: err })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => { void run() }, [run])

  return { ...state, reload: run }
}
