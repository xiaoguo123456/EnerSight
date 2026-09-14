import { View, Text } from '@tarojs/components'
import './index.scss'

export interface TabOption {
  value: string
  label: string
  /** 带计数变体：全部（2）/ 光伏（1） */
  count?: number
  /** 带状态点变体：预警等级筛选 */
  dotColor?: string
}

interface Props {
  options: TabOption[]
  value: string
  onChange: (value: string) => void
  variant?: 'pill' | 'underline'
}

/** 胶囊用于指标筛选，下划线用于页面范围切换。 */
export function SegmentedTabs({ options, value, onChange, variant = 'pill' }: Props) {
  return (
    <View className={`seg-tabs seg-tabs--${variant}`}>
      {options.map((o) => (
        <View
          key={o.value}
          className={`seg-tabs__item ${o.value === value ? 'seg-tabs__item--active' : ''}`}
          hoverClass="pressed"
          hoverStayTime={60}
          onClick={() => onChange(o.value)}
        >
          {o.dotColor && (
            <View className="seg-tabs__dot" style={{ background: o.dotColor }} />
          )}
          <Text className="seg-tabs__label">
            {o.label}
            {o.count !== undefined && `（${o.count}）`}
          </Text>
        </View>
      ))}
    </View>
  )
}
