import { View, Text } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { Icon, PageTitleBar } from '@/components'
import type { IconName } from '@/components'
import { mockStations } from '@/mocks'
import './index.scss'

/**
 * 「我的」页面 —— docs/01 §二 标注为待出设计稿。
 * 此处按底部导航已占位的事实做一个合理实现，设计稿到位后重做。
 */

interface Row {
  icon: IconName
  tone: string
  label: string
  value?: string
  onTap?: () => void
}

export default function Mine() {
  const groups: { title: string; rows: Row[] }[] = [
    {
      title: '站点',
      rows: [
        { icon: 'lineChart', tone: '#1677ff', label: '我的站点',
          value: `${mockStations.length} 个`,
          onTap: () => Taro.switchTab({ url: '/pages/station/index' }) },
        { icon: 'bell', tone: '#f59e0b', label: '预警通知', value: '已开启' },
      ],
    },
    {
      title: '数据',
      rows: [
        { icon: 'sun', tone: '#f97316', label: '单位与精度' },
        { icon: 'coins', tone: '#16a34a', label: '上网电价', value: '0.4 元/kWh' },
      ],
    },
    {
      title: '关于',
      rows: [
        { icon: 'fileText', tone: '#8b5cf6', label: '隐私保护指引' },
        { icon: 'helpCircle', tone: '#6b7280', label: '关于 EnerSight', value: 'v0.1.0' },
      ],
    },
  ]

  return (
    <View className="mine">
      <PageTitleBar title="我的" />

      <View className="mine__body">
        <View className="mine__profile">
          <View className="mine__avatar">
            <Icon name="user" size={24} color="#ffffff" />
          </View>
          <View className="mine__profile-text">
            <Text className="mine__profile-name">未登录</Text>
            <Text className="mine__profile-sub">登录后可同步站点与预警设置</Text>
          </View>
          <Icon name="chevronRight" size={15} color="#9ca3af" />
        </View>

        {groups.map((g) => (
          <View className="mine__group" key={g.title}>
            <Text className="mine__group-title">{g.title}</Text>
            <View className="mine__card">
              {g.rows.map((r) => (
                <View className="mine__row" key={r.label} onClick={r.onTap}>
                  <View className="mine__row-icon" style={{ background: `${r.tone}1f` }}>
                    <Icon name={r.icon} size={15} color={r.tone} />
                  </View>
                  <Text className="mine__row-label">{r.label}</Text>
                  {r.value && <Text className="mine__row-value">{r.value}</Text>}
                  <Icon name="chevronRight" size={13} color="#9ca3af" />
                </View>
              ))}
            </View>
          </View>
        ))}

        <Text className="mine__note">设计稿未出，本页为占位实现</Text>
      </View>
    </View>
  )
}
