import { View, Text, Button } from '@tarojs/components'
import Taro, { useRouter, usePullDownRefresh } from '@tarojs/taro'
import { useEffect } from 'react'
import {
  formatBeijingTime, formatCoordinate, formatPercent, formatPower, formatRadiation,
  formatTemperature, formatWindSpeed,
} from '@enersight/core/format'
import type { StationDetailResponse } from '@enersight/core/types'
import { useStationStore } from '@/store'
import { homeApi } from '@/api/home'
import {
  EnergyScoreCard, ErrorState, Icon, MetricCard, MetricGrid, PageHeader,
  SectionHeader, Skeleton, StatusBadge,
} from '@/components'
import { useRequest } from '@/hooks/useRequest'
import { decodeRouteParam } from '@/route'
import { StationTrend } from '@/components/StationTrend'
import { useWeatherModel, weatherModelLabel } from '@/store/weatherModel'
import { DataFreshness } from '@/components/DataFreshness'
import './detail.scss'

const WEEKDAY_TEXT = (days: number[] | undefined) => !days?.length ? '每天' : days.join(',') === '1,2,3,4,5' ? '工作日' : days.join(',') === '6,7' ? '周末' : `周${days.map((d) => '一二三四五六日'[d - 1]).join('')}`
const hh = (h: number) => `${String(h).padStart(2, '0')}:00`
/** 头部一句话：限电 15% / 每天 11–14 时上限 60%（共 2 条） */
function constraintSummary(rule: NonNullable<StationDetailResponse['station']['curtailment']>) {
  if (rule.mode === 'ratio') return `限电 ${rule.ratio_percent}%`
  const w = rule.windows?.[0]
  if (!w) return '分时段限电'
  return `${WEEKDAY_TEXT(w.weekdays)} ${hh(w.start_hour)}–${hh(w.end_hour)} 上限 ${w.limit_percent}%${(rule.windows?.length ?? 0) > 1 ? `（共 ${rule.windows!.length} 条）` : ''}`
}

