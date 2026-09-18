import { useAppShare } from '@/hooks/useAppShare'
import { Button, Switch, Text, View } from '@tarojs/components'
import Taro, { usePullDownRefresh, useRouter } from '@tarojs/taro'
import { useEffect, useRef, useState } from 'react'
import { thousands } from '@enersight/core/format'
import type { CorrectionStatus, MeasuredEntry, MeasuredSummary } from '@enersight/core/types'
import { ApiError } from '@enersight/core/api'
import { measuredApi } from '@/api/measured'
import { stationsApi } from '@/api/stations'
import { homeApi } from '@/api/home'
import { ErrorState, Icon, InfoTip, PageHeader, RecordSheet, Skeleton } from '@/components'
import { useRequest } from '@/hooks/useRequest'
import { decodeRouteParam } from '@/route'
import './measured.scss'

const STATUS: Record<MeasuredEntry['status'], string> = {
  used: '采用',
  excluded: '未采用',
  pending: '回算中',
  idle: '未参与',
}
const HELP = [
  '订正拿你记的电量去比同一个气象模型在同一天算出的电量（模型同期），两者之比就是订正系数，之后的预测电量与功率都乘这个系数。',
  '日电量记满 7 天、或月电量记满 2 个月就能开始。日电量只看最近 60 天，跟着季节滚动；日电量不够时用月电量。',
  '启用前先回测：每条记录都用其余记录拟出的系数去修它，订正后的逐日平均误差比订正前至少小 3 个百分点才启用，免得一两天的起伏来回拨动订正；系数偏离太多（不到 0.4 倍或超过 2.5 倍）说明参数本身有误，先核对装机容量与机型。',
  '实测只有模型的两成以下、或超过五倍的记录判为停机或录错，不参与拟合。',
  '订正不改发电适宜度评分（它只反映气象），也不改全目录汇总；预测留档与预报演变仍按模型原始值。',
  '实测记录只有你能看到，删除电站或删除我的数据时一并删除。',
].join('\n\n')

function mwh(kwh: number | null | undefined) {
  if (kwh == null) return '—'
  return `${thousands(kwh / 1000, kwh < 10_000 ? 2 : 1)}`
}

/** Hero：模型偏高 / 偏低多少，按几倍修正。数字优先，说明退后。docs/02 §九 */
function CorrectionCard({ c, onToggle, busy }: { c: CorrectionStatus; onToggle: (on: boolean) => void; busy: boolean }) {
  const gap = c.k ? (1 / c.k - 1) * 100 : null
  const headline = gap == null ? '尚未订正' : `模型${gap > 0 ? '偏高' : '偏低'} ${Math.abs(gap).toFixed(0)}%`
  return (
    <View className="measured__card measured__hero">
      <View className="measured__row">
        <View className="measured__hero-title"><Text>订正状态</Text><InfoTip title="实测订正怎么算" content={HELP} /></View>
        <View className="measured__switch"><Text>{c.enabled ? '已开启' : '已关闭'}</Text><Switch checked={c.enabled} disabled={busy} color="#1677ff" onChange={e => onToggle(!!e.detail.value)} /></View>
      </View>
      <Text className={`measured__headline ${c.applied ? '' : 'measured__headline--idle'}`}>{headline}</Text>
      {c.k != null && <Text className="measured__sub">{`${c.applied ? '按' : '拟合结果'} ${c.k.toFixed(2)} 倍${c.applied ? '修正' : ''} · ${c.sample_count} ${c.method === 'month' ? '个月' : '天'}样本${c.excluded_count ? ` · ${c.excluded_count} 条未采用` : ''}`}</Text>}
      {c.error_before != null && c.error_after != null && <View className="measured__split">
        <View><Text className="measured__label">逐日误差 · 订正前</Text><Text className="measured__num">{`${c.error_before.toFixed(0)}%`}</Text></View>
        <View><Text className="measured__label">订正后</Text><Text className="measured__num">{`${c.error_after.toFixed(0)}%`}</Text></View>
      </View>}
      {!c.applied && c.reason && <Text className="measured__reason">{c.reason}</Text>}
    </View>
  )
}

