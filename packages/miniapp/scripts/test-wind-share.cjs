const {test} = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const root = path.join(__dirname, '../src')
function load(file, requireModule = () => ({}), extra = {}) {
  const exports = {}
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, esModuleInterop: true},
  }).outputText, {exports, require: requireModule, process: {env: {TARO_ENV: 'weapp'}}, ...extra})
  return exports
}
const map = load('../../core/src/map/index.ts', () => ({}))
const {createWindAnimation} = load('components/WindParticles/draw.ts', () => map)
const region = {southwest:{latitude:30,longitude:110},northeast:{latitude:32,longitude:112}}
const vectors = (u,v) => [30,32].flatMap(latitude => [110,112].map(longitude => ({latitude,longitude,u,v})))
function canvas() {
  const lines=[], clears=[]
  return {lines,clears,ctx:{clearRect:(...p)=>clears.push(p),beginPath(){},moveTo(){},lineTo:(...p)=>lines.push(p),stroke(){}}}
}

test('风场有箭头头部，东风分量向右、北风分量向上移动', () => {
  const frame=createWindAnimation(vectors(2,3),region,320,480,[],()=>.5)
  const c=canvas(); frame(c.ctx,40); const first=c.lines[0]
  assert.equal(c.lines.length%3,0)
  c.lines.length=0; frame(c.ctx,40); const second=c.lines[0]
  assert.ok(second[0]>first[0] && second[1]<first[1])
  assert.ok(Math.abs(second[0]-first[0]-.64)<1e-6)
})
test('风场每帧清理旧轨迹并避开操作面板', () => {
  const c=canvas();createWindAnimation(vectors(2,0),region,320,480,[{x:0,y:350,w:320,h:130}],()=>.5)(c.ctx)
  assert.deepEqual(c.clears[0],[0,0,320,480])
  assert.deepEqual(c.clears.at(-1),[-2,348,324,134])
})
test('静风与缺少有效矢量时不伪造移动箭头', () => {
  for(const input of [vectors(0,0),[],vectors(NaN,0)]) {
    const c=canvas();createWindAnimation(input,region,320,480,[],()=>.5)(c.ctx)
    assert.equal(c.lines.length,0)
  }
})

function shareHarness(copyFails = false) {
  let friend,timeline,shown;const menus=[]
  const taro={env:{USER_DATA_PATH:'wxfile://usr'},getFileSystemManager:()=>({copyFile:options=>copyFails?options.fail():options.success()}),showShareMenu:options=>{menus.push(options);return Promise.resolve()}}
  const hooks={__esModule:true,default:taro,useDidShow:cb=>shown=cb,useShareAppMessage:cb=>friend=cb,useShareTimeline:cb=>timeline=cb}
  const api=load('hooks/useAppShare.ts',()=>hooks)
  return {api,menus,friend:()=>friend(),timeline:()=>timeline(),show:()=>shown()}
}
test('任意页面转发使用统一首页、标题和封面，不携带当前场站参数', async () => {
  const h=shareHarness();h.api.useAppShare();h.show()
  const data=await h.friend().promise
  assert.equal(data.path,'/pages/home/index')
  assert.equal(data.title,'晴川观象｜看天气，知发电')
  assert.equal(data.imageUrl,'wxfile://usr/enersight-share-home.jpg')
  assert.equal(h.menus[0].menus.join(','),'shareAppMessage')
  assert.ok(fs.existsSync(path.join(root,h.api.SHARE_IMAGE)))
})
test('首页同时支持朋友圈且分享查询参数为空', () => {
  const h=shareHarness();h.api.useHomeShare();h.show()
  assert.equal(h.timeline().query,'')
  assert.equal(h.menus[0].menus.join(','),'shareAppMessage,shareTimeline')
})
test('所有已注册页面开启好友分享，朋友圈入口只在首页', () => {
  const config=load('app.config.ts',undefined,{defineAppConfig:v=>v}).default
  for(const page of config.pages){
    const cfg=load(page+'.config.ts',undefined,{definePageConfig:v=>v}).default
    assert.equal(cfg.enableShareAppMessage,true,page)
    assert.equal(Boolean(cfg.enableShareTimeline),page==='pages/home/index',page)
  }
})

test('风场卸载会取消动画，迟到的画布查询不会重启动画', () => {
  for (const delayed of [false,true]) {
    let effect,queryCallback,frames=0
    const timers=new Map();let id=0
    const context={scale(){},clearRect(){}}
    const query={select(){return this},fields(){return this},boundingClientRect(){return this},exec(cb){queryCallback=cb;if(!delayed)cb([{node:{getContext:()=>context},width:320,height:480,left:0,top:0}])}}
    const exports={}
    const code=ts.transpileModule(fs.readFileSync(path.join(root,'components/WindParticles/index.tsx'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText
    vm.runInNewContext(code,{
      exports, process:{env:{TARO_ENV:'weapp'}}, Date,
      setTimeout:fn=>{timers.set(++id,fn);return id}, clearTimeout:id=>timers.delete(id),
      require:name=>name==='react'?{useEffect:fn=>effect=fn}:name==='@tarojs/taro'?{__esModule:true,default:{createSelectorQuery:()=>query,getWindowInfo:()=>({pixelRatio:2})}}:name==='./draw'?{createWindAnimation:()=>()=>frames++}:name==='react/jsx-runtime'?{jsx:()=>({}),jsxs:()=>({})}:{},
    })
    exports.WindParticles({vectors:vectors(2,0),region,layoutVersion:100})
    const cleanup=effect();cleanup()
    if(delayed)queryCallback([{node:{getContext:()=>context},width:320,height:480,left:0,top:0}])
    assert.equal(timers.size,0)
    assert.equal(frames,delayed?0:1)
  }
})

test('封面复制失败仍返回统一首页分享信息', async () => {
  const h=shareHarness(true);h.api.useAppShare();
  const data=await h.friend().promise
  assert.equal(data.path,'/pages/home/index')
  assert.equal(data.imageUrl,h.api.SHARE_IMAGE)
})
