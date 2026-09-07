/**
 * 首页 mock 数据。
 *
 * ⚠️ 仅供接口落地前预览页面效果使用，接入 GET /v1/home 后删除本文件。
 * 数值取自合理范围，不是设计稿里的示意值 —— 设计稿数字不作为对账依据。
 */
import type { StationStatus } from '@enersight/core/types'

export const mockHome = {
  station: {
    name: '苏州光伏站',
    status: 'normal' as StationStatus,
    address: '江苏省苏州市吴中区',
    latitude: 31.3,
    longitude: 120.62,
  },
  index: {
    score: 82,
    level: 'good' as const,
    summary: '今日适宜发电，下午存在轻度云层影响',
  },
  weather: {
    temperature: { value: 32, delta_percent: null },
    weather_text: '晴转多云',
    wind_speed: { value: 4.5, delta_percent: 12 },
    cloud_cover: { value: 35, delta_percent: -8 },
    radiation: { value: 620, delta_percent: 6 },
  },
  alert: {
    title: '14:30后云量增加，预计辐射下降20%',
    description: '受上游云系影响，下午云量将逐步增加，可能对发电量产生一定影响，请提前做好发电计划调整。',
  },
}
