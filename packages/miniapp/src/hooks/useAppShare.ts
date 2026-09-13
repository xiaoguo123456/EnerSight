import Taro, { useDidShow, useShareAppMessage, useShareTimeline } from '@tarojs/taro'

export const SHARE_TITLE = '晴川观象｜看天气，知发电'
export const SHARE_IMAGE = '/assets/share/home.jpg'
export const SHARE_HOME = '/pages/home/index'

let localImage = SHARE_IMAGE
let preparing: Promise<string> | undefined

/** 分享面板使用本地文件路径，兼容无法直接显示代码包图片的客户端。 */
function prepareShareImage() {
  if (process.env.TARO_ENV !== 'weapp') return Promise.resolve(SHARE_IMAGE)
  if (!preparing) {
    preparing = new Promise<string>(resolve => {
      const destination = `${Taro.env.USER_DATA_PATH}/enersight-share-home.jpg`
      Taro.getFileSystemManager().copyFile({
        srcPath: SHARE_IMAGE,
        destPath: destination,
        success: () => { localImage = destination; resolve(destination) },
        fail: () => resolve(SHARE_IMAGE),
      })
    })
  }
  return preparing
}

const friendContent = (imageUrl: string) => ({ title: SHARE_TITLE, path: SHARE_HOME, imageUrl })

/** 全站转发均进入首页，分享内容不携带场站资料或账号参数。 */
export function useAppShare(timeline = false) {
  useShareAppMessage(() => ({ ...friendContent(localImage), promise: prepareShareImage().then(friendContent) }))
  useDidShow(() => {
    if (process.env.TARO_ENV !== 'weapp') return
    void prepareShareImage()
    const options = {
      withShareTicket: false,
      menus: timeline ? ['shareAppMessage', 'shareTimeline'] : ['shareAppMessage'],
    }
    void Taro.showShareMenu(options).catch(() => undefined)
  })
}

/** 朋友圈固定打开来源页，所以只在首页开启，保证所有分享入口都落在首页。 */
export function useHomeShare() {
  useAppShare(true)
  useShareTimeline(() => ({ title: SHARE_TITLE, imageUrl: localImage, query: '' }))
}
