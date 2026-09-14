import Taro from '@tarojs/taro'

/**
 * 把 CSV 交给用户。小程序没有文件下载入口：写入用户目录后用 shareFileMessage 发到聊天，
 * 可发给「文件传输助手」再在电脑上打开；H5 直接下载。发送失败时可复制表格内容。
 */
export async function exportCsv(fileName: string, content: string): Promise<void> {
  const name = fileName.replace(/[\\/:*?"<>|\s]+/g, '_')
  if (process.env.TARO_ENV === 'h5') {
    const url = URL.createObjectURL(new Blob([content], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = name
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    return
  }
  const filePath = `${Taro.env.USER_DATA_PATH}/${name}`
  try {
    Taro.getFileSystemManager().writeFileSync(filePath, content, 'utf8')
    await Taro.shareFileMessage({ filePath, fileName: name })
  } catch (e) {
    if (/cancel/i.test((e as { errMsg?: string } | null)?.errMsg ?? '')) return
    const answer = await Taro.showModal({
      title: '暂时无法发送文件',
      content: '可以复制表格内容，粘贴到电脑上的表格软件中使用。',
      confirmText: '复制内容',
    })
    if (answer.confirm) await Taro.setClipboardData({ data: content.replace(/^\uFEFF/, '') })
  }
}
