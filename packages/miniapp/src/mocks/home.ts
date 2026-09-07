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
  trends: {
    // 25 个点，00:00 – 24:00 逐小时。docs/04「24 小时趋势图数据」
    radiation: {
      unit: 'W/m²', yMax: 1000,
      values: [0, 0, 0, 0, 0, 18, 96, 210, 340, 462, 560, 632, 668, 680,
        652, 596, 512, 408, 290, 168, 62, 8, 0, 0, 0],
    },
    wind_speed: {
      unit: 'm/s', yMax: null,
      values: [2.1, 2.0, 1.8, 1.9, 2.2, 2.6, 3.1, 3.6, 4.0, 4.3, 4.5, 4.8,
        5.1, 5.3, 5.2, 4.9, 4.6, 4.2, 3.8, 3.4, 3.0, 2.7, 2.4, 2.2, 2.1],
    },
    cloud_cover: {
      unit: '%', yMax: 100,
      values: [42, 40, 38, 35, 33, 30, 26, 22, 20, 18, 20, 24, 30, 38,
        48, 58, 66, 70, 68, 62, 55, 50, 46, 44, 42],
    },
  },

  alert: {
    title: '14:30后云量增加，预计辐射下降20%',
    description: '受上游云系影响，下午云量将逐步增加，可能对发电量产生一定影响，请提前做好发电计划调整。',
  },
}
