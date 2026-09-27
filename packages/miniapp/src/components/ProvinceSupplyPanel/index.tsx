import { Button, Text, View } from '@tarojs/components'
import { formatEnergy } from '@enersight/core/format'
import type { FleetDay } from '@enersight/core/types'
import { Icon } from '../Icon'
import './index.scss'

function value(kwh: number) { const v = formatEnergy(kwh); return `${v.value} ${v.unit}` }

/** 省级公开目录供给排序；点击地图只改变视野，不声称是全省真实出力。 */
export function ProvinceSupplyPanel({ day, onMap, onProvince }: { day: FleetDay; onMap?: () => void; onProvince?: (province: string) => void }) {
  const rows = [...(day.regions ?? [])].filter(r => r.province !== '地区待补充').sort((a, b) => b.energy_kwh - a.energy_kwh)
  const max = rows[0]?.energy_kwh ?? 1
  if (!rows.length) return null
  return <View className="province-supply">
    <View className="province-supply__head"><View className="province-supply__title"><Icon name="map" size={17} color="#16a34a" /><Text>省级新能源供给</Text></View>{onMap && <Button className="province-supply__action" onClick={onMap}>地图查看</Button>}</View>
    <Text className="province-supply__caption">{day.date.slice(5).replace('-', '/')} · 平台公开目录估算</Text>
    {rows.slice(0, 6).map((row, index) => <View className="province-supply__row" key={row.province} onClick={() => onProvince?.(row.province)}>
      <Text className="province-supply__rank">{index + 1}</Text><View className="province-supply__name"><Text>{row.province}</Text><View className="province-supply__bar"><View style={{ width: `${Math.max(5, row.energy_kwh / max * 100)}%` }} /></View></View><Text className="province-supply__value">{value(row.energy_kwh)}</Text><Icon name="chevronRight" size={13} color="#9ca3af" />
    </View>)}
    {rows.length > 6 && <Text className="province-supply__more">还有 {rows.length - 6} 个地区 · 点击进入地图</Text>}
  </View>
}
