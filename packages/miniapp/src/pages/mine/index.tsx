import { useAppShare } from '@/hooks/useAppShare'
import { View, Text, Button } from '@tarojs/components'
import Taro, { useDidShow } from '@tarojs/taro'
import { useState } from 'react'
import { ApiError } from '@enersight/core/api'
import { Icon, PageTitleBar } from '@/components'
import { meApi } from '@/api/me'
import { stationsApi } from '@/api/stations'
import { useStationStore } from '@/store'
import { useAuthStore } from '@/store/auth'
import { requireLogin } from '@/utils/requireLogin'
import './index.scss'

/**
 * 「我的」：身份、自建电站入口、本机记录、数据与隐私、帮助、关于。docs/17 §一、docs/09 §4.3
 * 游客可浏览公开数据；我的电站需要登录，登录后可退出登录或删除我的数据。
 */
export default function Mine() {
  useAppShare()
  const { recent, favorites, clearRecent } = useStationStore()
  const { loggedIn, logout } = useAuthStore()
  const [mineCount, setMineCount] = useState<number | null>(null)
  useDidShow(() => {
    if (!useAuthStore.getState().loggedIn) { setMineCount(null); return }
    stationsApi.mine().then((d) => setMineCount(d.counts.all)).catch(() => setMineCount(null))
  })
  const info = (kind: string) => Taro.navigateTo({ url: `/pages/info/index?kind=${kind}` })
  const goMine = () => {
    if (!requireLogin()) return
    Taro.setStorageSync('enersight_station_tab', 'mine')
    void Taro.switchTab({ url: '/pages/station/index' })
  }
  const addStation = () => { if (requireLogin()) void Taro.navigateTo({ url: '/pages/station/form' }) }
  const clear = async () => {
    if (!recent.length) return
    const answer = await Taro.showModal({ title: '清除最近浏览', content: '仅移除本机浏览记录，当前电站和自建电站不受影响。', confirmText: '清除' })
    if (answer.confirm) { clearRecent(); void Taro.showToast({ title: '已清除', icon: 'success' }) }
  }
  const privacy = async () => {
    try { await Taro.openPrivacyContract() } catch { void Taro.showModal({ title: '暂时无法打开', content: '请稍后重试。你也可以先查看“数据使用说明”。', showCancel: false }) }
  }
  const signOut = async () => {
    const answer = await Taro.showModal({ title: '退出登录', content: '退出后仍可浏览公开电站，我的电站需重新登录后查看。', confirmText: '退出' })
    if (!answer.confirm) return
    logout()
    setMineCount(null)
    void Taro.showToast({ title: '已退出登录', icon: 'none' })
  }
  const deleteData = async () => {
    const answer = await Taro.showModal({ title: '删除我的数据', content: '将删除本账号的全部自建电站及其预警、发电记录、分析报告与预测留档，删除后不可恢复，并退出登录。', confirmText: '删除', confirmColor: '#b91c1c' })
    if (!answer.confirm) return
    void Taro.showLoading({ title: '正在删除', mask: true })
    try {
      await meApi.deleteData()
    } catch (e) {
      Taro.hideLoading()
      void Taro.showToast({ title: e instanceof ApiError ? e.message : '删除失败，请稍后重试', icon: 'none' })
      return
    }
    Taro.hideLoading()
    logout()
    setMineCount(null)
    void Taro.showToast({ title: '已删除', icon: 'success' })
  }
  let version = '开发版'
  try { version = Taro.getAccountInfoSync().miniProgram.version || '开发版' } catch { /* 本地调试 */ }
  const row = (label: string, onTap: () => void, value?: string) => <View key={label} className="mine__row" hoverClass="pressed" onClick={onTap}>
    <Text className="mine__row-label">{label}</Text>{value && <Text className="mine__row-value">{value}</Text>}<Icon name="chevronRight" size={16} color="#64748b" />
  </View>
  const copyContext = () => Taro.setClipboardData({ data: `产品：晴川观象\n版本：${version}\n当前电站：${useStationStore.getState().currentId || '目录示例电站'}\n登录态：${loggedIn ? '已登录' : '未登录'}\n时间：${new Date().toISOString()}\n问题描述：` })
  return <View className="mine"><PageTitleBar title="我的" /><View className="mine__body">
    <View className="mine__identity" hoverClass={loggedIn ? 'none' : 'pressed'} onClick={loggedIn ? undefined : () => void Taro.navigateTo({ url: '/pages/login/index' })}>
      <View className="mine__avatar"><Icon name="user" size={26} color="#1264d6" strokeWidth={1.6} /></View>
      <View className="mine__identity-text">
        <Text className="mine__brand">{loggedIn ? '微信用户' : '未登录'}</Text>
      </View>
      {!loggedIn && <View className="mine__login"><Text>登录</Text><Icon name="chevronRight" size={16} color="#1264d6" /></View>}
    </View>
    <View><Text className="mine__group-title">我的电站</Text><View className="mine__card">
      {row('我的电站', goMine, !loggedIn ? '登录后查看' : mineCount == null ? undefined : `${mineCount} 座`)}
      {row('添加电站', addStation)}
    </View></View>
    <View><Text className="mine__group-title">浏览记录</Text><View className="mine__card">
      {row('常看电站', () => Taro.switchTab({ url: '/pages/station/index' }), favorites.length ? `${favorites.length} 座` : '暂无收藏')}
      <View className="mine__row" hoverClass="pressed" onClick={clear}><Text className="mine__row-label">清除最近浏览</Text><Text className="mine__row-value">{recent.length ? `${recent.length} 条` : '暂无记录'}</Text></View>
    </View></View>
    <View><Text className="mine__group-title">数据与隐私</Text><View className="mine__card">
      {row('数据来源与估算方法', () => info('sources'))}
      {row('本机记录、位置与登录', () => info('privacy'))}
      {row('用户服务协议', () => info('terms'))}
      {row('微信隐私保护指引', privacy)}
      {loggedIn && row('删除我的数据', deleteData)}
    </View></View>
    <View><Text className="mine__group-title">使用帮助</Text><View className="mine__card">
      {row('使用指南与常见问题', () => info('help'))}
      {row('复制问题反馈信息', copyContext)}
      <Button className="mine__feedback" openType="feedback"><Text className="mine__row-label">提交意见反馈</Text><Icon name="chevronRight" size={16} color="#64748b" /></Button>
    </View></View>
    <View><Text className="mine__group-title">关于</Text><View className="mine__card">
      {row('关于晴川观象', () => info('about'))}
      <View className="mine__row"><Text className="mine__row-label">当前版本</Text><Text className="mine__row-value">{version}</Text></View>
    </View></View>
    {loggedIn && <View className="mine__card"><View className="mine__row mine__row--center" hoverClass="pressed" onClick={signOut}><Text className="mine__signout">退出登录</Text></View></View>}
  </View></View>
}
