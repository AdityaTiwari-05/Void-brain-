import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import CytoscapeComponent from 'react-cytoscapejs'
import Cytoscape from 'cytoscape'
import { Search, ZoomIn, ZoomOut, Maximize2, RefreshCw, Layers } from 'lucide-react'
import { accountsApi, detectionApi } from '../../services/api'
import Spinner from '../../components/ui/Spinner'
import RiskBadge from '../../components/ui/RiskBadge'
import EmptyState from '../../components/ui/EmptyState'
import toast from 'react-hot-toast'

// Cytoscape stylesheet
const CY_STYLE: Cytoscape.Stylesheet[] = [
  {
    selector: 'node',
    style: {
      'background-color': '#1e2e52',
      'border-width': 2,
      'border-color': '#2a3a6e',
      'color': '#e8eaf0',
      'label': 'data(label)',
      'font-size': '9px',
      'text-valign': 'bottom',
      'text-halign': 'center',
      'text-margin-y': 4,
      'width': 32,
      'height': 32,
    } as any,
  },
  {
    selector: 'node[?is_focus]',
    style: {
      'background-color': '#d4af37',
      'border-color': '#f0d060',
      'border-width': 3,
      'width': 44,
      'height': 44,
      'color': '#0d1526',
    } as any,
  },
  {
    selector: 'node[risk_level = "HIGH"]',
    style: {
      'background-color': '#7f1d1d',
      'border-color': '#ef4444',
      'border-width': 2,
    } as any,
  },
  {
    selector: 'node[risk_level = "MEDIUM"]',
    style: {
      'background-color': '#78350f',
      'border-color': '#f59e0b',
      'border-width': 2,
    } as any,
  },
  {
    selector: 'node:selected',
    style: {
      'border-color': '#d4af37',
      'border-width': 3,
    } as any,
  },
  {
    selector: 'edge',
    style: {
      'width': 1.5,
      'line-color': '#2a3a6e',
      'target-arrow-color': '#2a3a6e',
      'target-arrow-shape': 'triangle',
      'curve-style': 'bezier',
      'arrow-scale': 1.0,
    } as any,
  },
  {
    selector: 'edge:selected',
    style: {
      'line-color': '#d4af37',
      'target-arrow-color': '#d4af37',
      'width': 2.5,
    } as any,
  },
]

