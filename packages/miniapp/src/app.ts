import type { PropsWithChildren } from 'react'
import { useLaunch } from '@tarojs/taro'
import { useMapStore, useStationStore } from './store'
import './styles/global.scss'

export default function App({ children }: PropsWithChildren) {
  useLaunch(() => {
    useStationStore.getState().restore()
    useMapStore.getState().restore()
  })
  return children
}