function EntryRow({ e, onDelete }: { e: MeasuredEntry; onDelete: (e: MeasuredEntry) => void }) {
  return (
    <View className="measured__entry">
      <View className="measured__entry-date">
        <Text>{e.kind === 'month' ? `${e.date} 月` : e.date.slice(5)}</Text>
        <Text className="measured__entry-kind">{e.basis === 'grid' ? '上网' : '发电'}</Text>
      </View>
      <Text className="measured__entry-num">{mwh(e.kwh)}</Text>
      <Text className="measured__entry-num measured__entry-model">{mwh(e.model_kwh)}</Text>
      <Text className={`measured__entry-status measured__entry-status--${e.status}`}>{STATUS[e.status]}</Text>
      <View className="measured__entry-del" hoverClass="pressed" onClick={() => onDelete(e)} ariaLabel="删除这条记录"><Icon name="trash" size={15} color="#94a3b8" /></View>
    </View>
  )
}

/** 实测对账：记下的日 / 月电量、模型同期值与订正状态。只对我的电站。docs/19 §三 */
export default function MeasuredPage() {
  useAppShare()
  const id = decodeRouteParam(useRouter().params.id) ?? ''
  const req = useRequest(() => measuredApi.get(id), [id])
  const detail = useRequest(() => homeApi.detail(id), [id])
  const [override, setOverride] = useState<MeasuredSummary | null>(null)
  const [recording, setRecording] = useState(false)
  const [busy, setBusy] = useState(false)
  usePullDownRefresh(async () => { setOverride(null); await req.reload(); Taro.stopPullDownRefresh() })
  const data = override ?? req.data
  const station = detail.data?.station
  // 回算超过服务端等待上限时先回「回算中」，后台跑完再来取；最多追三次，免得一直轮询
  const polls = useRef(0)
  const pending = !!data?.entries.some(e => e.status === 'pending')
  useEffect(() => {
    if (!pending || polls.current >= 3) return
    const timer = setTimeout(() => { polls.current += 1; setOverride(null); void req.reload() }, 6000)
    return () => clearTimeout(timer)
  }, [pending, data, req.reload])

  const toggle = async (on: boolean) => {
    setBusy(true)
    try {
      // coord 在生成类型里是必填；不改坐标时它不起作用
      await stationsApi.update(id, { correction_enabled: on, coord: 'wgs84' })
      setOverride(null)
      await req.reload()
    } catch (e) {
      void Taro.showToast({ title: e instanceof ApiError ? e.message : '设置失败', icon: 'none' })
    } finally { setBusy(false) }
  }
  const remove = async (e: MeasuredEntry) => {
    const ok = await Taro.showModal({ title: '删除这条记录？', content: `${e.date} 的实测电量删除后，订正会按剩下的记录重新拟合。`, confirmText: '删除', confirmColor: '#ef4444' })
    if (!ok.confirm) return
    try {
      setOverride(await measuredApi.remove(id, e.id))
    } catch (err) {
      void Taro.showToast({ title: err instanceof ApiError ? err.message : '删除失败', icon: 'none' })
    }
  }

  return (
    <View className="measured">
      <PageHeader title="实测对账" subtitle={station?.name} />
      <View className="measured__body">
        {req.status === 'error' && !override ? <ErrorState error={req.error} onRetry={req.reload} />
          : !data ? <Skeleton height={180} lines={3} />
          : <>
            <CorrectionCard c={data.correction} onToggle={toggle} busy={busy} />
            <Button className="measured__record" hoverClass="pressed" onClick={() => setRecording(true)}>
              <Icon name="plus" size={16} color="#ffffff" /><Text>记一笔实测电量</Text>
            </Button>
            <View className="measured__card">
              <View className="measured__row measured__list-head">
                <Text className="measured__list-title">记录</Text>
                <Text className="measured__caption">单位 MWh</Text>
              </View>
              {data.entries.length === 0
                ? <Text className="measured__empty">还没有记录。逆变器 App、电表或结算单上的日电量、月电量都可以记。</Text>
                : <>
                  <View className="measured__entry measured__entry--head">
                    <Text className="measured__entry-date">日期</Text>
                    <Text className="measured__entry-num">实测</Text>
                    <Text className="measured__entry-num measured__entry-model">模型同期</Text>
                    <Text className="measured__entry-status">状态</Text>
                    <View className="measured__entry-del" />
                  </View>
                  {data.entries.map(e => <EntryRow key={e.id} e={e} onDelete={remove} />)}
                </>}
            </View>
          </>}
      </View>
      {station && <RecordSheet stationId={id} capacityKw={station.capacity} visible={recording} onClose={() => setRecording(false)} onDone={s => setOverride(s)} />}
    </View>
  )
}
