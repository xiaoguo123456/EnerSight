export default defineAppConfig({
  pages: [
    'pages/home/index',
    'pages/map/index',
    'pages/alert/index',
    'pages/station/index',
    'pages/mine/index',
    'pages/station/detail',
    'pages/report/index',
  ],
  window: {
    backgroundTextStyle: 'light',
    navigationStyle: 'custom',
    backgroundColor: '#F5F9FC',
  },
  tabBar: {
    color: '#9CA3AF',
    selectedColor: '#1677FF',
    backgroundColor: '#FFFFFF',
    borderStyle: 'white',
    list: [
      { pagePath: 'pages/home/index', text: '首页' },
      { pagePath: 'pages/map/index', text: '地图' },
      { pagePath: 'pages/alert/index', text: '预警' },
      { pagePath: 'pages/station/index', text: '站点' },
      { pagePath: 'pages/mine/index', text: '我的' },
    ],
  },
  // 位置接口必须在此声明，且需在 mp 后台「开发管理 → 接口设置」申请开通。
  // 两者缺一不可。见 docs/09 §五
  requiredPrivateInfos: ['getLocation', 'chooseLocation'],
  permission: {
    'scope.userLocation': {
      desc: '用于定位当前区域的气象数据与添加站点坐标',
    },
  },
})
