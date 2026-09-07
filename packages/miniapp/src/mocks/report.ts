export const mockReport = {
  station: { name: '苏州光伏站', address: '江苏省苏州市吴中区', latitude: 31.3, longitude: 120.62 },
  report_date: '2026年9月7日',
  generated_at: '2026年9月7日 08:00',

  verdict_title: '今日适宜发电，下午存在轻度云层风险',
  verdict_detail: '整体天气条件良好，建议按计划运行，关注下午云量变化，适时调整发电策略。',

  periods: [
    { period: 'morning' as const, time_range: '06:00 – 12:00',
      weather_summary: '晴转多云', generation_impact: '发电条件良好', level: 'good' as const },
    { period: 'afternoon' as const, time_range: '12:00 – 18:00',
      weather_summary: '云量逐步增加', generation_impact: '发电效率可能下降', level: 'warning' as const },
    { period: 'evening' as const, time_range: '18:00 – 24:00',
      weather_summary: '多云转晴', generation_impact: '对次日无明显影响', level: 'good' as const },
  ],

  risk_title: '14:30后云量增加，预计辐射下降20%',
  risk_detail:
    '受上游云系影响，下午云量将逐步增加，可能对发电量产生一定影响，建议密切关注实时天气变化，适时调整运行策略。',

  suggestions: [
    '建议今日按计划运行，重点关注14:30后云量变化，适时调整逆变器运行策略。',
    '加强下午时段的实时监测，若云量持续增加，可考虑启用功率平滑控制，减少输出波动。',
    '关注未来24小时天气变化，提前做好设备巡检，确保系统稳定运行。',
  ],

  summary: {
    generation: { value: 1680, delta_percent: 12 },
    equivalent_hours: { value: 3.4, delta_percent: 8 },
    co2_reduction: { value: 1344, delta_percent: 12 },
    estimated_revenue: { value: 672, delta_percent: 12 },
  },
}
