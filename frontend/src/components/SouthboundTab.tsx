import { useEffect, useState } from 'react'
import { api, formatNumber } from '../lib/api'
import { useT } from '../lib/i18n'
import {
  CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'

const COPY = {
  zh: {
    collect: '采集南下拆解',
    collecting: '采集中',
    reload: '刷新',
    empty: '还没有南下拆解数据。点「采集」后会拉取南下合计、关注名单里的公募持仓，以及近期权益披露。',
    latest: '最新南下持股',
    change: '区间变动',
    explained: '已确认解释',
    coverage: '覆盖率',
    unknown: '未识别南下资金',
    asOf: '对齐报告期',
    stale: '基金报告滞后天数',
    chart: '南下持股与收盘价',
    confirmed: '确认经港股通',
    possible: '可能经港股通（不计入覆盖率）',
    qdii: '非港股通公募（QDII / 海外股票型，不计入覆盖率）',
    di: '权益披露（不计入南下，禁止加总）',
    quarters: '季度公募加减仓归因',
    abnormal: '异常日',
    check: '席位交叉验证',
    conc: '已识别持仓集中度',
    price: '与股价的关系',
    volume: '与成交的关系',
    limits: '口径与局限',
    compliance: '数据源合规',
    holder: '机构',
    shares: '持股（股）',
    delta: '报告期变动',
    source: '来源',
    report: '报告期 / 事件日',
    confidence: '置信度',
    tag: '渠道',
    noRow: '这一栏没有记录。',
    rangeAll: '全部',
    misaligned: '下面这个数不可比',
  },
  en: {
    collect: 'Collect',
    collecting: 'Collecting',
    reload: 'Reload',
    empty: 'No southbound breakdown yet. Collect pulls the aggregate, the focus-fund holdings, and recent disclosure filings.',
    latest: 'Latest southbound shares',
    change: 'Change in range',
    explained: 'Confirmed explanation',
    coverage: 'Coverage',
    unknown: 'Unidentified southbound',
    asOf: 'Aligned report date',
    stale: 'Fund report lag (days)',
    chart: 'Southbound shares and close',
    confirmed: 'Confirmed Stock Connect',
    possible: 'Possibly Stock Connect (excluded from coverage)',
    qdii: 'Not Stock Connect funds (excluded)',
    di: 'Disclosure of interests (excluded, do not sum)',
    quarters: 'Quarterly fund attribution',
    abnormal: 'Unusual days',
    check: 'Seat cross-check',
    conc: 'Concentration of identified holdings',
    price: 'Relation to price',
    volume: 'Relation to volume',
    limits: 'Scope and limits',
    compliance: 'Source compliance',
    holder: 'Holder',
    shares: 'Shares',
    delta: 'Change vs prior report',
    source: 'Source',
    report: 'Report / event date',
    confidence: 'Confidence',
    tag: 'Channel',
    noRow: 'Nothing in this group.',
    rangeAll: 'All',
    misaligned: 'Not comparable',
  },
}

function pct(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '-'
  return (n * 100).toFixed(2) + '%'
}

function exactShares(n: number | null | undefined, signed = false): string {
  if (n == null) return '未知'
  const body = Math.abs(n).toLocaleString('zh-CN') + '股'
  if (!signed) return body
  if (n > 0) return '+' + body
  if (n < 0) return '-' + body
  return body
}

function HolderTable({ rows, copy }: { rows: any[]; copy: typeof COPY.zh }) {
  if (!rows?.length) return <p className="text-sm text-slate-400">{copy.noRow}</p>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-slate-500 border-b">
            <th className="py-2 pr-3">{copy.holder}</th>
            <th className="py-2 pr-3">{copy.tag}</th>
            <th className="py-2 pr-3 text-right">{copy.shares}</th>
            <th className="py-2 pr-3 text-right">{copy.delta}</th>
            <th className="py-2 pr-3">{copy.report}</th>
            <th className="py-2 pr-3 text-right">{copy.confidence}</th>
            <th className="py-2">{copy.source}</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 20).map((row) => (
            <tr key={`${row.holder_key}-${row.report_date}-${row.position_type}-${row.source_ref}`} className="border-b border-slate-100 align-top">
              <td className="py-2 pr-3">
                <div className="font-medium text-slate-800">{row.holder_name}</div>
                <div className="text-xs text-slate-400">{row.position_type === 'L' ? '好仓' : row.position_type === 'S' ? '淡仓' : row.position_type} · {row.report_scope}</div>
              </td>
              <td className="py-2 pr-3 whitespace-nowrap">{row.channel_label}</td>
              <td className="py-2 pr-3 text-right whitespace-nowrap">{exactShares(row.shares)}</td>
              <td className="py-2 pr-3 text-right whitespace-nowrap">
                {exactShares(row.shares_change, true)}
                <div className="text-xs text-slate-400 max-w-[180px] ml-auto">
                  {row.previous_report_date ? `上一披露 ${row.previous_report_date}。` : ''}{row.change_note}
                </div>
              </td>
              <td className="py-2 pr-3 whitespace-nowrap">{row.report_date}</td>
              <td className="py-2 pr-3 text-right">{row.confidence?.toFixed?.(2) ?? row.confidence}</td>
              <td className="py-2 text-xs text-slate-500 max-w-xs">{row.source}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function SouthboundTab({ stockCode }: { stockCode: string }) {
  const { lang } = useT()
  const copy = lang === 'en' ? COPY.en : COPY.zh
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [range, setRange] = useState<number | 'all'>('all')
  const [collecting, setCollecting] = useState(false)
  const [progress, setProgress] = useState<any>(null)

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      setData(await api.getSouthbound(stockCode))
    } catch (err: any) {
      setError(err.message || 'failed')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [stockCode])

  const collect = async () => {
    setCollecting(true)
    setError('')
    try {
      const job = await api.collectSouthbound(stockCode, 'focus')
      const poll = async () => {
        const status = await api.getJobStatus(job.job_id)
        setProgress(status)
        if (status.status === 'done') {
          setCollecting(false)
          await load()
          return
        }
        if (status.status === 'error') {
          setCollecting(false)
          setError(status.error || 'collect failed')
          return
        }
        setTimeout(poll, 2000)
      }
      poll()
    } catch (err: any) {
      setCollecting(false)
      setError(err.message || 'collect failed')
    }
  }

  const series = data?.series || []
  const shown = range === 'all' ? series : series.slice(-range)
  const snap = data?.snapshot
  const windowChange = shown.length >= 2 ? shown[shown.length - 1].shares - shown[0].shares : null
  const chartData = shown.map((row: any) => ({
    date: row.trade_date.slice(5),
    shares: row.shares,
    close: row.close_price,
  }))

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex gap-1">
          {([60, 180, 'all'] as const).map((item) => (
            <button
              key={String(item)}
              onClick={() => setRange(item)}
              className={`px-3 py-1 text-sm rounded-md border ${range === item ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-slate-600 border-slate-300'}`}
            >
              {item === 'all' ? copy.rangeAll : `${item}D`}
            </button>
          ))}
        </div>
        <div className="flex gap-2">
          <button onClick={load} className="btn btn-secondary text-sm">{copy.reload}</button>
          <button onClick={collect} disabled={collecting} className="btn btn-primary text-sm disabled:opacity-50">
            {collecting ? `${copy.collecting} ${progress?.progress ?? 0}%` : copy.collect}
          </button>
        </div>
      </div>

      {error && <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg">{error}</div>}
      {loading ? <div className="text-slate-400 py-16 text-center">加载中...</div> : !data?.has_data ? (
        <div className="card text-slate-600">{copy.empty}</div>
      ) : (
        <>
          <div className="bg-amber-50 border border-amber-200 text-amber-900 text-sm px-4 py-3 rounded-lg">
            {copy.compliance}：当前默认源是东方财富/天天基金和港交所公开页面，只适合个人非商业核对。覆盖率分子只计「确认经港股通」。权益披露和 QDII 单独列出，不并进南下。
          </div>

          <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
            <Kpi label={copy.latest} value={exactShares(snap.latest_southbound_shares)} hint={snap.latest_southbound_date} />
            <Kpi label={copy.change} value={exactShares(windowChange, true)} hint={shown.length ? `${shown[0].trade_date} → ${shown[shown.length - 1].trade_date}` : ''} />
            <Kpi label={copy.explained} value={exactShares(snap.explained_shares)} hint={snap.report_date || '-'} />
            <Kpi label={copy.coverage} value={pct(snap.coverage)} hint={`${copy.asOf} ${snap.aligned_southbound_date || '-'} · ${snap.alignment_quality}`} />
            <Kpi label={copy.unknown} value={exactShares(snap.unidentified_shares)} hint={snap.over_explained ? '已确认持仓超过南下合计' : `${copy.stale} ${snap.staleness_days ?? '-'}`} />
          </div>

          <div className="card">
            <div className="flex justify-between text-sm text-slate-500 mb-1">
              <span>已确认 {pct(snap.coverage)}</span>
              <span>未识别 {snap.southbound_shares ? pct(1 - Math.min(snap.coverage || 0, 1)) : '-'}</span>
            </div>
            <div className="h-3 bg-slate-100 rounded-full overflow-hidden flex">
              <div className="h-full bg-blue-600" style={{ width: `${Math.min((snap.coverage || 0) * 100, 100)}%` }} />
            </div>
            <p className="text-xs text-slate-500 mt-2">
              分母是 {snap.aligned_southbound_date} 的南下持股 {exactShares(snap.southbound_shares)}，不是 {snap.latest_southbound_date} 的最新持股。
              {snap.misaligned_coverage ? ` ${copy.misaligned}：若用最新持股当除数，覆盖率会变成 ${pct(snap.misaligned_coverage.coverage)}。${snap.misaligned_coverage.warning}` : ''}
            </p>
          </div>

          <div className="card">
            <h3 className="font-semibold text-slate-800 mb-3">{copy.chart}</h3>
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={24} />
                  <YAxis yAxisId="shares" tick={{ fontSize: 11 }} tickFormatter={(v) => formatNumber(v)} />
                  <YAxis yAxisId="price" orientation="right" tick={{ fontSize: 11 }} />
                  <Tooltip formatter={(value: any, name: any) => name === 'shares' ? exactShares(Number(value)) : value} />
                  <Legend />
                  <Line yAxisId="shares" type="monotone" dataKey="shares" name={lang === 'en' ? 'Southbound shares' : '南下持股'} stroke="#2563eb" dot={false} strokeWidth={2} />
                  <Line yAxisId="price" type="monotone" dataKey="close" name={lang === 'en' ? 'Close' : '收盘价'} stroke="#f97316" dot={false} strokeWidth={1.5} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </div>

          <section className="card">
            <h3 className="font-semibold text-slate-800 mb-3">{copy.confirmed}</h3>
            <HolderTable rows={data.holders.confirmed} copy={copy} />
          </section>
          <section className="card">
            <h3 className="font-semibold text-slate-800 mb-3">{copy.possible}</h3>
            <p className="text-xs text-slate-500 mb-2">{snap.possible_note} 同一报告期合计 {exactShares(snap.possible_shares_same_report)}，只作参考。</p>
            <HolderTable rows={data.holders.possible} copy={copy} />
          </section>
          <section className="card">
            <h3 className="font-semibold text-slate-800 mb-3">{copy.qdii}</h3>
            <HolderTable rows={data.holders.non_connect_funds} copy={copy} />
          </section>
          <section className="card">
            <h3 className="font-semibold text-slate-800 mb-3">{copy.di}</h3>
            <HolderTable rows={data.holders.disclosures} copy={copy} />
          </section>

          <section className="card">
            <h3 className="font-semibold text-slate-800 mb-3">{copy.quarters}</h3>
            <div className="space-y-3">
              {(data.analysis.quarterly_attribution || []).slice(-4).reverse().map((row: any) => (
                <div key={`${row.report_start}-${row.report_end}`} className="border border-slate-100 rounded-md p-3">
                  <div className="text-sm font-medium text-slate-800">
                    {row.report_start} → {row.report_end}
                  </div>
                  <div className="text-sm text-slate-600 mt-1">
                    南下变动 {exactShares(row.southbound_change, true)}（{row.southbound_date_start} → {row.southbound_date_end}）
                    ，已识别公募变动 {exactShares(row.confirmed_known_change, true)}
                    ，解释比例 {row.explained_fraction_of_change == null ? '不适用' : pct(row.explained_fraction_of_change)}
                    。可比基金 {row.known_funds} 只，因前十大缺口未计入 {row.unknown_funds} 只。
                  </div>
                  <div className="text-xs text-slate-400 mt-1">{row.note}</div>
                </div>
              ))}
            </div>
          </section>

          <div className="grid lg:grid-cols-2 gap-4">
            <section className="card">
              <h3 className="font-semibold text-slate-800 mb-2">{copy.price}</h3>
              <p className="text-sm text-slate-600">{data.analysis.price_relationship.interpretation}</p>
              <p className="text-xs text-slate-400 mt-1">n={data.analysis.price_relationship.n}，r={data.analysis.price_relationship.pearson_r ?? '-'}</p>
              <h3 className="font-semibold text-slate-800 mt-4 mb-2">{copy.volume}</h3>
              <p className="text-sm text-slate-600">{data.analysis.volume_relationship.interpretation || data.analysis.volume_relationship.reason}</p>
            </section>
            <section className="card">
              <h3 className="font-semibold text-slate-800 mb-2">{copy.conc}</h3>
              {data.analysis.concentration.available ? (
                <p className="text-sm text-slate-600">
                  已识别 {data.analysis.concentration.n_funds} 只基金，HHI {data.analysis.concentration.hhi}，
                  最大 {data.analysis.concentration.top1_name} 占已识别部分 {pct(data.analysis.concentration.top1_share_of_explained)}。
                  {data.analysis.concentration.note}
                </p>
              ) : <p className="text-sm text-slate-500">{data.analysis.concentration.reason}</p>}
              <h3 className="font-semibold text-slate-800 mt-4 mb-2">{copy.check}</h3>
              <CrossCheck check={data.analysis.cross_check} />
            </section>
          </div>

          <section className="card">
            <h3 className="font-semibold text-slate-800 mb-3">{copy.abnormal}</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-slate-500 border-b">
                    <th className="py-2">日期</th>
                    <th className="py-2 text-right">持股变动</th>
                    <th className="py-2 text-right">z</th>
                    <th className="py-2">方向</th>
                    <th className="py-2 text-right">涨跌幅</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.analysis.abnormal_days || []).slice(0, 8).map((row: any) => (
                    <tr key={row.trade_date} className="border-b border-slate-100">
                      <td className="py-2">{row.trade_date}</td>
                      <td className="py-2 text-right">{exactShares(row.share_change, true)}</td>
                      <td className="py-2 text-right">{row.z}</td>
                      <td className="py-2">{row.direction}</td>
                      <td className="py-2 text-right">{row.change_pct == null ? '-' : row.change_pct.toFixed(2) + '%'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="card">
            <h3 className="font-semibold text-slate-800 mb-2">{copy.limits}</h3>
            <ul className="text-sm text-slate-600 list-disc pl-5 space-y-1">
              {(data.limitations || []).map((item: string) => <li key={item}>{item}</li>)}
              {(data.analysis.design_notes || []).map((item: string) => <li key={item}>{item}</li>)}
            </ul>
          </section>
        </>
      )}
    </div>
  )
}

function Kpi({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="card">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="text-lg font-semibold text-slate-900 mt-1">{value}</div>
      {hint && <div className="text-xs text-slate-400 mt-1">{hint}</div>}
    </div>
  )
}

function CrossCheck({ check }: { check: any }) {
  if (!check?.available) return <p className="text-sm text-slate-500">{check?.reason || '没有可核对的席位。'}</p>
  return (
    <div className="text-sm text-slate-600 space-y-1">
      {check.seat_sum != null && (
        <p>
          A00003 {exactShares(check.a00003_shares)} + A00004 {exactShares(check.a00004_shares)} = {exactShares(check.seat_sum)}
          ，与南下合计相差 {exactShares(check.diff_vs_daily, true)}。{check.match_daily ? '一致。' : '不一致。'}
        </p>
      )}
      {check.official_shares != null && (
        <p>港交所官方合计 {exactShares(check.official_shares)}。{check.match_official ? '与席位或日频合计一致。' : '与日频合计不一致。'}</p>
      )}
      {check.panel_ccass && <p className="text-xs text-slate-400">{check.panel_ccass}</p>}
      {check.match_panel != null && <p>面板 CCASS 两席合计相差 {exactShares(check.diff_vs_panel, true)}。</p>}
    </div>
  )
}
