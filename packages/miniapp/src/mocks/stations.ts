import type { StationStatus, StationType } from '@enersight/core/types'

export interface MockStation {
  id: string
  name: string
  type: StationType
  status: StationStatus
  capacity: number // kW
  address: string
  latitude: number
  longitude: number
  metrics: {
    daily_generation: number | null // kWh
    current_power: number | null // kW
    total_generation: number | null // kWh
    co2_reduction: number | null // kg
  }
}

export const mockStations: MockStation[] = [
  {
    id: 's1', name: '苏州光伏站', type: 'solar', status: 'normal',
    capacity: 500, address: '江苏省苏州市吴中区',
    latitude: 31.3, longitude: 120.62,
    metrics: {
      daily_generation: 1680, current_power: 222,
      total_generation: 1_260_000, co2_reduction: 1_008_000,
    },
  },
  {
    id: 's2', name: '广东风电站', type: 'wind', status: 'normal',
    capacity: 2000, address: '广东省阳江市阳西县',
    latitude: 21.75, longitude: 111.95,
    metrics: {
      daily_generation: 11_520, current_power: 1420,
      total_generation: 12_800_000, co2_reduction: 10_240_000,
    },
  },
]
