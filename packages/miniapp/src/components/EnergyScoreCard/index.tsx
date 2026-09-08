import { View, Text } from '@tarojs/components'
import type { IndexLevel } from '@enersight/core/types'
import { Icon } from '../Icon'
import './index.scss'

interface Props {
  /** 0-100。null 表示不可算，展示「数据获取中」而非 0 分 */
  score: number | null
  level: IndexLevel | null
  /** AI 一句话结论 */
  summary: string | null
  onExplain?: () => void
}

const LEVEL_TEXT: Record<IndexLevel, string> = {
  excellent: '优秀', good: '良好', fair: '一般', poor: '较差',
}

/**
 * 页面 Hero：环境指数。
 *
 * 这是全页唯一的大字，52px，其他任何数字都不能接近它。
 * 层级靠尺寸差拉开，不靠加边框加阴影。
 */
export function EnergyScoreCard({ score, level, summary, onExplain }: Props) {
  const lv = level ?? 'poor'
  return (
    <View className={`hero hero--${lv}`}>
      <View className="hero__head" onClick={onExplain}>
        <Text className="hero__label">新能源环境指数</Text>
        <Icon name="helpCircle" size={13} color="rgba(17,24,39,.35)" />
      </View>

      {score === null ? (
        <Text className="hero__empty">数据获取中</Text>
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

      {summary && <Text className="hero__summary">{summary}</Text>}

      <View className="hero__deco">
        <Icon name="leaf" size={96} color="rgba(22,163,74,.10)" fill="rgba(22,163,74,.06)" strokeWidth={1.2} />
      </View>
    </View>
  )
}
