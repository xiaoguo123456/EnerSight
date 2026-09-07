import { View, Text } from '@tarojs/components'
import type { IndexLevel } from '@enersight/core/types'
import './index.scss'

interface Props {
  /** 0-100。null 表示不可算，展示「数据获取中」而非 0 分 */
  score: number | null
  level: IndexLevel | null
  /** AI 一句话结论，替代等级文字单独展示 */
  summary: string | null
  onExplain?: () => void
}

export function EnergyScoreCard({ score, level, summary, onExplain }: Props) {
  return (
    <View className="score-card">
      <View className="score-card__ring">
        <Text className="score-card__leaf">🍃</Text>
      </View>
      <View className="score-card__body">
        <View className="score-card__head">
          <Text className="score-card__label">新能源环境指数</Text>
          <Text className="score-card__help" onClick={onExplain}>?</Text>
        </View>
        {score === null ? (
          <Text className="score-card__empty">数据获取中</Text>
        ) : (
          <View className={`score-card__value score-card__value--${level ?? 'poor'}`}>
            <Text className="score-card__num">{Math.round(score)}</Text>
            <Text className="score-card__unit">分</Text>
          </View>
        )}
        {summary && <Text className="score-card__summary">{summary}</Text>}
      </View>
      <View className="score-card__slogan">
        <Text>绿电同行</Text>
        <Text>共建零碳未来</Text>
      </View>
    </View>
  )
}
