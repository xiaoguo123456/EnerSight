import { View, Text } from '@tarojs/components'
import Taro, { useRouter } from '@tarojs/taro'
import { PageHeader } from '@/components'
import './index.scss'

const CONTENT: Record<string, { title: string; sections: [string, string][] }> = {
  help: { title: '使用指南与常见问题', sections: [
    ['怎样选择电站？', '在目录搜索名称、地区或业主，打开资料后点击“设为当前电站”。仅浏览资料不会改变首页和预警的电站。收藏后可从目录的“常看电站”快速找到，最多保留 20 座。'],
    ['怎样刷新数据？', '首页、详情、预警和报告支持下拉刷新，也可点击“刷新数据”。离开页面超过一分钟再返回会重新请求；上游数据仍按各自周期更新，刷新不代表获得新的观测。'],
    ['评分低就是设备故障吗？', '不是。发电适宜度只评估气象条件，不含实时设备状态。未触发预警也不意味着发电条件良好。'],
    ['图片或曲线加载失败怎么办？', '点击对应模块的重新加载。过期数据和失败状态会明确标注；加载失败的影像不能用来判断是否有云。'],
    ['如何反馈或纠错？', '在详情复制电站资料，再点击“资料纠错”；也可在设置复制反馈信息后提交意见反馈，补充现象、时间和需要核对的字段。'],
  ] },
  sources: { title: '数据与计算说明', sections: [
    ['公开电站', '电站信息来自 Global Energy Monitor 与 WRI Global Power Plant Database（CC BY 4.0）。目录信息存在更新周期，运营状态不代表实时设备健康。原始外文名称与地区在资料不完整时予以保留。'],
    ['气象与卫星', '气象使用 Open-Meteo 预报，卫星影像来自 Himawari。不同数据有各自的观测和更新时间；区域覆盖、云图渲染与上游可用性可能不同。'],
    ['更新与时间', '当前气象与逐小时预报使用约 10 分钟接口缓存，预报本身按上游模型周期更新；卫星通常按 10 分钟帧序列获取，可能延迟或缺帧。当天报告访问时超过 10 分钟会重新生成。界面更新时间统一标注北京时间；24 小时趋势按电站当地日分段。目录更新时间是平台入库时间，不等于原始数据发布日。'],
    ['覆盖范围', '公开目录可能缺中文名、业主或地区。Himawari 为静止卫星，其视野不覆盖全球；夜间使用红外，低暖云识别存在限制。无图或无数据时不作晴空判断。'],
    ['估算示例', '收益估算 = 发电量估算 × 电价假设。例如按 100 kWh 与 0.4 元/kWh 假设，结果为 40 元；这只是公式示例，不是某座电站的实际收益。每份新报告展示其使用的电价与减排系数。'],
    ['发电适宜度', '指数基于气象条件估算，光伏主要参考辐射、温度等，风电主要参考风速等。指数不是设备健康评分，也不是电网调度指令。'],
    ['发电量与收益', '发电量为模型估算，未接入电站电表实测；收益基于模型电价假设，不代表实际结算。缺失数据以“—”展示，未积累的历史记录不补成零。'],
    ['预警与分析', '当前未触发预警仅表示没有命中本次监测规则，不保证未来没有异常。分析报告会标明规则分析或 AI 辅助分析，结论用于气象参考。'],
  ] },
  privacy: { title: '数据使用说明', sections: [
    ['登录与接口', '小程序通过微信登录进行身份校验，后端使用登录凭证保护接口访问。'],
    ['本机记录', '当前电站、最近浏览和常看收藏保存在本机，用于恢复查看位置。你可以在设置页清除最近浏览。'],
    ['位置信息', '地图以所选电站或搜索位置显示气象。地图的回位按钮回到当前电站，并非自动获取你的位置；如微信请求定位权限，可拒绝后继续使用目录。'],
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
  const sources = [
    ['Global Energy Monitor', 'https://globalenergymonitor.org/'],
    ['WRI 全球电站数据库', 'https://datasets.wri.org/datasets/global-power-plant-database'],
    ['Open-Meteo', 'https://open-meteo.com/'],
    ['日本气象厅 Himawari', 'https://www.data.jma.go.jp/mscweb/en/himawari89/'],
  ]
  return <View className="info-page"><PageHeader title={data.title} /><View className="info-page__body">
    {data.sections.map(([title, text]) => <View key={title} className="info-page__section"><Text className="info-page__title">{title}</Text><Text className="info-page__text">{text}</Text></View>)}
    {params.kind === 'sources' && <View className="info-page__section"><Text className="info-page__title">原始资料入口</Text>{sources.map(([name, url]) => <View key={url} className="info-page__source" onClick={() => Taro.setClipboardData({ data: url! })}><Text>{name}</Text><Text>复制链接</Text></View>)}</View>}
  </View></View>
}
