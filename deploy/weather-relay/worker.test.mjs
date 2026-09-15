import assert from 'node:assert/strict';
import test from 'node:test';
import {handle, upstreamUrl} from './worker.mjs';

const env = {RELAY_SECRET: 'a'.repeat(64), ALLOWED_IPS: '47.93.60.25'};
const path = '/v1/forecast?latitude=39.9&longitude=116.4&minutely_15=temperature_2m&forecast_days=8&past_days=1';
const req = (p = path, headers = {}, method = 'GET') => new Request('https://relay.example' + p, {
  method, headers: {'CF-Connecting-IP': env.ALLOWED_IPS, Authorization: `Bearer ${env.RELAY_SECRET}`, ...headers},
});
const forbidden = () => {throw new Error('不应访问上游');};

test('未配置、错误来源、错误密钥和错误方法不会请求上游', async () => {
  assert.equal((await handle(req(), {}, forbidden)).status, 503);
  assert.equal((await handle(req(path, {'CF-Connecting-IP': '1.2.3.4'}), env, forbidden)).status, 403);
  assert.equal((await handle(req(path, {Authorization: 'Bearer wrong'}), env, forbidden)).status, 401);
  assert.equal((await handle(req(path, {}, 'POST'), env, forbidden)).status, 405);
});

test('拒绝任意目标、重复参数、越界坐标和超过100坐标', () => {
  for (const p of ['/proxy?url=https://example.com', path + '&latitude=0',
    '/v1/forecast?latitude=91&longitude=0', '/v1/forecast?latitude=&longitude=0',
    path + '&url=https://example.com', path + '&models=best_match,gfs_global',
    path + '&cell_selection=ocean', path + '&cell_selection=sea&cell_selection=land',
    '/v1/forecast?latitude=' + Array(101).fill('0').join(',') + '&longitude=' + Array(101).fill('0').join(',')]) {
    assert.equal(upstreamUrl(new URL('https://relay.example' + p)), null);
  }
});

test('100坐标预报、历史小时资料和三种元数据使用指定源站', () => {
  const query = new URLSearchParams({latitude:Array(100).fill('39').join(','), longitude:Array(100).fill('116').join(','), minutely_15:'wind_speed_100m',forecast_days:'8'});
  assert.equal(new URL(upstreamUrl(new URL('https://relay.example/v1/forecast?' + query))).hostname, 'api.open-meteo.com');
  assert.equal(new URL(upstreamUrl(new URL('https://relay.example/v1/archive?latitude=0&longitude=0&hourly=temperature_2m&start_date=2026-01-01&end_date=2026-01-02'))).hostname, 'archive-api.open-meteo.com');
  for (const slug of ['ecmwf_ifs', 'ncep_gfs013', 'dwd_icon']) assert.equal(upstreamUrl(new URL(`https://relay.example/data/${slug}/static/meta.json`)), `https://api.open-meteo.com/data/${slug}/static/meta.json`);
});

test('密钥不传上游，保留限流与重试时间，不进行重试或跳转', async () => {
  let count = 0;
  const response = await handle(req(path, {Cookie:'private-cookie'}), env, async (url, options) => {
    count++;
    assert.equal(url, 'https://api.open-meteo.com' + path);
    assert.deepEqual(options.headers, {Accept:'application/json'});
    assert.equal(options.redirect, 'manual');
    return new Response('限流样例', {status:429, headers:{'Retry-After':'60'}});
  });
  assert.equal(count, 1);
  assert.equal(response.status, 429);
  assert.equal(response.headers.get('retry-after'), '60');
  assert.equal(response.headers.get('x-weather-relay'), 'cloudflare');
  assert.equal(await response.text(), '限流样例');
});

test('完整保留大型响应流和压缩标记，拒绝上游重定向', async () => {
  const response = await handle(req(), env, async () => new Response('x'.repeat(200000), {headers:{'content-encoding':'gzip'}}));
  assert.equal((await response.text()).length, 200000);
  assert.equal(response.headers.get('content-encoding'), 'gzip');
  assert.equal((await handle(req(), env, async () => new Response(null,{status:302,headers:{Location:'https://example.com'}}))).status, 502);
});

test('Worker 自身拒绝不带透传标记，后端据此区分转发配置错误与上游响应', async () => {
  const rejected = await handle(req(path, {Authorization: 'Bearer wrong'}), env, forbidden);
  assert.equal(rejected.status, 401);
  assert.equal(rejected.headers.get('x-weather-relay'), null);
  const unsupported = await handle(req('/v1/elevation?latitude=0&longitude=0'), env, forbidden);
  assert.equal(unsupported.status, 400);
  assert.equal(unsupported.headers.get('x-weather-relay'), null);
});

test('海上风电的格点选择参数原样透传', () => {
  for (const p of [path + '&cell_selection=sea', '/v1/archive?latitude=34.4&longitude=120.2&hourly=wind_speed_100m&start_date=2026-01-01&end_date=2026-01-02&cell_selection=sea']) {
    assert.equal(new URL(upstreamUrl(new URL('https://relay.example' + p))).searchParams.get('cell_selection'), 'sea');
  }
});
