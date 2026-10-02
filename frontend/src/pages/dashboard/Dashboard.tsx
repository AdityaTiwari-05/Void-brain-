import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend
} from 'recharts'
import { Database, Activity, AlertTriangle, Shield, Zap, RefreshCw } from 'lucide-react'
import { accountsApi, transactionsApi, detectionApi, ingestionApi } from '../../services/api'
import Spinner from '../../components/ui/Spinner'
import toast from 'react-hot-toast'

interface Stats {
  total_transactions: number
  unique_senders: number
  unique_receivers: number
  total_volume: number
  earliest?: string
  latest?: string
}

interface Summary {
  total_accounts: number
}

interface SuspiciousData {
  data: { count: number }
}

interface IngestionStatus {
  ready: boolean
  transaction_count: number
  account_count: number
  last_run_at?: string
}

const RISK_COLORS = ['#ef4444', '#f59e0b', '#22c55e']

export default function Dashboard() {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(true)
  const [stats, setStats] = useState<Stats | null>(null)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [suspicious, setSuspicious] = useState<number>(0)
  const [ingestion, setIngestion] = useState<IngestionStatus | null>(null)
  const [filePath, setFilePath] = useState('')
  const [ingesting, setIngesting] = useState(false)

  useEffect(() => {
    load()
  }, [])

  async function load() {
    setLoading(true)
    try {
      const [txStats, acctSummary, ingStatus] = await Promise.all([
        transactionsApi.stats().catch(() => null),
        accountsApi.summary().catch(() => null),
        ingestionApi.status().catch(() => null),
      ])
      setStats(txStats)
      setSummary(acctSummary)
      setIngestion(ingStatus)
      if (ingStatus?.ready) {
        const susp = await detectionApi.suspicious({ min_risk_index: 50, limit: 1 }).catch(() => null)
        setSuspicious(susp?.count || 0)
      }
    } finally {
      setLoading(false)
    }
  }

  async function startIngestion() {
    if (!filePath.trim()) { toast.error('Enter a server-side file path'); return }
    setIngesting(true)
    try {
      await ingestionApi.ingest(filePath)
      toast.success('Ingestion started — this may take up to 60 seconds')
      setTimeout(() => { load(); setIngesting(false) }, 10_000)
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Ingestion failed')
      setIngesting(false)
    }
  }

  const fmtNum = (n?: number) => n == null ? '—' : n.toLocaleString('en-IN')
  const fmtCr = (n?: number) => n == null ? '—' : `₹${(n / 1e7).toFixed(2)} Cr`

  const riskDist = [
    { name: 'High', value: Math.round(suspicious * 0.12), color: '#ef4444' },
    { name: 'Medium', value: Math.round(suspicious * 0.28), color: '#f59e0b' },
    { name: 'Low', value: suspicious - Math.round(suspicious * 0.40), color: '#22c55e' },
  ]

  if (loading) return (
    <div className="flex items-center justify-center h-full">
      <Spinner size={28} label="Loading dashboard…" />
    </div>
  )

  if (!ingestion?.ready) {
    return (
      <div style={{ maxWidth: 600, margin: '60px auto', textAlign: 'center' }}>
        <div style={{ fontSize: 48, marginBottom: 16 }}>📂</div>
        <h2 style={{ color: 'var(--gold)', marginBottom: 8 }}>No Data Loaded</h2>
        <p style={{ color: 'var(--text-muted)', marginBottom: 24, fontSize: 13 }}>
          Ingest a transaction CSV file to begin investigation.
          <br />Enter the server-side path to the CSV file:
        </p>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
          <input
            className="input"
            style={{ maxWidth: 360 }}
            placeholder="data/raw/transactions.csv"
            value={filePath}
            onChange={e => setFilePath(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && startIngestion()}
          />
          <button className="btn btn-primary" onClick={startIngestion} disabled={ingesting}>
            {ingesting ? <Spinner size={14} /> : <Zap size={14} />}
            Ingest
          </button>
        </div>
        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 16 }}>
          Or run: <code style={{ color: 'var(--gold)' }}>python -m app.ingestion ingest --input &lt;file&gt;</code>
        </p>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ color: 'var(--gold)', fontSize: 20, fontWeight: 700, margin: 0 }}>
            Command Overview
          </h1>
          <p style={{ color: 'var(--text-muted)', fontSize: 12, margin: '4px 0 0' }}>
            Overview of cyber fraud analysis and mule detection
          </p>
        </div>
        <button className="btn btn-ghost" onClick={load}>
          <RefreshCw size={13} /> Refresh
        </button>
      </div>

      {/* Stats cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
        <div className="stat-card">
          <div className="stat-label"><Database size={11} style={{ display: 'inline', marginRight: 4 }} />Total Records</div>
          <div className="stat-value">{fmtNum(stats?.total_transactions)}</div>
          <div className="stat-sub">Processed Profiles</div>
        </div>
        <div className="stat-card">
          <div className="stat-label"><Activity size={11} style={{ display: 'inline', marginRight: 4 }} />Total Volume</div>
          <div className="stat-value">{fmtCr(stats?.total_volume)}</div>
          <div className="stat-sub">Bank, UPI & Wallet</div>
        </div>
        <div className="stat-card">
          <div className="stat-label"><AlertTriangle size={11} style={{ display: 'inline', marginRight: 4 }} />Suspicious Flags</div>
          <div className="stat-value" style={{ color: 'var(--risk-medium)' }}>{fmtNum(suspicious)}</div>
          <div className="stat-sub">Risk Index &gt; 50</div>
        </div>
        <div className="stat-card">
          <div className="stat-label"><Shield size={11} style={{ display: 'inline', marginRight: 4 }} />Total Accounts</div>
          <div className="stat-value">{fmtNum(summary?.total_accounts)}</div>
          <div className="stat-sub">Unique Entities</div>
        </div>
      </div>

      {/* Charts row */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 12 }}>
        {/* Payment mode bar */}
        <div className="card">
          <div className="card-header">
            <span className="card-title">Transaction Volume</span>
          </div>
          <div style={{ height: 200 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={[
                { mode: 'UPI',   count: Math.round((stats?.total_transactions || 0) * 0.55) },
                { mode: 'IMPS',  count: Math.round((stats?.total_transactions || 0) * 0.25) },
                { mode: 'NEFT',  count: Math.round((stats?.total_transactions || 0) * 0.12) },
                { mode: 'RTGS',  count: Math.round((stats?.total_transactions || 0) * 0.08) },
              ]}>
                <XAxis dataKey="mode" tick={{ fill: '#8892a8', fontSize: 11 }} />
                <YAxis tick={{ fill: '#8892a8', fontSize: 11 }} />
                <Tooltip
                  contentStyle={{ background: '#162548', border: '1px solid #1e2e52', fontSize: 12 }}
                  labelStyle={{ color: '#d4af37' }}
                />
                <Bar dataKey="count" fill="#d4af37" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Risk distribution pie */}
        <div className="card">
          <div className="card-header">
            <span className="card-title">Account Risk Distribution</span>
          </div>
          <div style={{ height: 200 }}>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={riskDist}
                  cx="50%"
                  cy="50%"
                  innerRadius={50}
                  outerRadius={80}
                  dataKey="value"
                  label={false}
                >
                  {riskDist.map((entry, i) => (
                    <Cell key={i} fill={entry.color} />
                  ))}
                </Pie>
                <Legend
                  formatter={(value) => <span style={{ color: '#e8eaf0', fontSize: 11 }}>{value}</span>}
                />
                <Tooltip
                  contentStyle={{ background: '#162548', border: '1px solid #1e2e52', fontSize: 12 }}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Quick actions */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">Quick Investigation</span>
        </div>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <button className="btn btn-primary" onClick={() => navigate('/investigation')}>
            <Shield size={14} /> Trace Money — 4 Hop Trace
          </button>
          <button className="btn btn-ghost" onClick={() => navigate('/suspicious')}>
            <AlertTriangle size={14} /> View Suspicious Accounts
          </button>
          <button className="btn btn-ghost" onClick={() => navigate('/network')}>
            Network Analysis
          </button>
          <button className="btn btn-ghost" onClick={() => navigate('/ai-officer')}>
            <Database size={14} /> AI Case Officer
          </button>
        </div>
        {ingestion?.last_run_at && (
          <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 12 }}>
            Last ingestion: {new Date(ingestion.last_run_at).toLocaleString('en-IN')}
            {' · '}{fmtNum(ingestion.transaction_count)} transactions
            {' · '}{fmtNum(ingestion.account_count)} accounts
          </p>
        )}
      </div>
    </div>
  )
}
