import type { AlertLevel } from '@enersight/core/types'

export interface MockAlert {
  id: string
  level: AlertLevel
  title: string
  description: string
  published_at: string
}

export const mockAlerts = {
  satellite: {
    band: '卫星云图',
    observed_at: '2026-09-07 14:00',
  },
  current: {
    id: 'a1',
    level: 'minor' as AlertLevel,
    title: '14:30后云量增加，预计辐射下降20%',
    description:
      '受上游云系影响，下午云量将逐步增加，可能对光伏发电产生一定影响，建议提前做好发电计划调整。',
    published_at: '2026-09-07 13:50',
  },
  cloudMotion: {
    distance_km: 80,
    direction: '东北',
    direction_detail: '向东/东北方向移动',
    impact_in_minutes: 45,
    impact_start_time: '14:30',
    reference_station: '距苏州光伏站',
  },
  records: [
    {
      id: 'a1', level: 'minor' as AlertLevel,
      title: '14:30后云量增加，预计辐射下降20%',
      description: '受上游云系影响，可能对光伏发电产生一定影响。',
      published_at: '2026-09-07 13:50',
    },
    {
      id: 'a2', level: 'cleared' as AlertLevel,
      title: '云系逐渐远离，辐射将恢复',
      description: '影响云团已移出周边区域，发电条件逐步转好。',
      published_at: '2026-09-07 09:20',
    },
    {
      id: 'a3', level: 'moderate' as AlertLevel,
      title: '午后可能出现分散云团',
      description: '预计未来2小时内有分散云团过境，可能造成辐射波动。',
      published_at: '2026-09-06 15:10',
    },
  ],
}
