/** 页面使用中心点与 scale 控制视野，不使用 includePoints 自动适配。
 * 移除 Taro 默认生成的空列表属性，规避微信模拟器对空列表执行 fitBounds。
 * 若以后需要 includePoints，应改用 MapContext.includePoints 并验证非空坐标。
 */
module.exports = (ctx) => {
  ctx.modifyBuildAssets(({ assets }) => {
    if (process.env.TARO_ENV !== 'weapp') return
    for (const name of Object.keys(assets)) {
      if (!name.endsWith('.wxml')) continue
      const source = String(assets[name].source())
      const fixed = source.replace(/(<map\s[^>]*?)\sinclude-points="[^"]*"/g, '$1')
      if (fixed !== source) assets[name] = { source: () => fixed }
    }
  })
}
