import { View, Text, Button } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useState } from 'react'
import { ApiError } from '@enersight/core/api'
import { Icon, PageHeader } from '@/components'
import { useAuthStore } from '@/store/auth'
import './index.scss'

/**
 * 登录页。游客可浏览公开数据；我的电站、数据导出前进入此页。docs/09 §4.3
 * 协议默认不勾选，未勾选点登录先请用户确认；可暂不登录返回。不采集手机号、头像与昵称。
 */
export default function Login() {
  const login = useAuthStore((s) => s.login)
  const [agreed, setAgreed] = useState(false)
  const [busy, setBusy] = useState(false)
  const leave = () => {
    if (Taro.getCurrentPages().length > 1) void Taro.navigateBack()
    else void Taro.switchTab({ url: '/pages/home/index' })
  }
  const submit = async () => {
    if (busy) return
    if (!agreed) {
      const answer = await Taro.showModal({ title: '请阅读并同意', content: '登录前请阅读并同意《用户服务协议》和《隐私保护指引》。', confirmText: '同意', cancelText: '取消' })
      if (!answer.confirm) return
      setAgreed(true)
    }
    setBusy(true)
    try {
      await login()
      void Taro.showToast({ title: '登录成功', icon: 'success' })
      setTimeout(leave, 500)
    } catch (e) {
      void Taro.showToast({ title: e instanceof ApiError ? e.message : '登录失败，请稍后重试', icon: 'none' })
    } finally {
      setBusy(false)
    }
  }
  const privacy = async () => {
    try { await Taro.openPrivacyContract() } catch { void Taro.navigateTo({ url: '/pages/info/index?kind=privacy' }) }
  }
  return <View className="login">
    <PageHeader title="登录" />
    <View className="login__body">
      <View className="login__brand"><Icon name="user" size={32} color="#1264d6" strokeWidth={1.6} /></View>
      <Text className="login__title">登录晴川观象</Text>
      <Text className="login__purpose">登录后可添加和管理自建电站、导出预测数据</Text>
      {/* 不用 disabled：H5 会渲染 disabled="false" 命中 [disabled] 样式；重复点击由 busy 拦截 */}
      <Button className={`login__primary ${busy ? 'login__primary--busy' : ''}`} onClick={submit}>{busy ? '正在登录…' : '微信一键登录'}</Button>
      <View className="login__agreement">
        <View className="login__agree" onClick={() => setAgreed((v) => !v)}>
          <View className={`login__check ${agreed ? 'login__check--on' : ''}`} />
          <Text>我已阅读并同意</Text>
        </View>
        <Text className="login__link" onClick={() => Taro.navigateTo({ url: '/pages/info/index?kind=terms' })}>《用户服务协议》</Text>
        <Text>和</Text>
        <Text className="login__link" onClick={privacy}>《隐私保护指引》</Text>
      </View>
      <View className="login__skip" hoverClass="pressed" onClick={leave}><Text>暂不登录</Text></View>
    </View>
  </View>
}
