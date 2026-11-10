const API_BASE = '/api'

async function fetchApi(path: string, options?: RequestInit) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `API error: ${res.status}`)
  }
  return res.json()
}

export const api = {
  listStocks: () => fetchApi('/stocks'),
  addStock: (code: string) => fetchApi(`/stocks/${code}`, { method: 'POST' }),
  deleteStock: (code: string) => fetchApi(`/stocks/${code}`, { method: 'DELETE' }),
  fetchStockData: (code: string, days = 90) => fetchApi(`/stocks/${code}/fetch?days=${days}`, { method: 'POST' }),

  getDashboard: (code: string) => fetchApi(`/stock/${code}/dashboard`),
  getInfo: (code: string) => fetchApi(`/stock/${code}/info`),
  getTrading: (code: string, days = 90) => fetchApi(`/stock/${code}/trading?days=${days}`),
  getCcassTotal: (code: string, days = 60) => fetchApi(`/stock/${code}/ccass/total?days=${days}`),
  getParticipants: (code: string, date?: string) =>
    fetchApi(`/stock/${code}/ccass/participants${date ? `?date=${date}` : ''}`),
  getChanges: (code: string, date?: string) =>
    fetchApi(`/stock/${code}/ccass/changes${date ? `?date=${date}` : ''}`),
  getDates: (code: string, days = 60) => fetchApi(`/stock/${code}/ccass/dates?days=${days}`),
  ensureCcassRange: (code: string, days = 60) =>
    fetchApi(`/stock/${code}/ccass/ensure?days=${days}`, { method: 'POST' }),
  getSignals: (code: string) => fetchApi(`/stock/${code}/signals`),
  getCostAnalysis: (code: string) => fetchApi(`/stock/${code}/cost-analysis`),
  getScenarioAnalysis: (code: string) => fetchApi(`/stock/${code}/scenario-analysis`),
  getCapitalFlow: (code: string, days = 5, endDate?: string) =>
    fetchApi(`/stock/${code}/capital-flow?days=${days}${endDate ? `&end_date=${encodeURIComponent(endDate)}` : ''}`),
  getHkConnect: (code: string, days = 60) => fetchApi(`/stock/${code}/hk-connect?days=${days}`),

  getAttribution: (code: string) => fetchApi(`/stock/${code}/attribution`),
  addAttribution: (code: string, data: any) =>
    fetchApi(`/stock/${code}/attribution`, { method: 'POST', body: JSON.stringify(data) }),
  updateAttribution: (code: string, id: number, data: any) =>
    fetchApi(`/stock/${code}/attribution/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  patchAttribution: (code: string, id: number, data: any) =>
    fetchApi(`/stock/${code}/attribution/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteAttribution: (code: string, id: number) =>
    fetchApi(`/stock/${code}/attribution/${id}`, { method: 'DELETE' }),
  getSelloff: (code: string, days = 30) => fetchApi(`/stock/${code}/selloff?days=${days}`),
  getUnlocks: (code: string) => fetchApi(`/stock/${code}/unlocks`),

  uploadFile: (code: string, file: File, overwrite = false) => {
    const fd = new FormData()
    fd.append('file', file)
    const query = overwrite ? '?overwrite=true' : ''
    return fetch(`${API_BASE}/stock/${code}/upload${query}`, { method: 'POST', body: fd }).then(async r => {
      const data = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(data.detail || data.error || 'Upload failed')
      return data
    })
  },

  getJobStatus: (jobId: string) => fetchApi(`/jobs/${jobId}`),

  _get: (path: string) => fetchApi(`/${path.replace(/^\/+/, '')}`),
  _post: (path: string, body?: any) => fetchApi(`/${path.replace(/^\/+/, '')}`, {
    method: 'POST',
    body: body ? JSON.stringify(body) : undefined,
  }),
}

function currentLang(): 'zh' | 'en' {
  if (typeof window !== 'undefined' && localStorage.getItem('hk-panel-lang') === 'en') return 'en'
  return 'zh'
}

function formatCompactEn(n: number, decimals = 0): string {
  const abs = Math.abs(n)
  if (abs >= 1e9) return (n / 1e9).toFixed(2) + 'B'
  if (abs >= 1e6) return (n / 1e6).toFixed(2) + 'M'
  if (abs >= 1e3) return (n / 1e3).toFixed(decimals) + 'K'
  return n.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
}

export function formatNumber(n: number | null | undefined, decimals = 0): string {
  if (n == null) return '-'
  if (currentLang() === 'en') return formatCompactEn(n, decimals)
  if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + '亿'
  if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(decimals) + '万'
  return n.toLocaleString('zh-CN', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
}

export function formatPct(n: number | null | undefined): string {
  if (n == null) return '-'
  return (n > 0 ? '+' : '') + n.toFixed(2) + '%'
}

export function formatShares(n: number | null | undefined): string {
  if (n == null) return '-'
  if (currentLang() === 'en') return formatCompactEn(n, 0) + ' shares'
  if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + '亿股'
  if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(0) + '万股'
  return n.toLocaleString() + '股'
}

export function formatMoney(n: number | null | undefined): string {
  if (n == null) return '-'
  if (currentLang() === 'en') return 'HK$' + formatCompactEn(n, 0)
  if (Math.abs(n) >= 1e8) return 'HK$' + (n / 1e8).toFixed(2) + '亿'
  if (Math.abs(n) >= 1e4) return 'HK$' + (n / 1e4).toFixed(0) + '万'
  return 'HK$' + n.toLocaleString()
}
