// 测试与生产部署相同代码，分别绑定 RELAY_SECRET、ALLOWED_IPS 和独立域名。
const PARAMETERS = new Set([
  'latitude', 'longitude', 'minutely_15', 'hourly', 'models', 'timezone',
  'forecast_days', 'past_days', 'wind_speed_unit', 'start_date', 'end_date', 'cell_selection',
]);
const MODELS = new Set(['best_match', 'ecmwf_ifs', 'gfs_global', 'icon_global']);
// 海上风电取海上格点（后端只传 sea），其余取值一并拒绝。
const CELL_SELECTIONS = new Set(['land', 'sea', 'nearest']);
const META = /^\/data\/(ecmwf_ifs|ncep_gfs013|dwd_icon)\/static\/meta\.json$/;

function message(text, status) {
  return Response.json({error: true, reason: text}, {status, headers: {'Cache-Control': 'no-store'}});
}

export function upstreamUrl(input) {
  if (META.test(input.pathname)) {
    return input.search ? null : 'https://api.open-meteo.com' + input.pathname;
  }
  const hosts = {'/v1/forecast': 'api.open-meteo.com', '/v1/archive': 'archive-api.open-meteo.com'};
  const host = hosts[input.pathname];
  if (!host) return null;
  for (const key of input.searchParams.keys()) {
    if (!PARAMETERS.has(key) || input.searchParams.getAll(key).length !== 1) return null;
  }
  const lat = input.searchParams.get('latitude')?.split(',') || [];
  const lon = input.searchParams.get('longitude')?.split(',') || [];
  const valid = (values, bound) => values.every(v => v.trim() !== '' && Number.isFinite(Number(v)) && Math.abs(Number(v)) <= bound);
  if (!lat.length || lat.length > 100 || lat.length !== lon.length || !valid(lat, 90) || !valid(lon, 180)) return null;
  for (const key of ['forecast_days', 'past_days']) {
    const raw = input.searchParams.get(key);
    if (raw !== null && (!/^\d+$/.test(raw) || Number(raw) > 16)) return null;
  }
  if (input.searchParams.has('models') && !MODELS.has(input.searchParams.get('models'))) return null;
  if (input.searchParams.has('cell_selection') && !CELL_SELECTIONS.has(input.searchParams.get('cell_selection'))) return null;
  const path = input.pathname === '/v1/archive' ? '/v1/archive' : '/v1/forecast';
  return `https://${host}${path}${input.search}`;
}

export async function handle(request, env, fetcher = fetch) {
  const secret = env.RELAY_SECRET;
  const allowed = String(env.ALLOWED_IPS || '').split(',').map(ip => ip.trim()).filter(Boolean);
  if (typeof secret !== 'string' || secret.length < 32 || !allowed.length) return message('转发服务未配置', 503);
  if (!allowed.includes(request.headers.get('CF-Connecting-IP'))) return message('禁止访问', 403);
  if (request.headers.get('Authorization') !== `Bearer ${secret}`) return message('认证失败', 401);
  if (request.method !== 'GET') return message('仅支持 GET', 405);
  const input = new URL(request.url);
  if (input.pathname === '/health' && !input.search) return Response.json({status: 'ok'}, {headers: {'Cache-Control': 'no-store'}});
  const target = upstreamUrl(input);
  if (!target) return message('不支持的气象请求', 400);
  try {
    const response = await fetcher(target, {
      headers: {'Accept': 'application/json'},
      redirect: 'manual',
      signal: AbortSignal.timeout(35000),
      cf: {cacheTtl: 0, cacheEverything: false},
    });
    if (response.status >= 300 && response.status < 400) return message('上游重定向未受支持', 502);
    const headers = new Headers({'Cache-Control': 'no-store', 'X-Weather-Relay': 'cloudflare'});
    for (const name of ['content-type', 'content-encoding', 'retry-after']) {
      if (response.headers.has(name)) headers.set(name, response.headers.get(name));
    }
    // 保留响应流和 429，不解析大批量 JSON、不重试、不转发后端密钥或 Cookie。
    return new Response(response.body, {status: response.status, headers});
  } catch {
    return message('上游连接失败或超时', 502);
  }
}

export default {fetch(request, env) {return handle(request, env);}};
