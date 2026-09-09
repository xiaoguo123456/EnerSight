import { View, Text } from '@tarojs/components'
import type { ApiError } from '@enersight/core/api'
import { Icon, type IconName } from '../Icon'
import './index.scss'

/** 骨架屏：加载中不要转圈，用内容形状占位 */
export function Skeleton({ lines = 3, height = 120 }: { lines?: number; height?: number }) {
  return (
    <View className="skeleton" style={{ minHeight: `${height}px` }}>
      {Array.from({ length: lines }).map((_, i) => (
        <View className="skeleton__line" key={i} style={{ width: `${88 - i * 18}%` }} />
      ))}
    </View>
  )
}

interface EmptyProps {
  icon?: IconName
  title: string
  description?: string
  actionText?: string
  onAction?: () => void
}

export function EmptyState({ icon = 'clipboard', title, description, actionText, onAction }: EmptyProps) {
  return (
    <View className="state-view">
      <View className="state-view__icon">
        <Icon name={icon} size={28} color="#94a3b8" strokeWidth={1.8} />
      </View>
      <Text className="state-view__title">{title}</Text>
      {description && <Text className="state-view__desc">{description}</Text>}
      {actionText && (
        <View className="state-view__btn" hoverClass="pressed" onClick={onAction}>
          <Text>{actionText}</Text>
        </View>
      )}
    </View>
  )
}

/**
 * 错误态。503 DATA_UNAVAILABLE 是「该位置本就没数据」，重试无用，按空态展示。
 * docs/06 §十三
 */
export function ErrorState({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  if (error.code === 'DATA_UNAVAILABLE') {
    return <EmptyState icon="cloud" title="该区域暂无数据" description={error.message} />
  }
  return (
    <EmptyState
      icon="alertTriangle"
      title={error.status === 0 ? '网络连接失败' : '加载失败'}
      description={error.message}
      actionText="重试"
      onAction={onRetry}
    />
  )
}
