import { View, Text } from '@tarojs/components'
import type { SatelliteIrradiance } from '@enersight/core/types'
import { formatBeijingTime } from '@enersight/core/format'
import { Icon } from '../Icon'
import './index.scss'

interface Props {
  data: SatelliteIrradiance
  /** 无预警时这张卡是页面的 Hero，数字更大；有预警时退成普通卡 */
  hero?: boolean
}

const FALLBACK: Record<string, string> = {
  night: '夜间没有可见光反演，日出后恢复',
  stale: '最新一帧过旧，暂按预报显示',
  unavailable: '卫星数据暂时拿不到',
}

/**
 * 卫星辐照实况。docs/19 §四
 *
 * 只展示，不参与任何计算 —— 精度按 07 §8.1 回测达标后才会替换当前功率。
 * 署名不能删：JAXA 研究数据条款要求衍生展示注明来源。
 */
export function SatelliteNowcast({ data, hero = false }: Props) {
  const ok = data.status === 'ok' && data.ghi_w_m2 !== null
  const percent = data.clear_sky_index !== null ? Math.round(data.clear_sky_index * 100) : null
  return (
    <View className={`sat-now${hero ? ' sat-now--hero' : ''}`}>
      <View className="sat-now__head">
        <Icon name="sun" size={16} color="#f59e0b" strokeWidth={1.75} />
        <Text className="sat-now__title">卫星辐照实况</Text>
        {ok && data.observed_at && (
          <Text className="sat-now__time">{formatBeijingTime(data.observed_at)}</Text>
        )}
      </View>

      {ok ? (
        <View className="sat-now__body">
          <View className="sat-now__value">
            <Text className="sat-now__num">{data.ghi_w_m2}</Text>
            <Text className="sat-now__unit">W/m²</Text>
          </View>
          {percent !== null && <Text className="sat-now__ratio">晴空的 {percent}%</Text>}
        </View>
      ) : (
        <Text className="sat-now__empty">{FALLBACK[data.status] ?? '卫星数据暂时拿不到'}</Text>
      )}

      {ok && (data.direct_w_m2 !== null || data.diffuse_w_m2 !== null) && (
        <Text className="sat-now__split">
          直射 {data.direct_w_m2 ?? '—'} · 散射 {data.diffuse_w_m2 ?? '—'} W/m²
        </Text>
      )}
      <Text className="sat-now__source">{data.source}</Text>
    </View>
  )
}
