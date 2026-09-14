import type { PropsWithChildren } from 'react'
import { useLaunch } from '@tarojs/taro'
import { useMapStore, useStationStore } from './store'
import { useAuthStore } from './store/auth'
import './styles/global.scss'

export default function App({ children }: PropsWithChildren) {
  useLaunch(() => {
    useAuthStore.getState().restore()
    useStationStore.getState().restore()
    useMapStore.getState().restore()
  })
  return children
}
