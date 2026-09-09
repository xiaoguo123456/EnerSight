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
  let version = '开发版'
  try { version = Taro.getAccountInfoSync().miniProgram.version || '开发版' } catch { /* 本地调试 */ }
  const row = (label: string, onTap: () => void) => <View key={label} className="mine__row" onClick={onTap}><Text className="mine__row-label">{label}</Text><Icon name="chevronRight" size={16} color="#64748b" /></View>
  const copyContext = () => Taro.setClipboardData({ data: `产品：晴川观象\n版本：${version}\n当前电站：${useStationStore.getState().currentId || '目录示例电站'}\n时间：${new Date().toISOString()}\n问题描述：` })
  return <View className="mine"><PageTitleBar title="设置" /><View className="mine__body">
    <View className="mine__identity"><Text className="mine__brand">晴川观象</Text><Text className="mine__description">公开电站 · 气象趋势 · 卫星观测</Text></View>
    <View><Text className="mine__group-title">使用帮助</Text><View className="mine__card">
      {row('使用指南与常见问题', () => info('help'))}
      {row('复制问题反馈信息', copyContext)}
      <Button className="mine__feedback" openType="feedback"><Text className="mine__row-label">提交意见反馈</Text><Icon name="chevronRight" size={16} color="#64748b" /></Button>
    </View></View>
    <View><Text className="mine__group-title">数据与隐私</Text><View className="mine__card">
      {row('数据来源与估算方法', () => info('sources'))}
      {row('本机记录与位置使用', () => info('privacy'))}
      {row('微信隐私保护指引', privacy)}
      <View className="mine__row" onClick={clear}><Text className="mine__row-label">清除最近浏览</Text><Text className="mine__row-value">{recent.length ? `${recent.length} 条` : '暂无记录'}</Text></View>
    </View></View>
    <View><Text className="mine__group-title">关于</Text><View className="mine__card">
      {row('关于晴川观象', () => info('about'))}
      <View className="mine__row"><Text className="mine__row-label">当前版本</Text><Text className="mine__row-value">{version}</Text></View>
    </View></View>
    <Text className="mine__note">气象推算供参考，非电站实时运行数据</Text>
  </View></View>
}
