import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, Download, ChevronLeft, ChevronRight, Eye, Search } from 'lucide-react'
import { detectionApi, accountsApi } from '../../services/api'
import RiskBadge from '../../components/ui/RiskBadge'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import toast from 'react-hot-toast'

const PAGE_SIZE = 100
const INITIAL_COLS = ['account_id', 'pass_through_ratio', 'incoming_amount', 'outgoing_amount', 'unique_senders', 'unique_receivers', 'ip_sample']
const EXTRA_COLS   = ['account_node_id', 'incoming_count', 'outgoing_count', 'first_seen', 'last_seen']

export default function SuspiciousAccounts() {
  const navigate = useNavigate()
  const [accounts, setAccounts] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(false)
  const [minRisk, setMinRisk] = useState(50)
  const [search, setSearch] = useState('')
  const [expandedCols, setExpandedCols] = useState(false)
  const [selected, setSelected] = useState<any | null>(null)
  const [riskDetail, setRiskDetail] = useState<any | null>(null)
  const [riskLoading, setRiskLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await accountsApi.list({
        limit: PAGE_SIZE,
        offset,
        search: search || undefined,
        sort_by: 'incoming_amount',
        order: 'desc',
      })
      setAccounts(result.data || [])
      setTotal(result.total || 0)
    } catch {
      toast.error('Failed to load accounts')
    } finally {
      setLoading(false)
    }
  }, [offset, search])

  useEffect(() => { setOffset(0) }, [search, minRisk])
  useEffect(() => { load() }, [offset, search])

  async function selectAccount(acct: any) {
    setSelected(acct)
    setRiskDetail(null)
    setRiskLoading(true)
    try {
      const risk = await detectionApi.risk(acct.account_id)
      setRiskDetail(risk)
    } catch {
      toast.error('Failed to load risk score')
    } finally {
      setRiskLoading(false)
    }
  }

  function exportCsv() {
    const cols = ['account_id', 'incoming_amount', 'outgoing_amount', 'pass_through_ratio',
                  'unique_senders', 'unique_receivers', 'first_seen', 'last_seen']
    const rows = [cols.join(',')]
    for (const a of accounts) {
      rows.push(cols.map(c => JSON.stringify((a as any)[c] ?? '')).join(','))
    }
    const blob = new Blob([rows.join('\n')], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `suspicious_accounts_${new Date().toISOString().slice(0, 10)}.csv`
    link.click()
    URL.revokeObjectURL(url)
  }

  const fmtAmt = (v: any) => v == null ? '—' : `₹${parseFloat(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
  const fmtPct = (v: any) => v == null ? '—' : `${(parseFloat(v) * 100).toFixed(1)}%`
  const pages  = Math.ceil(total / PAGE_SIZE)
  const page   = Math.floor(offset / PAGE_SIZE)

  const risk = riskDetail  // shorthand

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, height: '100%' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ color: 'var(--gold)', fontSize: 18, fontWeight: 700, margin: 0 }}>
            Suspicious Accounts
          </h1>
          <p style={{ color: 'var(--text-muted)', fontSize: 12, margin: '2px 0 0' }}>
            {total.toLocaleString('en-IN')} accounts · sorted by incoming volume
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-ghost" onClick={() => setExpandedCols(e => !e)}>
            <Eye size={13} /> {expandedCols ? 'Compact' : 'Expand Columns'}
          </button>
          <button className="btn btn-ghost" onClick={exportCsv}>
            <Download size={13} /> Export CSV
          </button>
        </div>
      </div>

      {/* Search */}
      <div style={{ display: 'flex', gap: 8 }}>
        <div style={{ position: 'relative', flex: 1, maxWidth: 360 }}>
          <Search size={13} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
          <input
            className="input"
            style={{ paddingLeft: 30 }}
            placeholder="Search account ID…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--text-muted)' }}>
          Min Risk Index:
          <input
            type="number" min={0} max={100}
            className="input" style={{ width: 70 }}
            value={minRisk}
            onChange={e => setMinRisk(Number(e.target.value))}
          />
        </label>
      </div>

      {/* Split view */}
      <div style={{ display: 'flex', gap: 12, flex: 1, overflow: 'hidden' }}>
        {/* Table */}
        <div className="card" style={{ flex: 1, overflow: 'auto', padding: 0 }}>
          {loading ? (
            <div style={{ padding: 32, display: 'flex', justifyContent: 'center' }}>
              <Spinner label="Loading accounts…" />
            </div>
          ) : accounts.length === 0 ? (
            <EmptyState title="No accounts found" description="Ingest data or adjust filters." />
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Account ID</th>
                  <th>Pass-Through %</th>
                  <th>Incoming</th>
                  <th>Outgoing</th>
                  <th>Senders</th>
                  <th>Receivers</th>
                  {expandedCols && <>
                    <th>In Txns</th>
                    <th>Out Txns</th>
                    <th>First Seen</th>
                    <th>Last Seen</th>
                  </>}
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map(a => (
                  <tr
                    key={a.account_id}
                    onClick={() => selectAccount(a)}
                    className={selected?.account_id === a.account_id ? 'selected' : ''}
                    style={{ cursor: 'pointer' }}
                  >
                    <td className="mono" style={{ color: 'var(--gold)', fontSize: 11 }}>{a.account_id}</td>
                    <td>
                      {a.pass_through_ratio != null ? (
                        <span style={{
                          color: parseFloat(a.pass_through_ratio) > 0.9 ? 'var(--risk-high)' :
                                 parseFloat(a.pass_through_ratio) > 0.7 ? 'var(--risk-medium)' : 'var(--risk-low)'
                        }}>
                          {fmtPct(a.pass_through_ratio)}
                        </span>
                      ) : '—'}
                    </td>
                    <td>{fmtAmt(a.incoming_amount)}</td>
                    <td>{fmtAmt(a.outgoing_amount)}</td>
                    <td>{a.unique_senders}</td>
                    <td>{a.unique_receivers}</td>
                    {expandedCols && <>
                      <td>{a.incoming_count}</td>
                      <td>{a.outgoing_count}</td>
                      <td style={{ fontSize: 10 }}>{a.first_seen ? new Date(a.first_seen).toLocaleDateString('en-IN') : '—'}</td>
                      <td style={{ fontSize: 10 }}>{a.last_seen ? new Date(a.last_seen).toLocaleDateString('en-IN') : '—'}</td>
                    </>}
                    <td>
                      <div style={{ display: 'flex', gap: 4 }}>
                        <button
                          className="btn btn-ghost"
                          style={{ padding: '2px 6px', fontSize: 10 }}
                          onClick={e => { e.stopPropagation(); navigate(`/network?account=${a.account_id}`) }}
                        >Graph</button>
                        <button
                          className="btn btn-primary"
                          style={{ padding: '2px 6px', fontSize: 10 }}
                          onClick={e => { e.stopPropagation(); navigate(`/investigation?victim=${a.account_id}`) }}
                        >Trace</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Risk detail panel */}
        {selected && (
          <div className="card" style={{ width: 340, flexShrink: 0, overflow: 'auto' }}>
            <div className="card-header">
              <span className="card-title">Account Detail</span>
              <button className="btn btn-ghost" style={{ padding: '2px 6px' }} onClick={() => setSelected(null)}>✕</button>
            </div>
            <p className="mono" style={{ color: 'var(--gold)', fontSize: 13, margin: '0 0 12px' }}>
              {selected.account_id}
            </p>

            {riskLoading && <Spinner label="Loading risk…" />}

            {risk && (
              <>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12 }}>
                  <span style={{ fontSize: 28, fontWeight: 700, color: 'var(--gold)' }}>
                    {risk.risk_index?.toFixed(0)}
                  </span>
                  <div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>MULE RISK INDEX</div>
                    <RiskBadge level={risk.risk_level} />
                  </div>
                </div>

                <div className="divider" />

                <div style={{ fontSize: 12, marginBottom: 12 }}>
                  <div style={{ color: 'var(--text-muted)', marginBottom: 6, fontWeight: 600 }}>Risk Components</div>
                  {risk.components?.map((c: any) => (
                    <div key={c.component} style={{
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                      padding: '4px 0', borderBottom: '1px solid var(--border)',
                    }}>
                      <span style={{ color: c.triggered ? 'var(--cream)' : 'var(--text-muted)' }}>
                        {c.triggered ? '⚠ ' : '✓ '}
                        {c.component.replace(/_/g, ' ')}
                      </span>
                      <span style={{ color: c.triggered ? 'var(--risk-high)' : 'var(--text-muted)' }}>
                        {c.score.toFixed(1)} / {c.weight}
                      </span>
                    </div>
                  ))}
                </div>

                {risk.pass_through && (
                  <div style={{ background: 'var(--bg-primary)', borderRadius: 6, padding: 10, marginBottom: 8, fontSize: 11 }}>
                    <div style={{ color: 'var(--gold)', fontWeight: 600, marginBottom: 4 }}>Pass-Through</div>
                    <div>{risk.pass_through.explanation}</div>
                    {risk.pass_through.limitation && (
                      <div style={{ color: 'var(--text-muted)', marginTop: 4, fontStyle: 'italic' }}>
                        ⚠ {risk.pass_through.limitation}
                      </div>
                    )}
                  </div>
                )}

                {risk.limitations?.length > 0 && (
                  <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 8 }}>
                    {risk.limitations.map((l: string, i: number) => (
                      <div key={i}>• {l}</div>
                    ))}
                  </div>
                )}

                <div className="divider" />
                <div style={{ display: 'flex', gap: 6 }}>
                  <button className="btn btn-primary" style={{ flex: 1, fontSize: 11 }}
                    onClick={() => navigate(`/investigation?victim=${selected.account_id}`)}>
                    4-Hop Trace
                  </button>
                  <button className="btn btn-ghost" style={{ flex: 1, fontSize: 11 }}
                    onClick={() => navigate(`/network?account=${selected.account_id}`)}>
                    Network Graph
                  </button>
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {/* Pagination */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12 }}>
        <button className="btn btn-ghost" onClick={() => setOffset(o => Math.max(0, o - PAGE_SIZE))} disabled={offset === 0}>
          <ChevronLeft size={14} />
        </button>
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          Page {page + 1} of {pages || 1}
        </span>
        <button
          className="btn btn-ghost"
          onClick={() => setOffset(o => Math.min((pages - 1) * PAGE_SIZE, o + PAGE_SIZE))}
          disabled={offset + PAGE_SIZE >= total}
        >
          <ChevronRight size={14} />
        </button>
      </div>
    </div>
  )
}
