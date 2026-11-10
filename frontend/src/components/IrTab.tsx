import { useState, useEffect, useRef } from 'react'
import { api, formatShares, formatMoney, formatNumber } from '../lib/api'
import { useT } from '../lib/i18n'

const fieldClass = 'px-3 py-2 border border-slate-300 rounded text-sm w-full'

const emptyForm = {
  shareholder_name: '', participant_id: '', participant_name: '',
  shares_baseline: '', holding_pct: '', cost_hkd: '', investor_type: '',
  unlock_date: '', notes: '',
}

function trimPct(value: number): string {
  if (!Number.isFinite(value)) return ''
  return String(Math.round(value * 10000) / 10000)
}

function showPct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `${Number(value).toFixed(2)}%`
}

export default function IrTab({ stockCode, onConfigured }: { stockCode: string; onConfigured: () => void }) {
  const [attribution, setAttribution] = useState<any[]>([])
  const [totals, setTotals] = useState<any>(null)
  const [issued, setIssued] = useState<number | null>(null)
  const [selloff, setSelloff] = useState<any[]>([])
  const [unlocks, setUnlocks] = useState<any>({ upcoming: [], past: [] })
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [overwriting, setOverwriting] = useState(false)
  const [uploadResult, setUploadResult] = useState<any>(null)
  const [dialog, setDialog] = useState<null | 'edit' | 'delete'>(null)
  const [active, setActive] = useState<any>(null)
  const [form, setForm] = useState(emptyForm)
  const [formError, setFormError] = useState('')
  const [saving, setSaving] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const lastFile = useRef<File | null>(null)
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
      setTotals(aRes.totals || null)
      setIssued(aRes.issued_shares || null)
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
    lastFile.current = file
    setUploading(true)
    setUploadResult(null)
    try {
      const result = await api.uploadFile(stockCode, file)
      setUploadResult(result)
      await load()
      onConfigured()
    } catch (err: any) {
      setUploadResult({ error: err.message })
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const handleOverwrite = async () => {
    if (!lastFile.current) return
    setOverwriting(true)
    try {
      const result = await api.uploadFile(stockCode, lastFile.current, true)
      setUploadResult(result)
      await load()
      onConfigured()
    } catch (err: any) {
      setUploadResult({ error: err.message })
    } finally {
      setOverwriting(false)
    }
  }

  const openAdd = () => {
    setActive(null)
    setForm(emptyForm)
    setFormError('')
    setDialog('edit')
  }

  const openEdit = (row: any) => {
    const pct = row.baseline_pct != null ? row.baseline_pct : row.stored_holding_pct
    setActive(row)
    setForm({
      shareholder_name: row.shareholder_name || '',
      participant_id: row.participant_id || '',
      participant_name: row.participant_name || '',
      shares_baseline: row.shares_baseline != null ? String(row.shares_baseline) : '',
      holding_pct: pct != null ? trimPct(Number(pct)) : '',
      cost_hkd: row.cost_hkd != null ? String(row.cost_hkd) : '',
      investor_type: row.investor_type || '',
      unlock_date: row.unlock_date || '',
      notes: row.notes || '',
    })
    setFormError('')
    setDialog('edit')
  }

  const onShares = (value: string) => {
    const next = { ...form, shares_baseline: value }
    if (issued && value !== '' && Number.isFinite(Number(value))) {
      next.holding_pct = trimPct(Number(value) / issued * 100)
    }
    setForm(next)
  }

  const onPct = (value: string) => {
    const next = { ...form, holding_pct: value }
    if (issued && value !== '' && Number.isFinite(Number(value))) {
      next.shares_baseline = String(Math.round(issued * Number(value) / 100))
    }
    setForm(next)
  }

  const handleSave = async () => {
    if (!form.shareholder_name.trim() || !form.participant_id.trim()) {
      setFormError(t('shareholderName') + ' / ' + t('participantId'))
      return
    }
    const payload = {
      shareholder_name: form.shareholder_name.trim(),
      participant_id: form.participant_id.trim().toUpperCase(),
      participant_name: form.participant_name.trim(),
      investor_type: form.investor_type.trim(),
      notes: form.notes.trim(),
      unlock_date: form.unlock_date.trim(),
      shares_baseline: form.shares_baseline === '' ? 0 : Number(form.shares_baseline),
      holding_pct: form.holding_pct === '' ? null : Number(form.holding_pct),
      cost_hkd: form.cost_hkd === '' ? null : Number(form.cost_hkd),
    }
    setSaving(true)
    setFormError('')
    try {
      if (active?.id) await api.updateAttribution(stockCode, active.id, payload)
      else await api.addAttribution(stockCode, payload)
      setDialog(null)
      await load()
      onConfigured()
    } catch (err: any) {
      setFormError(err.message || String(err))
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!active?.id) return
    setSaving(true)
    setFormError('')
    try {
      await api.deleteAttribution(stockCode, active.id)
      setDialog(null)
      await load()
      onConfigured()
    } catch (err: any) {
      setFormError(err.message || String(err))
    } finally {
      setSaving(false)
    }
  }

  const hasAttribution = attribution.length > 0
  const skipped = uploadResult?.skipped_manual || []

  return (
    <div className="space-y-6">
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
          <button onClick={openAdd} className="btn btn-secondary">
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

        {skipped.length > 0 && (
          <div className="mt-4 p-3 rounded-lg bg-amber-50 text-amber-900 text-sm">
            <p>{uploadResult.import_warning || t('importKept', { count: skipped.length })}</p>
            <ul className="mt-2 text-xs space-y-1">
              {skipped.map((row: any) => (
                <li key={row.id}>• {row.shareholder_name} ({row.participant_id})</li>
              ))}
            </ul>
            <button onClick={handleOverwrite} disabled={overwriting} className="btn btn-secondary mt-3">
              {overwriting ? t('uploadingParsing') : t('overwriteManual')}
            </button>
          </div>
        )}
      </div>

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
                    <th className="py-2 text-right">{t('holdingPct')}</th>
                    <th className="py-2 text-right">{t('changeFromBaseline')}</th>
                    <th className="py-2 text-right">{t('change30d')}</th>
                    <th className="py-2">{t('cost')}</th>
                    <th className="py-2">{t('unlockDate')}</th>
                    <th className="py-2">{t('notes')}</th>
                    <th className="py-2">{t('actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {attribution.map((s: any) => (
                    <tr key={s.id || s.shareholder_name + s.participant_id} className="border-b border-slate-100">
                      <td className="py-2">
                        <div className="font-medium text-slate-800">{s.shareholder_name}</div>
                        {s.investor_type && <div className="text-xs text-slate-400">{s.investor_type}</div>}
                        {s.manually_edited ? <span className="badge badge-yellow">{t('editedBadge')}</span> : null}
                      </td>
                      <td className="py-2">
                        <div className="text-xs">{brokerName(s.participant_id, s.participant_name)}</div>
                        <div className="text-xs font-mono text-slate-400">{s.participant_id}</div>
                      </td>
                      <td className="py-2 text-right font-mono">{formatShares(s.shares_current)}</td>
                      <td className="py-2 text-right font-mono text-slate-500">{formatShares(s.shares_baseline)}</td>
                      <td className="py-2 text-right font-mono">{showPct(s.holding_pct)}</td>
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
                      <td className="py-2 text-xs text-slate-500 max-w-[140px] truncate">{s.notes || '-'}</td>
                      <td className="py-2 whitespace-nowrap">
                        <button onClick={() => openEdit(s)} className="text-blue-600 hover:underline mr-3">{t('edit')}</button>
                        <button
                          onClick={() => { setActive(s); setFormError(''); setDialog('delete') }}
                          className="text-red-600 hover:underline"
                        >{t('delete')}</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
                {totals && (
                  <tfoot>
                    <tr className="border-t font-medium text-slate-700">
                      <td className="py-2">{t('totalRow')}</td>
                      <td className="py-2 text-slate-400">{totals.count}</td>
                      <td className="py-2 text-right font-mono">{formatShares(totals.shares_current)}</td>
                      <td className="py-2 text-right font-mono">{formatShares(totals.shares_baseline)}</td>
                      <td className="py-2 text-right font-mono">{showPct(totals.holding_pct)}</td>
                      <td colSpan={6} />
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          </div>

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

      {dialog === 'edit' && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
          <div className="bg-white rounded-lg shadow-lg w-full max-w-lg max-h-[90vh] overflow-y-auto p-5">
            <h3 className="font-semibold text-slate-800 mb-1">{active ? t('editShareholder') : t('manualAddTitle')}</h3>
            <p className="text-xs text-slate-500 mb-3">
              {issued ? t('pctHint', { issued: formatNumber(issued) }) : t('pctHintNoIssued')}
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <input className={fieldClass} placeholder={`${t('shareholderName')} *`} value={form.shareholder_name} onChange={e => setForm({ ...form, shareholder_name: e.target.value })} />
              <input className={`${fieldClass} font-mono`} placeholder={`${t('participantId')} *`} value={form.participant_id} onChange={e => setForm({ ...form, participant_id: e.target.value.toUpperCase() })} />
              <input className={fieldClass} placeholder={t('participantName')} value={form.participant_name} onChange={e => setForm({ ...form, participant_name: e.target.value })} />
              <input className={fieldClass} placeholder={t('investorType')} value={form.investor_type} onChange={e => setForm({ ...form, investor_type: e.target.value })} />
              <input className={fieldClass} type="number" min="0" step="1" placeholder={t('sharesBaseline')} value={form.shares_baseline} onChange={e => onShares(e.target.value)} />
              <input className={fieldClass} type="number" min="0" max="100" step="0.0001" placeholder={t('holdingPct')} value={form.holding_pct} onChange={e => onPct(e.target.value)} />
              <input className={fieldClass} type="number" min="0" step="0.01" placeholder={t('costHkd')} value={form.cost_hkd} onChange={e => setForm({ ...form, cost_hkd: e.target.value })} />
              <input className={fieldClass} placeholder={t('unlockDate')} value={form.unlock_date} onChange={e => setForm({ ...form, unlock_date: e.target.value })} />
            </div>
            <textarea
              className={`${fieldClass} mt-3`}
              rows={3}
              placeholder={t('notes')}
              value={form.notes}
              onChange={e => setForm({ ...form, notes: e.target.value })}
            />
            {formError && <p className="text-sm text-red-600 mt-2">{formError}</p>}
            <div className="flex gap-2 mt-4">
              <button onClick={handleSave} disabled={saving} className="btn btn-primary">{saving ? t('loading') : t('saveChanges')}</button>
              <button onClick={() => setDialog(null)} className="btn btn-secondary">{t('cancel')}</button>
            </div>
          </div>
        </div>
      )}

      {dialog === 'delete' && active && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
          <div className="bg-white rounded-lg shadow-lg w-full max-w-md p-5">
            <h3 className="font-semibold text-slate-800 mb-2">{t('deleteShareholderTitle')}</h3>
            <p className="text-sm text-slate-600">
              {t('deleteShareholderBody', { name: active.shareholder_name, seat: active.participant_id })}
            </p>
            {formError && <p className="text-sm text-red-600 mt-2">{formError}</p>}
            <div className="flex gap-2 mt-4">
              <button onClick={handleDelete} disabled={saving} className="btn btn-danger">{saving ? t('loading') : t('confirmDelete')}</button>
              <button onClick={() => setDialog(null)} className="btn btn-secondary">{t('cancel')}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
