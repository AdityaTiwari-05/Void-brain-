import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search, Filter, ExternalLink, ChevronLeft, ChevronRight } from 'lucide-react'
import axios from 'axios'
import { transactionsApi } from '../../services/api'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import toast from 'react-hot-toast'

const PAGE_SIZE = 100

export default function Transactions() {
  const navigate = useNavigate()
  const [data, setData] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(false)
  const [filters, setFilters] = useState({
    sender: '', receiver: '', transaction_id: '',
    start_time: '', end_time: '', payment_mode: '', device_type: '',
    min_amount: '', max_amount: '',
  })
  const [showFilters, setShowFilters] = useState(false)
  const [selected, setSelected] = useState<any | null>(null)
  const cancelRef = useRef<any>(null)

  const fetch = useCallback(async (page: number) => {
    if (cancelRef.current) cancelRef.current.cancel()
    const source = axios.CancelToken.source()
    cancelRef.current = source
    setLoading(true)
    try {
      const params: any = { limit: PAGE_SIZE, offset: page * PAGE_SIZE }
      if (filters.sender) params.sender = filters.sender
      if (filters.receiver) params.receiver = filters.receiver
      if (filters.transaction_id) params.transaction_id = filters.transaction_id
      if (filters.start_time) params.start_time = filters.start_time
      if (filters.end_time) params.end_time = filters.end_time
      if (filters.payment_mode) params.payment_mode = filters.payment_mode
      if (filters.device_type) params.device_type = filters.device_type
      if (filters.min_amount) params.min_amount = parseFloat(filters.min_amount)
      if (filters.max_amount) params.max_amount = parseFloat(filters.max_amount)

      const result = await transactionsApi.list(params, source.token)
      setData(result.data || [])
      setTotal(result.total || 0)
    } catch (e: any) {
      if (!axios.isCancel(e)) toast.error('Failed to load transactions')
    } finally {
      setLoading(false)
    }
  }, [filters])

  useEffect(() => { setOffset(0); fetch(0) }, [filters])
  useEffect(() => { fetch(offset / PAGE_SIZE) }, [offset])
  useEffect(() => () => { if (cancelRef.current) cancelRef.current.cancel() }, [])

  const fmtAmt = (v: any) => `₹${parseFloat(v).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`
  const fmtTs  = (v: string) => new Date(v).toLocaleString('en-IN', { dateStyle: 'short', timeStyle: 'medium' })
  const pages  = Math.ceil(total / PAGE_SIZE)
  const page   = Math.floor(offset / PAGE_SIZE)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, height: '100%' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ color: 'var(--gold)', fontSize: 18, fontWeight: 700, margin: 0 }}>Transaction Analysis</h1>
          <p style={{ color: 'var(--text-muted)', fontSize: 12, margin: '2px 0 0' }}>
            {total.toLocaleString('en-IN')} transactions · Page {page + 1} of {pages || 1}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-ghost" onClick={() => setShowFilters(f => !f)}>
            <Filter size={13} /> {showFilters ? 'Hide' : 'Filters'}
          </button>
        </div>
      </div>

      {/* Filters */}
      {showFilters && (
        <div className="card" style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
          {[
            ['sender', 'Sender Account'],
            ['receiver', 'Receiver Account'],
            ['transaction_id', 'Transaction ID'],
            ['start_time', 'From (YYYY-MM-DD HH:MM:SS)'],
            ['end_time', 'To (YYYY-MM-DD HH:MM:SS)'],
            ['min_amount', 'Min Amount (₹)'],
            ['max_amount', 'Max Amount (₹)'],
          ].map(([key, placeholder]) => (
            <input
              key={key}
              className="input"
              placeholder={placeholder}
              value={(filters as any)[key]}
              onChange={e => setFilters(f => ({ ...f, [key]: e.target.value }))}
            />
          ))}
          <select
            className="input"
            value={filters.payment_mode}
            onChange={e => setFilters(f => ({ ...f, payment_mode: e.target.value }))}
          >
            <option value="">All Modes</option>
            {['UPI', 'IMPS', 'NEFT', 'RTGS'].map(m => <option key={m}>{m}</option>)}
          </select>
          <button className="btn btn-ghost" onClick={() => setFilters({
            sender: '', receiver: '', transaction_id: '',
            start_time: '', end_time: '', payment_mode: '', device_type: '',
            min_amount: '', max_amount: '',
          })}>
            Clear
          </button>
        </div>
      )}

      {/* Split view */}
      <div style={{ display: 'flex', gap: 12, flex: 1, overflow: 'hidden' }}>
        {/* Table */}
        <div className="card" style={{ flex: 1, overflow: 'auto', padding: 0 }}>
          {loading ? (
            <div style={{ padding: 32, display: 'flex', justifyContent: 'center' }}>
              <Spinner label="Loading transactions…" />
            </div>
          ) : data.length === 0 ? (
            <EmptyState title="No transactions found" description="Adjust filters or wait for ingestion." />
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Transaction ID</th>
                  <th>From</th>
                  <th>To</th>
                  <th>Amount</th>
                  <th>Timestamp</th>
                  <th>Mode</th>
                  <th>Device</th>
                  <th>IP</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {data.map(txn => (
                  <tr
                    key={txn.transaction_id}
                    onClick={() => setSelected(txn)}
                    className={selected?.transaction_id === txn.transaction_id ? 'selected' : ''}
                    style={{ cursor: 'pointer' }}
                  >
                    <td className="mono" style={{ color: 'var(--gold)', fontSize: 11 }}>{txn.transaction_id}</td>
                    <td className="mono" style={{ fontSize: 11 }}>{txn.sender_account}</td>
                    <td className="mono" style={{ fontSize: 11 }}>{txn.receiver_account}</td>
                    <td style={{ color: 'var(--risk-low)' }}>{fmtAmt(txn.amount)}</td>
                    <td style={{ fontSize: 11 }}>{fmtTs(txn.ts)}</td>
                    <td><span className="badge badge-blue">{txn.payment_mode}</span></td>
                    <td style={{ fontSize: 11 }}>{txn.device_type}</td>
                    <td className="mono" style={{ fontSize: 10, color: 'var(--text-muted)' }}>{txn.ip_address}</td>
                    <td>
                      <button
                        className="btn btn-ghost"
                        style={{ padding: '2px 6px', fontSize: 11 }}
                        onClick={e => { e.stopPropagation(); navigate(`/network?account=${txn.sender_account}`) }}
                        title="View in network"
                      >
                        <ExternalLink size={11} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Detail panel */}
        {selected && (
          <div className="card" style={{ width: 320, flexShrink: 0, overflow: 'auto' }}>
            <div className="card-header">
              <span className="card-title">Transaction Detail</span>
              <button className="btn btn-ghost" style={{ padding: '2px 6px' }} onClick={() => setSelected(null)}>✕</button>
            </div>
            <dl style={{ fontSize: 12, display: 'grid', gridTemplateColumns: '1fr 1fr', rowGap: 8, columnGap: 8 }}>
              {[
                ['ID', selected.transaction_id],
                ['Amount', fmtAmt(selected.amount)],
                ['Timestamp', selected.ts],
                ['Mode', selected.payment_mode],
                ['Sender', selected.sender_account],
                ['Receiver', selected.receiver_account],
                ['Sender IFSC', selected.sender_ifsc || '—'],
                ['Receiver IFSC', selected.receiver_ifsc || '—'],
                ['Device', selected.device_type || '—'],
                ['IP Address', selected.ip_address || '—'],
                ['Narration', selected.narration || '—'],
              ].map(([label, value]) => (
                <React.Fragment key={label}>
                  <dt style={{ color: 'var(--text-muted)' }}>{label}</dt>
                  <dd className="mono" style={{ margin: 0, wordBreak: 'break-all', color: 'var(--text-primary)' }}>{value}</dd>
                </React.Fragment>
              ))}
            </dl>
            <div className="divider" />
            <button
              className="btn btn-ghost"
              style={{ width: '100%' }}
              onClick={() => navigate(`/network?account=${selected.sender_account}`)}
            >
              <ExternalLink size={13} /> View Sender in Network
            </button>
            <button
              className="btn btn-ghost"
              style={{ width: '100%', marginTop: 6 }}
              onClick={() => navigate(`/investigation?victim=${selected.sender_account}`)}
            >
              Trace from Sender
            </button>
          </div>
        )}
      </div>

      {/* Pagination */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12 }}>
        <button className="btn btn-ghost" onClick={() => setOffset(o => Math.max(0, o - PAGE_SIZE))} disabled={offset === 0}>
          <ChevronLeft size={14} />
        </button>
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          Page {page + 1} of {pages || 1} · {total.toLocaleString('en-IN')} total
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
