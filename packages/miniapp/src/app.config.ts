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
    // 图标由 Lucide SVG 渲染成 PNG（tabBar 只接受图片文件，不支持 data URI）
    // 生成脚本见 scripts/gen-tabbar-icons.mjs
    list: [
      { pagePath: 'pages/home/index', text: '首页',
        iconPath: 'assets/tabbar/home.png',
        selectedIconPath: 'assets/tabbar/home-active.png' },
      { pagePath: 'pages/map/index', text: '地图',
        iconPath: 'assets/tabbar/map.png',
        selectedIconPath: 'assets/tabbar/map-active.png' },
      { pagePath: 'pages/alert/index', text: '预警',
        iconPath: 'assets/tabbar/bell.png',
        selectedIconPath: 'assets/tabbar/bell-active.png' },
      { pagePath: 'pages/station/index', text: '站点',
        iconPath: 'assets/tabbar/chart.png',
        selectedIconPath: 'assets/tabbar/chart-active.png' },
      { pagePath: 'pages/mine/index', text: '我的',
        iconPath: 'assets/tabbar/user.png',
        selectedIconPath: 'assets/tabbar/user-active.png' },
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
