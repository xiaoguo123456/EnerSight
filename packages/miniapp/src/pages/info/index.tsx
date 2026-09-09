import { View, Text } from '@tarojs/components'
import { useRouter } from '@tarojs/taro'
import { PageHeader } from '@/components'
import './index.scss'

const CONTENT: Record<string, { title: string; sections: [string, string][] }> = {
  sources: { title: '数据与计算说明', sections: [
    ['公开电站', '电站信息来自 Global Energy Monitor 与 WRI Global Power Plant Database（CC BY 4.0）。目录信息存在更新周期，运营状态不代表实时设备健康。原始外文名称与地区在资料不完整时予以保留。'],
    ['气象与卫星', '气象使用 Open-Meteo 预报，卫星影像来自 Himawari。不同数据有各自的观测和更新时间；区域覆盖、云图渲染与上游可用性可能不同。'],
    ['发电适宜度', '指数基于气象条件估算，光伏主要参考辐射、温度等，风电主要参考风速等。指数不是设备健康评分，也不是电网调度指令。'],
    ['发电量与收益', '发电量为模型估算，未接入电站电表实测；收益基于模型电价假设，不代表实际结算。缺失数据以“—”展示，未积累的历史记录不补成零。'],
    ['预警与分析', '当前未触发预警仅表示没有命中本次监测规则，不保证未来没有异常。分析报告会标明规则分析或 AI 辅助分析，结论用于气象参考。'],
  ] },
  privacy: { title: '数据使用说明', sections: [
    ['登录与接口', '小程序通过微信登录进行身份校验，后端使用登录凭证保护接口访问。'],
    ['本机记录', '当前电站和最近浏览保存在本机，用于恢复查看位置。你可以在设置页清除最近浏览。'],
    ['位置信息', '使用地图定位功能时会请求位置授权，经纬度用于展示所在区域的地图和气象信息。拒绝定位后仍可通过目录搜索电站。'],
    ['微信隐私保护指引', '平台正式隐私保护指引可在设置页通过“微信隐私指引”打开。本页是当前功能的数据使用说明，具体授权以微信展示的指引为准。'],
  ] },
  about: { title: '关于晴川观象', sections: [
    ['晴川观象', '面向光伏与风电场景的公开电站、气象趋势与卫星观测工具。所有用户共享平台电站目录，选择即可查看。'],
    ['使用建议', '先在电站目录查找目标电站，再查看气象、趋势与预警。发电分析为气象推算，请结合真实设备数据判断。'],
    ['反馈', '遇到数据或使用问题，可返回设置页使用微信“意见反馈”，并说明相关电站和发生时间。'],
  ] },
}

export default function Info() {
  const { params } = useRouter()
  const data = CONTENT[params.kind ?? 'about'] ?? CONTENT.about!
  return <View className="info-page"><PageHeader title={data.title} /><View className="info-page__body">
    {data.sections.map(([title, text]) => <View key={title} className="info-page__section"><Text className="info-page__title">{title}</Text><Text className="info-page__text">{text}</Text></View>)}
  </View></View>
}
