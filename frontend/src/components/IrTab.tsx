import { useState, useEffect, useRef } from 'react'
import { api, formatShares, formatMoney } from '../lib/api'
import { useT } from '../lib/i18n'

export default function IrTab({ stockCode, onConfigured }: { stockCode: string; onConfigured: () => void }) {
  const [attribution, setAttribution] = useState<any[]>([])
  const [selloff, setSelloff] = useState<any[]>([])
  const [unlocks, setUnlocks] = useState<any>({ upcoming: [], past: [] })
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [uploadResult, setUploadResult] = useState<any>(null)
  const [showAddForm, setShowAddForm] = useState(false)
  const [newAttr, setNewAttr] = useState({
    shareholder_name: '', participant_id: '', participant_name: '',
    shares_baseline: '', cost_hkd: '', investor_type: '', unlock_date: '', notes: ''
  })
  const fileRef = useRef<HTMLInputElement>(null)
  const { t, brokerName } = useT()

  const load = async () => {
    setLoading(true)
    try {
      const [aRes, sRes, uRes] = await Promise.all([
        api.getAttribution(stockCode),
        api.getSelloff(stockCode, 30).catch(() => ({ data: [] })),
        api.getUnlocks(stockCode).catch(() => ({ upcoming: [], past: [] })),
      ])
      setAttribution(aRes.shareholders || [])
      setSelloff(sRes.data || [])
      setUnlocks(uRes)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [stockCode])

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    setUploadResult(null)
    try {
      const result = await api.uploadFile(stockCode, file)
      setUploadResult(result)
      await load()
      onConfigured()
    } catch (e: any) {
      setUploadResult({ error: e.message })
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const handleAddAttr = async () => {
    if (!newAttr.shareholder_name || !newAttr.participant_id) return
    await api.addAttribution(stockCode, {
      ...newAttr,
      shares_baseline: newAttr.shares_baseline ? parseInt(newAttr.shares_baseline) : 0,
      cost_hkd: newAttr.cost_hkd ? parseFloat(newAttr.cost_hkd) : null,
    })
    setNewAttr({ shareholder_name: '', participant_id: '', participant_name: '', shares_baseline: '', cost_hkd: '', investor_type: '', unlock_date: '', notes: '' })
    setShowAddForm(false)
    load()
  }

  const hasAttribution = attribution.length > 0

  return (
    <div className="space-y-6">
      {/* Upload Section */}
      <div className="card border-dashed border-2 border-blue-200">
        <h3 className="font-semibold text-slate-800 mb-2">{t('uploadTitle')}</h3>
        <p className="text-sm text-slate-600 mb-4">{t('uploadDesc')}</p>
        <div className="flex items-center gap-3 flex-wrap">
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx,.xls,.csv,.pdf,.txt,.tsv"
            onChange={handleFileUpload}
            className="hidden"
            id="file-upload"
          />
          <label htmlFor="file-upload" className="btn btn-primary cursor-pointer">
            {uploading ? t('uploadingParsing') : t('selectFile')}
          </label>
          <button onClick={() => setShowAddForm(!showAddForm)} className="btn btn-secondary">
            {t('addManualAttr')}
          </button>
        </div>

        {uploadResult && (
          <div className={`mt-4 p-3 rounded-lg ${uploadResult.error ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700'}`}>
            {uploadResult.error ? (
              <p>{t('parseFailed')} {uploadResult.error}</p>
            ) : (
              <div>
                <p className="font-medium">{t('parseSuccess')}</p>
                <p className="text-sm mt-1">
                  {t('parseSummary', {
                    count: uploadResult.summary?.attributions_found || 0,
                    unlocks: uploadResult.summary?.unlocks_found || 0,
                  })}
                </p>
                {uploadResult.attributions?.length > 0 && (
                  <div className="mt-2 text-xs space-y-1 max-h-40 overflow-y-auto">
                    {uploadResult.attributions.slice(0, 10).map((a: any, i: number) => (
                      <div key={i} className="text-slate-600">
                        • {a.shareholder_name} → {a.participant_name} ({a.participant_id})
                        {a.shares_baseline ? ` ${formatShares(a.shares_baseline)}` : ''}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Manual Add Form */}
      {showAddForm && (
        <div className="card">
          <h3 className="font-semibold text-slate-800 mb-3">{t('manualAddTitle')}</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <input
              placeholder={`${t('shareholderName')} *`}
              value={newAttr.shareholder_name}
              onChange={e => setNewAttr({ ...newAttr, shareholder_name: e.target.value })}
              className="px-3 py-2 border border-slate-300 rounded text-sm"
            />
            <input
              placeholder={`${t('participantId')} *`}
              value={newAttr.participant_id}
              onChange={e => setNewAttr({ ...newAttr, participant_id: e.target.value.toUpperCase() })}
              className="px-3 py-2 border border-slate-300 rounded text-sm font-mono"
            />
            <input
              placeholder={t('participantName')}
              value={newAttr.participant_name}
              onChange={e => setNewAttr({ ...newAttr, participant_name: e.target.value })}
              className="px-3 py-2 border border-slate-300 rounded text-sm"
            />
            <input
              placeholder={t('sharesBaseline')}
              type="number"
              value={newAttr.shares_baseline}
              onChange={e => setNewAttr({ ...newAttr, shares_baseline: e.target.value })}
              className="px-3 py-2 border border-slate-300 rounded text-sm"
            />
            <input
              placeholder={t('costHkd')}
              type="number"
              step="0.01"
              value={newAttr.cost_hkd}
              onChange={e => setNewAttr({ ...newAttr, cost_hkd: e.target.value })}
              className="px-3 py-2 border border-slate-300 rounded text-sm"
            />
            <input
              placeholder={t('investorType')}
              value={newAttr.investor_type}
              onChange={e => setNewAttr({ ...newAttr, investor_type: e.target.value })}
              className="px-3 py-2 border border-slate-300 rounded text-sm"
            />
            <input
              placeholder={t('unlockDate')}
              value={newAttr.unlock_date}
              onChange={e => setNewAttr({ ...newAttr, unlock_date: e.target.value })}
              className="px-3 py-2 border border-slate-300 rounded text-sm"
            />
          </div>
          <div className="flex gap-2 mt-3">
            <button onClick={handleAddAttr} className="btn btn-primary">{t('confirm')}</button>
            <button onClick={() => setShowAddForm(false)} className="btn btn-secondary">{t('cancel')}</button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="text-center py-8 text-slate-400">{t('loading')}</div>
      ) : !hasAttribution ? (
        uploadResult?.error ? null : (
          <div className="text-center py-12 text-slate-400">
            <p className="text-4xl mb-3">🔒</p>
            <p>{t('noAttribution')}</p>
          </div>
        )
      ) : (
        <>
          {/* Shareholder Summary */}
          <div className="card">
            <h3 className="font-semibold text-slate-800 mb-4">{t('shareholderAttribution')}</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-slate-500 border-b">
                    <th className="py-2">{t('shareholder')}</th>
                    <th className="py-2">{t('seat')}</th>
                    <th className="py-2 text-right">{t('currentHoldings')}</th>
                    <th className="py-2 text-right">{t('baselineHoldings')}</th>
                    <th className="py-2 text-right">{t('changeFromBaseline')}</th>
                    <th className="py-2 text-right">{t('change30d')}</th>
                    <th className="py-2">{t('cost')}</th>
                    <th className="py-2">{t('unlockDate')}</th>
                  </tr>
                </thead>
                <tbody>
                  {attribution.map((s: any, i: number) => (
                    <tr key={i} className="border-b border-slate-100">
                      <td className="py-2">
                        <div className="font-medium text-slate-800">{s.shareholder_name}</div>
                        {s.investor_type && <div className="text-xs text-slate-400">{s.investor_type}</div>}
                      </td>
                      <td className="py-2">
                        <div className="text-xs">{brokerName(s.participant_id, s.participant_name)}</div>
                        <div className="text-xs font-mono text-slate-400">{s.participant_id}</div>
                      </td>
                      <td className="py-2 text-right font-mono">{formatShares(s.shares_current)}</td>
                      <td className="py-2 text-right font-mono text-slate-500">{formatShares(s.shares_baseline)}</td>
                      <td className={`py-2 text-right font-mono ${
                        (s.change_from_baseline || 0) < 0 ? 'text-green-600' : (s.change_from_baseline || 0) > 0 ? 'text-red-600' : 'text-slate-400'
                      }`}>
                        {s.change_from_baseline != null ? (s.change_from_baseline > 0 ? '+' : '') + formatShares(s.change_from_baseline) : '-'}
                      </td>
                      <td className={`py-2 text-right font-mono ${
                        (s.change_30d || 0) < 0 ? 'text-green-600' : (s.change_30d || 0) > 0 ? 'text-red-600' : 'text-slate-400'
                      }`}>
                        {s.change_30d != null ? (s.change_30d > 0 ? '+' : '') + formatShares(s.change_30d) : '-'}
                      </td>
                      <td className="py-2 text-right text-xs">{s.cost_hkd ? `HK$${s.cost_hkd}` : '-'}</td>
                      <td className="py-2 text-xs font-mono text-slate-500">{s.unlock_date || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Selloff Tracking */}
          {selloff.length > 0 && (
            <div className="card">
              <h3 className="font-semibold text-slate-800 mb-4">{t('selloffTracking')}</h3>
              <div className="space-y-4 max-h-[500px] overflow-y-auto">
                {selloff.map((day: any, i: number) => (
                  <div key={i}>
                    <div className="flex items-center gap-2 mb-2">
                      <span className="font-mono text-sm text-slate-600">{day.date}</span>
                      {day.close && <span className="text-xs text-slate-400">{t('closingPrice')} HK${day.close.toFixed(2)}</span>}
                    </div>
                    <div className="space-y-1">
                      {day.shareholders?.filter((s: any) => s.shares_change !== 0).map((s: any, j: number) => (
                        <div key={j} className="flex items-center justify-between py-1 px-2 rounded bg-slate-50 text-sm">
                          <span className="text-slate-700">{s.shareholder_name}</span>
                          <span className={`font-mono ${s.shares_change < 0 ? 'text-green-600' : 'text-red-600'}`}>
                            {s.shares_change > 0 ? '+' : ''}{formatShares(s.shares_change)}
                            {s.estimated_proceeds != null && s.shares_change < 0 &&
                              <span className="text-xs text-slate-400 ml-2">≈ {formatMoney(s.estimated_proceeds)}</span>
                            }
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Unlock Calendar */}
          {(unlocks.upcoming?.length > 0 || unlocks.past?.length > 0) && (
            <div className="card">
              <h3 className="font-semibold text-slate-800 mb-4">{t('unlockCalendar')}</h3>
              {unlocks.upcoming?.length > 0 && (
                <div className="mb-4">
                  <h4 className="text-sm font-medium text-orange-600 mb-2">{t('upcomingUnlocks')}</h4>
                  <div className="space-y-1">
                    {unlocks.upcoming.map((u: any, i: number) => (
                      <div key={i} className="flex items-center justify-between py-1 px-2 rounded bg-orange-50 text-sm">
                        <span className="font-mono text-orange-700">{u.unlock_date}</span>
                        <span>{u.shareholder_name}</span>
                        <span className="font-mono">{formatShares(u.shares)}</span>
                        {u.cost_hkd && <span className="text-xs text-slate-400">@HK${u.cost_hkd}</span>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {unlocks.past?.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium text-slate-500 mb-2">{t('pastUnlocks')}</h4>
                  <div className="space-y-1">
                    {unlocks.past.map((u: any, i: number) => (
                      <div key={i} className="flex items-center justify-between py-1 px-2 rounded bg-slate-50 text-sm text-slate-500">
                        <span className="font-mono">{u.unlock_date}</span>
                        <span>{u.shareholder_name}</span>
                        <span className="font-mono">{formatShares(u.shares)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
