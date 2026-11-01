import { useState, useEffect, useRef } from 'react'
import { api, formatNumber, formatPct, formatShares, formatMoney } from '../lib/api'
import { useT } from '../lib/i18n'
import LanguageSwitch from '../components/LanguageSwitch'
import CcassTab from '../components/CcassTab'
import TradingTab from '../components/TradingTab'
import SignalsTab from '../components/SignalsTab'
import IrTab from '../components/IrTab'
import ReportsTab from '../components/ReportsTab'
import CostAnalysisTab from '../components/CostAnalysisTab'
import CapitalFlowTab from '../components/CapitalFlowTab'

interface Props {
  stockCode: string
  onBack: () => void
  onStockUpdated: () => void
  initialJobId?: string | null
}

type Tab = 'overview' | 'ccass' | 'trading' | 'signals' | 'reports' | 'ir' | 'cost' | 'flow'

export default function StockDashboard({ stockCode, onBack, onStockUpdated, initialJobId }: Props) {
  const [dashboard, setDashboard] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [fetching, setFetching] = useState(Boolean(initialJobId))
  const [fetchProgress, setFetchProgress] = useState<any>(null)
  const [tab, setTab] = useState<Tab>('overview')
  const [error, setError] = useState('')
  const [jobPolling, setJobPolling] = useState(Boolean(initialJobId))
  const { t, stockName: stockNameI18n } = useT()
  const pollGen = useRef(0)
  const activeJobRef = useRef<string | null>(initialJobId || null)

  const loadDashboard = async (silent = false) => {
    try {
      if (!silent) setLoading(true)
      setError('')
      const data = await api.getDashboard(stockCode)
      setDashboard(data)
      if (!data.latest_trading && !data.latest_ccass && !fetching && !jobPolling && !activeJobRef.current) {
        handleFetch()
      }
    } catch (e: any) {
      setError(e.message || t('loading'))
    } finally {
      if (!silent) setLoading(false)
    }
  }

  const stopWatchingJob = () => {
    activeJobRef.current = null
    setFetching(false)
    setJobPolling(false)
  }

  const pollJob = (jobId: string, retries = 0) => {
    const gen = ++pollGen.current
    const check = async () => {
      if (pollGen.current !== gen) return
      try {
        const res = await fetch(`/api/jobs/${jobId}`)
        if (pollGen.current !== gen) return
        if (res.status === 404) {
          stopWatchingJob()
          setFetchProgress(null)
          await loadDashboard()
          return
        }
        const status = await res.json()
        if (pollGen.current !== gen) return
        setFetchProgress(status)
        if (status.status === 'done') {
          stopWatchingJob()
          setFetchProgress(null)
          await loadDashboard()
          onStockUpdated()
        } else if (status.status === 'error') {
          stopWatchingJob()
          setError(t('fetchFailed') + ': ' + (status.error || 'unknown error'))
        } else {
          setTimeout(check, 3000)
        }
      } catch (e) {
        if (pollGen.current !== gen) return
        if (retries < 5) {
          setTimeout(() => {
            if (pollGen.current === gen) pollJob(jobId, retries + 1)
          }, 5000)
        } else {
          stopWatchingJob()
        }
      }
    }
    check()
  }

  const handleFetch = async () => {
    if (fetching || activeJobRef.current) return
    setFetching(true)
    setError('')
    try {
      const result = await api.fetchStockData(stockCode, 90)
      if (result.job_id) {
        activeJobRef.current = result.job_id
        setJobPolling(true)
        pollJob(result.job_id)
      } else {
        setFetching(false)
        await loadDashboard()
      }
    } catch (e: any) {
      setError(t('fetchFailed') + ': ' + e.message)
      stopWatchingJob()
    }
  }

  useEffect(() => {
    activeJobRef.current = initialJobId || null
    if (initialJobId) {
      setFetching(true)
      setJobPolling(true)
      pollJob(initialJobId)
    }
    loadDashboard()
    return () => {
      pollGen.current += 1
    }
  }, [stockCode, initialJobId])

  useEffect(() => {
    if (dashboard?.latest_ccass && !jobPolling && fetching) {
      setFetching(false)
      setFetchProgress(null)
    }
  }, [dashboard, jobPolling])

  const code = stockCode
  const name = stockNameI18n(code, dashboard?.stock_name || '')
  const trade = dashboard?.latest_trading
  const ccass = dashboard?.latest_ccass
  const hk = dashboard?.latest_hk_connect
  const signals = dashboard?.signals
  const t30 = dashboard?.trading_30d

  const tabs: { key: Tab; label: string; icon: string }[] = [
    { key: 'overview', label: t('tab_overview'), icon: '📊' },
    { key: 'ccass', label: t('tab_ccass'), icon: '🏦' },
    { key: 'flow', label: t('tab_flow'), icon: '🌊' },
    { key: 'cost', label: t('tab_cost'), icon: '💰' },
    { key: 'trading', label: t('tab_trading'), icon: '📈' },
    { key: 'signals', label: t('tab_signals'), icon: '🚨' },
    { key: 'reports', label: t('tab_reports'), icon: '📝' },
    { key: 'ir', label: dashboard?.ir_configured ? t('tab_ir') : t('tab_ir_setup'), icon: '🔒' },
  ]

  return (
    <div className="min-h-screen">
      {/* Header */}
      <header className="bg-white border-b border-slate-200 shadow-sm sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-3">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-4">
              <button onClick={onBack} className="text-slate-500 hover:text-slate-700 text-lg">{t('back')}</button>
              <div>
                <span className="text-xl font-bold text-slate-800">{name || t('loading')}</span>
                <span className="ml-2 text-sm font-mono text-slate-500">{code}.HK</span>
                {dashboard?.ir_configured && <span className="ml-2 badge badge-blue">IR</span>}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <LanguageSwitch />
              <button
                onClick={handleFetch}
                disabled={fetching}
                className="btn btn-secondary text-sm disabled:opacity-50"
              >
                {fetching ? t('updatingData') : t('updateData')}
              </button>
            </div>
          </div>
          {/* Progress bar when fetching */}
          {fetching && fetchProgress && (
            <div className="mt-3">
              <div className="flex items-center justify-between text-xs text-slate-500 mb-1">
                <span>{t('fetchingCcass')} {fetchProgress.current_date ? `(${fetchProgress.current_date})` : ''}...</span>
                <span>{fetchProgress.progress || 0}%</span>
              </div>
              <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
                <div
                  className="h-full bg-blue-500 transition-all duration-500 rounded-full"
                  style={{ width: `${fetchProgress.progress || 0}%` }}
                />
              </div>
            </div>
          )}
          {/* Tab bar */}
          <div className="flex gap-1 mt-3 -mb-[13px] overflow-x-auto">
            {tabs.map(t => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
                  tab === t.key
                    ? 'border-blue-600 text-blue-600'
                    : 'border-transparent text-slate-500 hover:text-slate-700'
                }`}
              >
                {t.icon} {t.label}
              </button>
            ))}
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 py-6">
        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg mb-4">
            {error}
          </div>
        )}

        {loading ? (
          <div className="text-center py-20 text-slate-400">{t('loading')}</div>
        ) : !dashboard?.latest_ccass ? (
          <div className="text-center py-20">
            <p className="text-4xl mb-4">⏳</p>
            {fetching ? (
              <>
                <p className="text-slate-600 mb-2">{t('fetchingCcass')}...</p>
                <p className="text-sm text-slate-400">{t('firstLoadHint')}</p>
                <p className="text-sm text-slate-400 mt-2">{t('marketDataReady')}</p>
              </>
            ) : (
              <>
                <p className="text-slate-500 mb-4">{t('noCcassData')}</p>
                <button onClick={handleFetch} className="btn btn-primary">{t('startFetch')}</button>
              </>
            )}
          </div>
        ) : (
          <>
            {tab === 'overview' && (
              <OverviewTab
                code={code}
                name={name}
                trade={trade}
                ccass={ccass}
                hk={hk}
                signals={signals}
                t30={t30}
                top10={dashboard?.top10 || []}
                info={dashboard?.info || {}}
                irConfigured={dashboard?.ir_configured}
                knownShares={dashboard?.known_shares}
              />
            )}
            {tab === 'ccass' && <CcassTab stockCode={code} />}
            {tab === 'flow' && <CapitalFlowTab stockCode={code} />}
            {tab === 'cost' && <CostAnalysisTab stockCode={code} />}
            {tab === 'trading' && <TradingTab stockCode={code} />}
            {tab === 'signals' && <SignalsTab stockCode={code} />}
            {tab === 'reports' && <ReportsTab stockCode={code} stockName={name} />}
            {tab === 'ir' && <IrTab stockCode={code} onConfigured={() => { loadDashboard(true) }} />}
          </>
        )}
      </main>
    </div>
  )
}

function OverviewTab({ code, name, trade, ccass, hk, signals, t30, top10, info, irConfigured, knownShares }: any) {
  const { t, brokerName } = useT()
  const priceColor = (trade?.change_pct || 0) >= 0 ? 'text-red-600' : 'text-green-600'
  const priceBg = (trade?.change_pct || 0) >= 0 ? 'bg-red-50' : 'bg-green-50'

  const ccassTotal = ccass?.total_shares || 0
  const ccassPct = ccass?.pct_of_issued || 0
  const issuedShares = ccassPct > 0 ? Math.round(ccassTotal / (ccassPct / 100)) : 0

  return (
    <div className="space-y-6">
      {/* Price Card */}
      {trade && (
        <div className={`card ${priceBg}`}>
          <div className="flex items-end justify-between flex-wrap gap-4">
            <div>
              <div className="flex items-baseline gap-3">
                <span className="text-4xl font-bold">{trade.close?.toFixed(2)}</span>
                <span className={`text-lg font-semibold ${priceColor}`}>
                  {formatPct(trade.change_pct)}
                </span>
              </div>
              <div className="flex gap-4 mt-2 text-sm text-slate-600">
                <span>{t('open')} {trade.open?.toFixed(2)}</span>
                <span>{t('high')} {trade.high?.toFixed(2)}</span>
                <span>{t('low')} {trade.low?.toFixed(2)}</span>
                <span>{t('volume')} {formatShares(trade.volume)}</span>
                <span>{t('turnover')} {formatMoney(trade.turnover)}</span>
              </div>
            </div>
            {t30 && (
              <div className="text-right text-sm text-slate-500">
                <div>{t('avgVol30d')} {formatNumber(t30.avg_vol)}</div>
                <div>{t('range')} {t30.period_low?.toFixed(2)} - {t30.period_high?.toFixed(2)}</div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* KPI Row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard
          label={t('ccassHoldings')}
          value={formatShares(ccassTotal)}
          sub={ccassPct ? `${ccassPct.toFixed(2)}% ${t('ofTotalShares')}` : '-'}
          color="blue"
        />
        <KpiCard
          label={t('southboundHoldings')}
          value={formatShares(hk?.shares)}
          sub={hk ? (
            <span className={(hk.shares_change || 0) >= 0 ? 'text-red-600' : 'text-green-600'}>
              {(hk.shares_change || 0) >= 0 ? '+' : ''}{formatShares(hk.shares_change)}
            </span>
          ) : '-'}
          color="purple"
        />
        <KpiCard
          label={t('signalScore')}
          value={signals ? `${signals.score}/100` : '-'}
          sub={signals?.verdict || t('noSignals')}
          color={
            signals?.score >= 60 ? 'red' : signals?.score <= 40 ? 'green' : 'yellow'
          }
        />
        <KpiCard
          label={t('issuedShares')}
          value={issuedShares > 0 ? formatShares(issuedShares) : '-'}
          sub={info?.pe_ratio ? `PE ${info.pe_ratio}` : '-'}
          color="gray"
        />
      </div>
      {ccassTotal > 0 && !ccassPct && (
        <p className="text-xs text-slate-500 -mt-2">{t('pctNotDisclosed')}</p>
      )}

      {/* Signal Verdict */}
      {signals?.anomalies?.length > 0 && (
        <div className="card">
          <h3 className="font-semibold text-slate-800 mb-3">{t('latestAnomalies')}</h3>
          <div className="space-y-2">
            {signals.anomalies.slice(0, 5).map((a: any, i: number) => (
              <div key={i} className={`flex items-start gap-2 p-2 rounded ${
                a.severity === 'high' ? 'bg-red-50' : 'bg-yellow-50'
              }`}>
                <span className={`badge ${a.severity === 'high' ? 'badge-red' : 'badge-yellow'}`}>
                  {a.severity === 'high' ? t('high') : t('medium')}
                </span>
                <span className="text-sm text-slate-700">{a.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Top 10 */}
      {top10?.length > 0 && (
        <div className="card">
          <h3 className="font-semibold text-slate-800 mb-3">{t('top10Holders')}</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-slate-500 border-b">
                  <th className="py-2 w-12">#</th>
                  <th className="py-2">{t('seat')}</th>
                  <th className="py-2 text-right">{t('holdings')}</th>
                  <th className="py-2 text-right">{t('pct')}</th>
                </tr>
              </thead>
              <tbody>
                {top10.map((p: any) => (
                  <tr key={p.rank} className="border-b border-slate-100">
                    <td className="py-2 text-slate-400">{p.rank}</td>
                    <td className="py-2">
                      <div className="font-medium text-slate-800">{brokerName(p.participant_id, p.participant_name)}</div>
                      <div className="text-xs text-slate-400 font-mono">{p.participant_id}</div>
                    </td>
                    <td className="py-2 text-right font-mono">{formatShares(p.shares)}</td>
                    <td className="py-2 text-right text-slate-600">{p.pct_of_issued != null ? `${Number(p.pct_of_issued).toFixed(2)}%` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* IR Status */}
      <div className={`card ${irConfigured ? 'border-blue-200 bg-blue-50/30' : ''}`}>
        {irConfigured ? (
          <div>
            <h3 className="font-semibold text-slate-800 mb-2">{t('irEnabled')}</h3>
            <p className="text-sm text-slate-600">
              {t('irEnabledDesc', { shares: formatShares(knownShares) })}
            </p>
          </div>
        ) : (
          <div>
            <h3 className="font-semibold text-slate-800 mb-2">{t('irDisabled')}</h3>
            <p className="text-sm text-slate-600 mb-3">
              {t('irDisabledDesc')}
            </p>
            <p className="text-xs text-slate-400">
              {t('irDisabledNote')}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

function KpiCard({ label, value, sub, color }: any) {
  const colors: Record<string, string> = {
    blue: 'border-blue-100',
    red: 'border-red-100 bg-red-50/50',
    green: 'border-green-100 bg-green-50/50',
    yellow: 'border-yellow-100 bg-yellow-50/50',
    purple: 'border-purple-100 bg-purple-50/50',
    gray: 'border-slate-200',
  }
  return (
    <div className={`card ${colors[color] || ''}`}>
      <div className="text-xs text-slate-500 mb-1">{label}</div>
      <div className="text-xl font-bold text-slate-800">{value}</div>
      <div className="text-xs mt-1 text-slate-500">{sub}</div>
    </div>
  )
}
