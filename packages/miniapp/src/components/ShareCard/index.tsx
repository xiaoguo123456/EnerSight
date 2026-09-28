import { Button, Canvas, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useEffect, useRef } from 'react'
import { formatBeijingTime, formatEnergy } from '@enersight/core/format'
import type { FleetSignalResponse } from '@enersight/core/types'
import './index.scss'

function energy(value: number | null | undefined) { const v = formatEnergy(value); return `${v.value} ${v.unit}` }

/** 生成轻量的交易晨报图片；不上传服务端，图片只留在用户本机。 */
export function ShareCard({ data, date, visible, onClose }: { data: FleetSignalResponse | null; date: string; visible: boolean; onClose: () => void }) {
  const canvasId = useRef(`share-card-${Math.random().toString(36).slice(2)}`).current
  useEffect(() => {
    if (!visible || !data) return
    const ctx = Taro.createCanvasContext(canvasId)
    ctx.setFillStyle('#f5f9fc'); ctx.fillRect(0, 0, 600, 840)
    ctx.setFillStyle('#ffffff'); ctx.fillRect(32, 32, 536, 776)
    ctx.setFillStyle('#1f2937'); ctx.setFontSize(34); ctx.fillText('晴川观象', 68, 104)
    ctx.setFillStyle('#64748b'); ctx.setFontSize(20); ctx.fillText('新能源供给晨报', 68, 140)
    ctx.setFillStyle('#1677ff'); ctx.setFontSize(24); ctx.fillText(date.slice(5).replace('-', '/') + ' 公开目录预测', 68, 192)
    ctx.setFillStyle('#1f2937'); ctx.setFontSize(22); ctx.fillText('预计电量', 68, 264)
    ctx.setFontSize(42); ctx.fillText(energy(data.combined?.current_kwh), 68, 320)
    ctx.setFillStyle('#64748b'); ctx.setFontSize(20); ctx.fillText(`较上一轮 ${data.combined?.change_percent == null ? '暂无可比变化' : `${data.combined.change_percent > 0 ? '+' : ''}${data.combined.change_percent.toFixed(1)}%`}`, 68, 356)
    ctx.setFillStyle('#eff5ff'); ctx.fillRect(68, 402, 464, 110)
    ctx.setFillStyle('#1f2937'); ctx.setFontSize(22); ctx.fillText('预测分歧', 92, 442)
    ctx.setFontSize(30); ctx.fillText(data.model_range?.spread_percent == null ? '暂无' : `${data.model_range.spread_percent.toFixed(1)}%`, 92, 484)
    ctx.setFillStyle('#64748b'); ctx.setFontSize(19); ctx.fillText(data.top_windows.length ? `重点时段 ${data.top_windows[0]!.start_hour.slice(11, 16)}–${data.top_windows[0]!.end_hour.slice(11, 16)}` : '暂无重点变化时段', 68, 584)
    ctx.setFillStyle('#9ca3af'); ctx.setFontSize(17); ctx.fillText(data.generated_at ? `${formatBeijingTime(data.generated_at)} 更新` : '等待今日签发', 68, 744)
    ctx.fillText('预测参考，不代表实际并网电量', 68, 776)
    ctx.draw()
  }, [canvasId, data, date, visible])

  const exportCard = async () => {
    if (!data) return
    try {
      const result = await Taro.canvasToTempFilePath({ canvasId, x: 0, y: 0, width: 600, height: 840, destWidth: 1200, destHeight: 1680 })
      const shareImageMessage = (Taro as any).shareImageMessage as ((options: { path: string }) => Promise<unknown>) | undefined
      if (shareImageMessage) await shareImageMessage({ path: result.tempFilePath })
      else await Taro.saveImageToPhotosAlbum({ filePath: result.tempFilePath })
      void Taro.showToast({ title: shareImageMessage ? '已打开分享' : '图片已保存', icon: 'success' })
    } catch (error) {
      if (/cancel/i.test((error as { errMsg?: string } | null)?.errMsg ?? '')) return
      void Taro.showToast({ title: '生成图片失败，请重试', icon: 'none' })
    }
  }
  if (!visible || !data) return null
  return <View className="share-card-modal"><View className="share-card-modal__mask" onClick={onClose} /><View className="share-card-modal__panel"><Text className="share-card-modal__title">分享晨报</Text><View className="share-card-modal__canvas-wrap"><Canvas canvasId={canvasId} className="share-card-modal__canvas" width="600" height="840" /></View><View className="share-card-modal__actions"><Button className="share-card-modal__cancel" onClick={onClose}>取消</Button><Button className="share-card-modal__confirm" onClick={() => void exportCard()}>生成图片</Button></View></View></View>
}
