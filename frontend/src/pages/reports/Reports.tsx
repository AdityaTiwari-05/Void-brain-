import React, { useState } from 'react'
import { Download, FileText, Table2, BarChart3 } from 'lucide-react'
import { accountsApi, transactionsApi, detectionApi } from '../../services/api'
import Spinner from '../../components/ui/Spinner'
import toast from 'react-hot-toast'

export default function Reports() {
  const [loading, setLoading] = useState<string | null>(null)

  async function exportSuspiciousAccounts() {
    setLoading('suspicious')
    try {
      const result = await accountsApi.list({ limit: 1000, sort_by: 'incoming_amount', order: 'desc' })
      const accounts = result.data || []
      const cols = [
        'account_id', 'incoming_amount', 'outgoing_amount', 'pass_through_ratio',
        'unique_senders', 'unique_receivers', 'incoming_count', 'outgoing_count',
        'first_seen', 'last_seen',
      ]
      const rows = [cols.join(',')]
      for (const a of accounts) {
        rows.push(cols.map(c => JSON.stringify((a as any)[c] ?? '')).join(','))
      }
      const blob = new Blob([rows.join('\n')], { type: 'text/csv' })
      downloadBlob(blob, `suspicious_accounts_${today()}.csv`)
      toast.success(`Exported ${accounts.length} accounts`)
    } catch { toast.error('Export failed') } finally { setLoading(null) }
  }

  async function exportTransactions(limit = 10000) {
    setLoading('transactions')
    try {
      const result = await transactionsApi.list({ limit, offset: 0 })
      const txns = result.data || []
      const cols = ['transaction_id', 'sender_account', 'receiver_account', 'amount', 'ts', 'payment_mode', 'device_type', 'ip_address', 'sender_ifsc', 'receiver_ifsc']
      const rows = [cols.join(',')]
      for (const t of txns) {
        rows.push(cols.map(c => JSON.stringify((t as any)[c] ?? '')).join(','))
      }
      const blob = new Blob([rows.join('\n')], { type: 'text/csv' })
      downloadBlob(blob, `transactions_${today()}.csv`)
      toast.success(`Exported ${txns.length} transactions`)
    } catch { toast.error('Export failed') } finally { setLoading(null) }
  }

  function downloadBlob(blob: Blob, name: string) {
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = name; a.click()
    URL.revokeObjectURL(url)
  }

  const today = () => new Date().toISOString().slice(0, 10)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h1 style={{ color: 'var(--gold)', fontSize: 18, fontWeight: 700, margin: 0 }}>Reports & Exports</h1>
        <p style={{ color: 'var(--text-muted)', fontSize: 12, margin: '2px 0 0' }}>
          Export actual backend data — no fabricated values
        </p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
        {/* Accounts report */}
        <div className="card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <Table2 size={18} style={{ color: 'var(--gold)' }} />
            <div>
              <div style={{ fontWeight: 600, fontSize: 13 }}>Suspicious Accounts</div>
              <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>All accounts with aggregates</div>
            </div>
          </div>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
            Exports account IDs, incoming/outgoing amounts, pass-through ratios,
            counterparty counts and timestamps.
          </p>
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="btn btn-primary" onClick={exportSuspiciousAccounts} disabled={loading === 'suspicious'}>
              {loading === 'suspicious' ? <Spinner size={12} /> : <Download size={12} />}
              Export CSV
            </button>
          </div>
        </div>

        {/* Transactions report */}
        <div className="card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <BarChart3 size={18} style={{ color: 'var(--gold)' }} />
            <div>
              <div style={{ fontWeight: 600, fontSize: 13 }}>Transaction Analysis</div>
              <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>Recent 10,000 transactions</div>
            </div>
          </div>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
            Exports transaction IDs, accounts, amounts, timestamps, payment modes,
            device types and IP addresses.
          </p>
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="btn btn-primary" onClick={() => exportTransactions(10000)} disabled={loading === 'transactions'}>
              {loading === 'transactions' ? <Spinner size={12} /> : <Download size={12} />}
              Export CSV (10k)
            </button>
          </div>
        </div>

        {/* Investigation report */}
        <div className="card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <FileText size={18} style={{ color: 'var(--gold)' }} />
            <div>
              <div style={{ fontWeight: 600, fontSize: 13 }}>Investigation Documents</div>
              <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>Case diary & Freeze requisition</div>
            </div>
          </div>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
            Generate case diary and freeze requisition drafts from the AI Case Officer module.
            All documents are evidence-validated before export.
          </p>
          <div style={{ display: 'flex', gap: 6 }}>
            <a className="btn btn-ghost" href="/ai-officer" style={{ fontSize: 12 }}>
              Open AI Officer →
            </a>
          </div>
        </div>
      </div>

      {/* Notice */}
      <div style={{
        background: 'rgba(245,158,11,0.05)',
        border: '1px solid rgba(245,158,11,0.2)',
        borderRadius: 6, padding: 12, fontSize: 12, color: 'var(--text-muted)',
      }}>
        <strong style={{ color: 'var(--risk-medium)' }}>⚠ Data integrity notice:</strong>
        {' '}All exports contain actual backend database values.
        No fabricated or calculated values are added. Exported files are for investigative use only
        and require authorized officer review before official use.
      </div>
    </div>
  )
}
