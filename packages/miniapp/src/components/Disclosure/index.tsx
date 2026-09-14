import type { ReactNode } from 'react'
import { Button, View, Text } from '@tarojs/components'
import { Icon } from '../Icon'
import { InfoTip } from '../InfoTip'
import './index.scss'

/** 折叠不改动业务状态；标题与口径入口分别点击，避免嵌套按钮。 */
export function Disclosure({ title, summary, open, onToggle, info, children, id }: {
  title: string; summary?: string; open: boolean; onToggle: () => void
  info?: { title: string; content: string }; children: ReactNode; id?: string
}) {
  return <View className="disclosure" id={id}>
    <View className="disclosure__head">
      <Button className="disclosure__toggle" ariaLabel={`${open ? '收起' : '展开'}${title}`} onClick={onToggle}>
        <View className="disclosure__text"><Text className="disclosure__title">{title}</Text>{!open && summary && <Text className="disclosure__summary">{summary}</Text>}</View>
        <Icon name={open ? 'chevronUp' : 'chevronDown'} size={16} color="#64748b" />
      </Button>
      {info && <InfoTip {...info} />}
    </View>
    {open && <View className="disclosure__body">{children}</View>}
  </View>
}
