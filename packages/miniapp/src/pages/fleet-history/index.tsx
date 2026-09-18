import { useAppShare } from '@/hooks/useAppShare'
import { Picker, View, Text } from '@tarojs/components'
import { useRouter } from '@tarojs/taro'
import { useState } from 'react'
import { PageHeader, Icon } from '@/components'
import { FleetHistory } from '@/components/FleetHistory'
import { servedModel, useWeatherModel, WEATHER_MODELS, weatherModelLabel } from '@/store/weatherModel'
import '../home/index.scss'
import './index.scss'

// 全目录不做三模式：这一页的选择器里不出现它，已选的按服务端实际用的自动模型显示。docs/19 §一
const OPTIONS = WEATHER_MODELS.filter(m => m.id !== 'ensemble')

/** 全目录历史独立于今日预测，保留模型选择，只展示已留存日期。 */
export default function FleetHistoryPage() {
  useAppShare()
  const { model, setModel } = useWeatherModel()
  const shown = servedModel(model)
  // 从首页带过来的地区范围；「看全国」后本页内不再筛，返回首页的筛选不受影响
  const initial = decodeURIComponent(useRouter().params.provinces ?? '')
  const [provinces, setProvinces] = useState(() => initial.split(',').filter(Boolean))
  return <View className="home history-page">
    <PageHeader title={provinces.length ? '地区历史趋势' : '历史趋势'} />
    <View className="history-page__body">
      <View className="history-page__model"><Text>气象模型</Text>
        <Picker mode="selector" range={OPTIONS.map(m => m.label)} value={OPTIONS.findIndex(m => m.id === shown)} onChange={e => { const next = OPTIONS[Number(e.detail.value)]; if (next && next.id !== shown) setModel(next.id) }}>
          <View className="forecast-model"><Text>{weatherModelLabel(shown)}</Text><Icon name="chevronDown" size={14} strokeWidth={1.5} /></View>
        </Picker>
      </View>
      <FleetHistory provinces={provinces} onClearScope={() => setProvinces([])} />
    </View>
  </View>
}
