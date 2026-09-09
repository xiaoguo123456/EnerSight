import Taro from '@tarojs/taro'
import { View, Text } from '@tarojs/components'
import type { IndexLevel } from '@enersight/core/types'
import { Icon } from '../Icon'
import './index.scss'

interface Props {
  /** 0-100。null 表示不可算，展示「暂无可用指数」而非 0 分 */
  score: number | null
  level: IndexLevel | null
  /** AI 一句话结论 */
  summary: string | null
  onExplain?: () => void
}

const LEVEL_TEXT: Record<IndexLevel, string> = {
  excellent: '优秀', good: '良好', fair: '一般', poor: '较差',
}

/** 发电适宜度辅助摘要，保留估算口径说明入口。 */
export function EnergyScoreCard({ score, level, summary, onExplain }: Props) {
  const lv = level ?? 'unknown'
  return (
    <View className={`hero hero--${lv}`}>
      <View className="hero__row">
        <View className="hero__head" onClick={onExplain ?? (() => Taro.showModal({ title: '发电适宜度说明', content: '指数为 0–100 分，依据气象条件估算光伏或风电的发电适宜程度。分数越高，气象条件越有利。低分不代表设备故障；未触发天气预警也不代表发电条件良好。估算未接入电站实测出力，仅供参考。', showCancel: false, confirmText: '知道了' }))}>
          <Text className="hero__label">发电适宜度</Text>
          <Icon name="helpCircle" size={13} color="#64748b" />
        </View>

        {score === null ? (
          <Text className="hero__empty">暂无可用指数</Text>
        ) : (
          <View className="hero__score">
            <Text className="hero__num">{Math.round(score)}</Text>
            <Text className="hero__unit">分</Text>
            {level && (
              <View className="hero__level">
                <Text>{LEVEL_TEXT[level]}</Text>
              </View>
            )}
          </View>
        )}
      </View>
      {summary && <Text className="hero__summary">{summary}</Text>}

    </View>
  )
}
