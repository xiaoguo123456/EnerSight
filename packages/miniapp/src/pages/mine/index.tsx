import { View, Text, Button } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { Icon, PageTitleBar } from '@/components'
import { useStationStore } from '@/store'
import './index.scss'

export default function Mine() {
  const { recent, clearRecent } = useStationStore()
  const info = (kind: string) => Taro.navigateTo({ url: `/pages/info/index?kind=${kind}` })
  const clear = async () => {
    if (!recent.length) return
    const answer = await Taro.showModal({ title: '清除最近浏览', content: '仅移除本机浏览记录，当前电站和公共目录不受影响。', confirmText: '清除' })
    if (answer.confirm) { clearRecent(); void Taro.showToast({ title: '已清除', icon: 'success' }) }
  }
  const privacy = async () => {
    try { await Taro.openPrivacyContract() } catch { void Taro.showModal({ title: '暂时无法打开', content: '请稍后重试。你也可以先查看“数据使用说明”。', showCancel: false }) }
  }
  const rows = [
    { label: '数据来源与计算说明', icon: 'fileText' as const, onTap: () => info('sources') },
    { label: '数据使用说明', icon: 'helpCircle' as const, onTap: () => info('privacy') },
    { label: '微信隐私指引', icon: 'fileText' as const, onTap: privacy },
    { label: '关于晴川观象', icon: 'sun' as const, onTap: () => info('about') },
  ]
  return <View className="mine"><PageTitleBar title="设置" /><View className="mine__body">
    <View className="mine__identity"><Text className="mine__brand">晴川观象</Text><Text className="mine__description">公开电站 · 气象趋势 · 卫星观测</Text></View>
    <View className="mine__card">{rows.map((r) => <View key={r.label} className="mine__row" onClick={r.onTap}><Icon name={r.icon} size={19} color="#64748b" /><Text className="mine__row-label">{r.label}</Text><Icon name="chevronRight" size={16} color="#64748b" /></View>)}</View>
    <View className="mine__card"><View className="mine__row" onClick={clear}><Icon name="clock" size={19} color="#64748b" /><Text className="mine__row-label">清除最近浏览</Text><Text className="mine__row-value">{recent.length ? `${recent.length} 条` : '暂无记录'}</Text></View>
      <Button className="mine__feedback" openType="feedback"><Icon name="helpCircle" size={19} color="#64748b" /><Text className="mine__row-label">意见反馈</Text><Icon name="chevronRight" size={16} color="#64748b" /></Button>
    </View><Text className="mine__note">气象推算供参考，非电站实时运行数据</Text>
  </View></View>
}
