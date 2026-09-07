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
}

/** 横向筛选切换。全圆角胶囊，激活项蓝底白字。docs/03 */
export function SegmentedTabs({ options, value, onChange }: Props) {
  return (
    <View className="seg-tabs">
      {options.map((o) => (
        <View
          key={o.value}
          className={`seg-tabs__item ${o.value === value ? 'seg-tabs__item--active' : ''}`}
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