export default function StationDetail() {
  const { params } = useRouter()
  const routeId = decodeRouteParam(params.id)
  // 没带 id（含开发期直接作为启动页）时退回默认站点，与首页一致
  const req = useRequest(
    () => (routeId ? homeApi.detail(routeId) : homeApi.detailDefault()),
    [routeId],
  )
  const id = req.data?.station.id ?? routeId
  const remember = useStationStore((s) => s.remember)
  useEffect(() => { if (req.data?.station) remember(req.data.station) }, [req.data, remember])
  const setCurrent = useStationStore((s) => s.setCurrent)
  const favorites = useStationStore((s) => s.favorites)
  const toggleFavorite = useStationStore((s) => s.toggleFavorite)
  const currentId = useStationStore((s) => s.currentId)
  usePullDownRefresh(async () => { await req.reload(); Taro.stopPullDownRefresh() })
  const selectStation = () => { setCurrent(id); void Taro.showToast({ title: '已设为当前电站', icon: 'success' }) }

  if (req.status === 'loading') {
    return (
      <View className="detail">
        <PageHeader title="电站详情" />
        <View className="detail__body">
          <Skeleton height={110} lines={3} />
          <Skeleton height={130} lines={2} />
          <Skeleton height={150} lines={3} />
        </View>
      </View>
    )
  }
  if (req.status === 'error') {
    return (
      <View className="detail">
        <PageHeader title="电站详情" />
        <View className="detail__body"><ErrorState error={req.error} onRetry={req.reload} /></View>
      </View>
    )
  }

  const { station, weather, index, updated_at } = req.data
  const cap = formatPower(station.capacity)

  return (
    <View className="detail">
      <PageHeader title="电站详情" />

      <View className="detail__body">
        <View className="detail__group">
          <View className="detail__station">
            <View className="detail__station-spec">
              <Icon name={station.type === 'solar' ? 'sun' : 'wind'} size={16} color="#64748b" />
              <Text>{station.type === 'solar' ? '光伏电站' : '风力电站'}</Text>
              <StatusBadge status={station.status} own={station.is_own} />
            </View>
            <Text className="detail__station-name">{station.name}</Text>
            <View className="detail__capacity">
              <Text className="detail__capacity-label">{station.is_own ? '装机容量' : '目录申报容量'}</Text>
              <Text className="detail__capacity-value">{cap.value}<Text className="detail__capacity-unit"> {cap.unit}</Text></Text>
            </View>
            {station.is_own && <View className="detail__chip" hoverClass="pressed" onClick={() => Taro.navigateTo({ url: `/pages/station/form?id=${encodeURIComponent(id)}` })}>
              <Icon name="sliders" size={13} color="#98521a" /><Text>{station.curtailment ? constraintSummary(station.curtailment) : '未设置限电规则'}</Text><Icon name="chevronRight" size={12} color="#98521a" />
            </View>}
            <View className="detail__favorite" onClick={() => toggleFavorite(station)}>{favorites.some((s) => s.id === id) ? '已收藏 · 点击取消' : '收藏到常看电站'}</View>
            <View className="detail__selection" onClick={selectStation}>{currentId === id ? '✓ 当前查看电站' : '设为当前电站'}</View>
            <View className="detail__actions">
              <View className="detail__action detail__action--primary" onClick={() => Taro.navigateTo({ url: `/pages/report/index?id=${encodeURIComponent(id)}` })}>
                <Icon name="fileText" size={16} color="#1264d6" /><Text>分析报告</Text>
              </View>
              <View className="detail__action" onClick={() => { setCurrent(id); Taro.switchTab({ url: '/pages/map/index' }) }}>
                <Icon name="map" size={16} color="#475569" /><Text>地图查看</Text>
              </View>
              {station.is_own && <View className="detail__action" onClick={() => Taro.navigateTo({ url: `/pages/station/form?id=${encodeURIComponent(id)}` })}>
                <Icon name="pencil" size={16} color="#475569" /><Text>编辑</Text>
              </View>}
            </View>
          </View>

          <EnergyScoreCard
            score={index?.score ?? null}
            level={index?.level ?? null}
            summary={index?.summary ?? null}
          />
        </View>

        <DataFreshness label={`${weatherModelLabel(useWeatherModel.getState().model)}${req.data.basis?.issued_at ? ` · 起报 ${formatBeijingTime(req.data.basis.issued_at)}` : ''}`} timeLabel="数据至 " time={updated_at} refreshing={req.refreshing} failed={!!req.refreshError} onRefresh={req.reload} />
        <View className="detail__card"><StationTrend key={id} stationId={id} type={station.type} initial={req.data.trends} /></View>

        {weather && (
          <View className="detail__card">
            <View className="detail__weather-head">
              <SectionHeader icon="sun" iconColor="#f59e0b" title="当前气象" />

            </View>
            <MetricGrid>
              <MetricCard icon="cloudSun" label="气温"
                metric={formatTemperature(weather.temperature.value)}
                caption={weather.weather_text ?? undefined} />
              <MetricCard icon="wind" iconFill={false} label="风速"
                metric={formatWindSpeed(weather.wind_speed.value)}
                deltaPercent={weather.wind_speed.delta_percent} />
              <MetricCard icon="cloud" label="云量"
                metric={formatPercent(weather.cloud_cover.value)}
                deltaPercent={weather.cloud_cover.delta_percent} />
              <MetricCard icon="sun" label="辐射"
                metric={formatRadiation(weather.radiation.value)}
                deltaPercent={weather.radiation.delta_percent} />
            </MetricGrid>
          </View>
        )}

        {(station.phases?.length || station.prediction_blocked_reason) && <View className="detail__card">
          <SectionHeader icon="fileText" title="分期与容量口径" />
          <Text className="detail__note">{station.capacity_note || '容量口径待核验'}</Text>
          {station.prediction_blocked_reason && <Text className="detail__note">{station.prediction_blocked_reason}</Text>}
          {station.phases?.map(phase => <View key={phase.id || phase.phase_name} className="detail__info-row"><Text className="detail__info-label">{phase.name} · {phase.phase_name}</Text><Text className="detail__info-value">{formatPower(phase.capacity_kw).value} {formatPower(phase.capacity_kw).unit} · {phase.capacity_rating === 'ac' ? '交流' : phase.capacity_rating === 'dc' ? '直流' : station.type === 'wind' ? '额定容量' : '类型未知'}</Text></View>)}
          <Text className="detail__note">来源版本：{station.source_file || '待核验'}。公开分期记录不代表已由场站实测确认。</Text>
        </View>}
        <View className="detail__card">
          <SectionHeader icon="mapPin" title="电站资料" info={{ title: '电站资料', content: station.is_own ? '电站资料由你自行填写，气象与发电适宜度为模型估算。反馈问题前可复制资料，附上需要更正的字段。' : '电站资料来自公开目录，运营状态不代表实时设备健康；气象与发电适宜度为模型估算。资料有误可复制后通过「资料纠错」反馈，附上需要更正的字段和来源。' }} />
          <View className="detail__info-row"><Text className="detail__info-label">所在地区</Text><Text className="detail__info-value">{station.address || '暂无地区信息'}</Text></View>
          <View className="detail__info-row"><Text className="detail__info-label">地理坐标</Text><Text className="detail__info-value">{formatCoordinate(station.latitude, station.longitude)}</Text></View>
          {!station.is_own && <View className="detail__info-row"><Text className="detail__info-label">原始名称</Text><Text className="detail__info-value">{station.original_name || station.name}</Text></View>}
          {station.local_name && <View className="detail__info-row"><Text className="detail__info-label">中文名称</Text><Text className="detail__info-value">{station.local_name}</Text></View>}
          <View className="detail__info-row"><Text className="detail__info-label">资料来源</Text><Text className="detail__info-value">{station.is_own ? '用户自建，仅本账号可见' : station.source === 'gem' ? 'Global Energy Monitor' : station.source === 'wri' ? 'WRI 全球电站数据库' : '来源待核实'}</Text></View>
          {!station.is_own && <View className="detail__info-row"><Text className="detail__info-label">目录入库</Text><Text className="detail__info-value">{formatBeijingTime(station.catalog_updated_at)}（北京时间，非源数据发布日期）</Text></View>}
          {station.owner_name && <View className="detail__info-row"><Text className="detail__info-label">业主</Text><Text className="detail__info-value">{station.owner_name}</Text></View>}
          <View className="detail__actions">
            <View className="detail__action" onClick={() => Taro.setClipboardData({ data: `电站：${station.name}\nID：${id}\n地区：${station.address || '暂无'}\n坐标：${formatCoordinate(station.latitude, station.longitude)}\n来源：${station.is_own ? '用户自建' : station.source || '待核实'}\n页面：电站详情\n反馈时间：${new Date().toISOString()}` })}>复制资料</View>
            <Button className="detail__action detail__feedback" openType="feedback">资料纠错</Button>
          </View>

        </View>
      </View>
    </View>
  )
}
