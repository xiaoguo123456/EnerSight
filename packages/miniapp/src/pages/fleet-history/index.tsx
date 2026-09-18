import { useAppShare } from '@/hooks/useAppShare'
import { Picker, View, Text } from '@tarojs/components'
import { useRouter } from '@tarojs/taro'
import { useState } from 'react'
import { PageHeader, Icon } from '@/components'
import { FleetHistory } from '@/components/FleetHistory'
import { useWeatherModel, WEATHER_MODELS, weatherModelLabel } from '@/store/weatherModel'
import '../home/index.scss'
import './index.scss'

/** 全目录历史独立于今日预测，保留模型选择，只展示已留存日期。 */
export default function FleetHistoryPage() {
  useAppShare()
  const { model, setModel } = useWeatherModel()
  // 从首页带过来的地区范围；「看全国」后本页内不再筛，返回首页的筛选不受影响
  const initial = decodeURIComponent(useRouter().params.provinces ?? '')
  const [provinces, setProvinces] = useState(() => initial.split(',').filter(Boolean))
  return <View className="home history-page">
    <PageHeader title={provinces.length ? '地区历史趋势' : '历史趋势'} />
    <View className="history-page__body">
      <View className="history-page__model"><Text>气象模型</Text>
        <Picker mode="selector" range={WEATHER_MODELS.map(m => m.label)} value={WEATHER_MODELS.findIndex(m => m.id === model)} onChange={e => { const next = WEATHER_MODELS[Number(e.detail.value)]; if (next) setModel(next.id) }}>
          <View className="forecast-model"><Text>{weatherModelLabel(model)}</Text><Icon name="chevronDown" size={14} strokeWidth={1.5} /></View>
        </Picker>
      </View>
      <FleetHistory provinces={provinces} onClearScope={() => setProvinces([])} />
    </View>
  </View>
}
