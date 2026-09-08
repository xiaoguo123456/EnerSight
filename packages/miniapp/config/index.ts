import path from 'node:path'
import type { UserConfigExport } from '@tarojs/cli'

const SRC = path.resolve(__dirname, '..', 'src')

export default {
  projectName: 'enersight',
  date: '2026-9-7',
  // 设计稿基准宽度。packages/ui/ 的 PNG 导出宽度为 941px，
  // 设计源文件的基准需与设计确认后校正 —— 若为 iPhone 375pt @2x 则保持 750。
  // 02 文档的字号（正文 16、辅助 13）是 375pt 基准的标注值，
  // 因此 designWidth 取 375：16px → 32rpx → 16pt。
  // 若后续确认设计稿基准不同，改这里，不要去改 token 里的数值。
  designWidth: 375,
  deviceRatio: { 640: 2.34 / 2, 750: 1, 828: 1.81 / 2, 375: 2 / 1 },
  sourceRoot: 'src',
  // 按平台分目录，否则 h5 构建会覆盖小程序产物
  outputRoot: `dist/${process.env.TARO_ENV}`,
  plugins: [],
  framework: 'react',
  compiler: 'vite',
  sass: {
    // 只注入 mixin —— 它不产生 CSS 输出，可以安全地在每个文件里展开。
    // token 由 global.scss 引入一次，产出到 app.wxss 全局生效。
    resource: [path.join(SRC, 'styles', 'mixins.scss')],
    projectDirectory: path.resolve(__dirname, '..'),
  },
  alias: { '@': SRC },
  // 地图 marker 图标必须是文件路径：小程序 iconPath 不认 base64，而 vite 会把小图内联。
  // 按文件拷进产物，代码里用 /assets/markers/xxx.png 绝对路径引用
  copy: {
    patterns: [
      { from: 'src/assets/markers/', to: `dist/${process.env.TARO_ENV}/assets/markers/` },
    ],
    options: {},
  },
  mini: {
    postcss: {
      pxtransform: { enable: true },
      url: { enable: true, config: { limit: 1024 } },
    },
  },
  h5: {
    publicPath: '/',
    staticDirectory: 'static',
    postcss: {
      autoprefixer: { enable: true },
    },
  },
} satisfies UserConfigExport<'vite'>