export default function NetworkAnalysis() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const cyRef = useRef<Cytoscape.Core | null>(null)

  const [searchInput, setSearchInput] = useState(searchParams.get('account') || '')
  const [hops, setHops] = useState(1)
  const [graphData, setGraphData] = useState<any | null>(null)
  const [loading, setLoading] = useState(false)
  const [selectedNode, setSelectedNode] = useState<any | null>(null)
  const [selectedEdge, setSelectedEdge] = useState<any | null>(null)
  const [nodeRisk, setNodeRisk] = useState<any | null>(null)

  useEffect(() => {
    const acct = searchParams.get('account')
    if (acct) { setSearchInput(acct); loadGraph(acct) }
  }, [])

  const loadGraph = useCallback(async (account?: string) => {
    const acct = (account || searchInput).trim().toUpperCase()
    if (!acct) { toast.error('Enter an account ID'); return }
    setLoading(true)
    setSelectedNode(null)
    setSelectedEdge(null)
    setNodeRisk(null)
    try {
      const data = await accountsApi.graph(acct, {
        hops,
        max_nodes: 200,
        max_edges: 800,
      })
      setGraphData(data)
      if (data.truncated) toast('Graph truncated — showing bounded subgraph', { icon: '⚡' })
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Account not found')
    } finally {
      setLoading(false)
    }
  }, [searchInput, hops])

  const elements = React.useMemo(() => {
    if (!graphData) return []
    const nodes = (graphData.nodes || []).map((n: any) => ({
      data: {
        id: n.id,
        label: n.id.slice(-6),
        is_focus: n.is_focus ? true : undefined,
        risk_level: n.risk_level || undefined,
        incoming_amount: n.incoming_amount,
        outgoing_amount: n.outgoing_amount,
        pass_through_ratio: n.pass_through_ratio,
      },
    }))
    const edges = (graphData.edges || []).map((e: any, i: number) => ({
      data: {
        id: e.id || `edge_${i}`,
        source: e.source,
        target: e.target,
        amount: e.amount,
        ts: e.ts,
        payment_mode: e.payment_mode,
        sender_ifsc: e.sender_ifsc,
        receiver_ifsc: e.receiver_ifsc,
      },
    }))
    return [...nodes, ...edges]
  }, [graphData])

  async function handleNodeClick(nodeId: string) {
    const node = graphData?.nodes?.find((n: any) => n.id === nodeId)
    setSelectedNode(node || { id: nodeId })
    setSelectedEdge(null)
    setNodeRisk(null)
    try {
      const risk = await detectionApi.risk(nodeId)
      setNodeRisk(risk)
    } catch {
      // risk not available
    }
  }

  function handleEdgeClick(edgeId: string) {
    const edge = graphData?.edges?.find((e: any) => e.id === edgeId)
    setSelectedEdge(edge || { id: edgeId })
    setSelectedNode(null)
  }

  const fmtAmt = (v: any) => v == null ? '—' : `₹${parseFloat(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, height: 'calc(100vh - 100px)' }}>
      {/* Header + Search */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <h1 style={{ color: 'var(--gold)', fontSize: 18, fontWeight: 700, margin: 0, flex: '0 0 auto' }}>
          Network Analysis
        </h1>
        <div style={{ flex: 1, display: 'flex', gap: 8, minWidth: 300 }}>
          <div style={{ position: 'relative', flex: 1, maxWidth: 380 }}>
            <Search size={13} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
            <input
              className="input"
              style={{ paddingLeft: 30 }}
              placeholder="Enter account ID…"
              value={searchInput}
              onChange={e => setSearchInput(e.target.value.toUpperCase())}
              onKeyDown={e => e.key === 'Enter' && loadGraph()}
            />
          </div>
          <select
            className="input"
            style={{ width: 120 }}
            value={hops}
            onChange={e => setHops(Number(e.target.value))}
          >
            <option value={1}>1 Hop</option>
            <option value={2}>2 Hops</option>
            <option value={3}>3 Hops</option>
          </select>
          <button className="btn btn-primary" onClick={() => loadGraph()} disabled={loading}>
            {loading ? <Spinner size={13} /> : <Search size={13} />}
            Analyse
          </button>
        </div>
        {graphData && (
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            {graphData.node_count} nodes · {graphData.edge_count} edges
            {graphData.truncated && <span style={{ color: 'var(--risk-medium)' }}> (truncated)</span>}
          </span>
        )}
      </div>

      {/* Main area */}
      <div style={{ display: 'flex', gap: 12, flex: 1, overflow: 'hidden' }}>
        {/* Graph canvas */}
        <div className="card" style={{ flex: 1, position: 'relative', padding: 0, overflow: 'hidden' }}>
          {loading && (
            <div style={{
              position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
              background: 'rgba(13,21,38,0.8)', zIndex: 10,
            }}>
              <Spinner size={28} label="Building graph…" />
            </div>
          )}

          {!graphData && !loading && (
            <EmptyState
              title="Enter an account ID to visualise"
              description="Focused subgraph — max 200 nodes / 800 edges. Never loads all 2M transactions."
              icon={<Layers size={40} />}
            />
          )}

          {elements.length > 0 && (
            <CytoscapeComponent
              elements={elements}
              stylesheet={CY_STYLE}
              style={{ width: '100%', height: '100%', background: 'var(--bg-primary)' }}
              layout={{ name: 'cose', animate: false, nodeRepulsion: () => 8000, idealEdgeLength: () => 80 } as any}
              cy={(cy: Cytoscape.Core) => {
                cyRef.current = cy
                cy.on('tap', 'node', (evt: any) => handleNodeClick(evt.target.id()))
                cy.on('tap', 'edge', (evt: any) => handleEdgeClick(evt.target.id()))
                cy.on('tap', (evt: any) => {
                  if (evt.target === cy) { setSelectedNode(null); setSelectedEdge(null) }
                })
              }}
            />
          )}

          {/* Graph controls */}
          {graphData && (
            <div style={{
              position: 'absolute', top: 8, right: 8,
              display: 'flex', flexDirection: 'column', gap: 4,
            }}>
              {[
                { icon: <ZoomIn size={14} />, action: () => cyRef.current?.zoom(cyRef.current.zoom() * 1.2), label: 'Zoom in' },
                { icon: <ZoomOut size={14} />, action: () => cyRef.current?.zoom(cyRef.current.zoom() * 0.8), label: 'Zoom out' },
                { icon: <Maximize2 size={14} />, action: () => cyRef.current?.fit(), label: 'Fit' },
                { icon: <RefreshCw size={14} />, action: () => loadGraph(), label: 'Refresh' },
              ].map(({ icon, action, label }) => (
                <button key={label} className="btn btn-ghost" style={{ padding: 7, width: 32, height: 32 }}
                  title={label} onClick={action}>{icon}</button>
              ))}
            </div>
          )}
        </div>

        {/* Inspector panel */}
        <div className="card" style={{ width: 300, flexShrink: 0, overflow: 'auto' }}>
          <div className="card-header">
            <span className="card-title">{selectedEdge ? 'Transaction' : 'Account Inspector'}</span>
          </div>

          {!selectedNode && !selectedEdge && (
            <p style={{ color: 'var(--text-muted)', fontSize: 12 }}>
              Click a node or edge to inspect.
            </p>
          )}

          {selectedNode && (
            <div style={{ fontSize: 12 }}>
              <div className="mono" style={{ color: 'var(--gold)', marginBottom: 10, wordBreak: 'break-all' }}>
                {selectedNode.id}
              </div>
              {[
                ['Incoming', fmtAmt(selectedNode.incoming_amount)],
                ['Outgoing', fmtAmt(selectedNode.outgoing_amount)],
                ['Senders', selectedNode.unique_senders],
                ['Receivers', selectedNode.unique_receivers],
                ['Pass-Through', selectedNode.pass_through_ratio != null ? `${(selectedNode.pass_through_ratio * 100).toFixed(1)}%` : '—'],
              ].map(([k, v]) => (
                <div key={k as string} style={{ display: 'flex', justifyContent: 'space-between', padding: '3px 0', borderBottom: '1px solid var(--border)' }}>
                  <span style={{ color: 'var(--text-muted)' }}>{k}</span>
                  <span>{String(v)}</span>
                </div>
              ))}

              {nodeRisk && (
                <>
                  <div className="divider" />
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 20, fontWeight: 700, color: 'var(--gold)' }}>
                      {nodeRisk.risk_index?.toFixed(0)}
                    </span>
                    <RiskBadge level={nodeRisk.risk_level} />
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>
                    {nodeRisk.explanation}
                  </div>
                </>
              )}

              <div className="divider" />
              <div style={{ display: 'flex', gap: 6 }}>
                <button className="btn btn-primary" style={{ flex: 1, fontSize: 11 }}
                  onClick={() => navigate(`/investigation?victim=${selectedNode.id}`)}>
                  4-Hop Trace
                </button>
                <button className="btn btn-ghost" style={{ flex: 1, fontSize: 11 }}
                  onClick={() => { setSearchInput(selectedNode.id); loadGraph(selectedNode.id) }}>
                  Expand
                </button>
              </div>
            </div>
          )}

          {selectedEdge && (
            <div style={{ fontSize: 12 }}>
              <dl style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', rowGap: 6, columnGap: 8 }}>
                {[
                  ['Transaction ID', selectedEdge.id],
                  ['Amount', fmtAmt(selectedEdge.amount)],
                  ['Timestamp', selectedEdge.ts ? new Date(selectedEdge.ts).toLocaleString('en-IN') : '—'],
                  ['Mode', selectedEdge.payment_mode],
                  ['From', selectedEdge.source],
                  ['To', selectedEdge.target],
                  ['Sender IFSC', selectedEdge.sender_ifsc || '—'],
                  ['Receiver IFSC', selectedEdge.receiver_ifsc || '—'],
                ].map(([k, v]) => (
                  <React.Fragment key={k as string}>
                    <dt style={{ color: 'var(--text-muted)' }}>{k}</dt>
                    <dd className="mono" style={{ margin: 0, wordBreak: 'break-all' }}>{String(v)}</dd>
                  </React.Fragment>
                ))}
              </dl>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
