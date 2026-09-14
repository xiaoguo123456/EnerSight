import { useRouter } from '@tarojs/taro'
import Mine, { SETTINGS_TITLES, type SettingsGroup } from './index'

/** 低频管理操作进入二级页，返回后保留「我的」页面状态。 */
export default function MineSettings() {
  const { params } = useRouter()
  const group = Object.prototype.hasOwnProperty.call(SETTINGS_TITLES, params.group ?? '') ? params.group as SettingsGroup : 'privacy'
  return <Mine group={group} />
}
