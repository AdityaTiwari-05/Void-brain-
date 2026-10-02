import React, { useState, useEffect, useRef } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import CytoscapeComponent from 'react-cytoscapejs'
import Cytoscape from 'cytoscape'
import { Shield, Play, Pause, SkipForward, SkipBack, RotateCcw } from 'lucide-react'
import { detectionApi } from '../../services/api'
import { FourHopTrace, HopEdge } from '../../types'
import Spinner from '../../components/ui/Spinner'
import toast from 'react-hot-toast'

const CY_STYLE: Cytoscape.Stylesheet[] = [
  {
    selector: 'node',
    style: {
      'background-color': '#1e2e52',
      'border-width': 2,
      'border-color': '#2a3a6e',
      'color': '#e8eaf0',
      'label': 'data(shortId)',
      'font-size': '10px',
      'text-valign': 'bottom',
      'width': 36,
      'height': 36,
    } as any,
  },
  {
    selector: 'node[layer = 0]',
    style: { 'background-color': '#d4af37', 'border-color': '#f0d060', 'color': '#0d1526', 'width': 48, 'height': 48 } as any,
  },
  {
    selector: 'node[layer = 1]',
    style: { 'background-color': '#ef4444', 'border-color': '#fca5a5' } as any,
  },
  {
    selector: 'node[layer = 2]',
    style: { 'background-color': '#f59e0b', 'border-color': '#fcd34d' } as any,
  },
  {
    selector: 'node[layer = 3]',
    style: { 'background-color': '#6366f1', 'border-color': '#a5b4fc' } as any,
  },
  {
    selector: 'node[layer = 4]',
    style: { 'background-color': '#22c55e', 'border-color': '#86efac' } as any,
  },
  {
    selector: 'node.active',
    style: { 'border-color': '#d4af37', 'border-width': 4 } as any,
  },
  {
    selector: 'edge',
    style: {
      'width': 1.5,
      'line-color': '#2a3a6e',
      'target-arrow-color': '#2a3a6e',
      'target-arrow-shape': 'triangle',
      'curve-style': 'bezier',
      'label': 'data(amountLabel)',
      'font-size': '9px',
      'color': '#8892a8',
    } as any,
  },
  {
    selector: 'edge.active',
    style: { 'line-color': '#d4af37', 'target-arrow-color': '#d4af37', 'width': 3 } as any,
  },
]

function buildElements(trace: FourHopTrace, highlightStep?: number) {
  const nodeMap = new Map<string, any>()
  const edgeList: any[] = []

  // Victim node
  nodeMap.set(trace.victim_account, {
    data: {
      id: trace.victim_account,
      shortId: trace.victim_account.slice(-6),
      layer: 0,
      account_id: trace.victim_account,
    },
  })

  // Nodes and edges from paths
  let edgeIdx = 0
  for (const path of trace.paths) {
    for (const edge of path.edges) {
      if (!nodeMap.has(edge.sender_account)) {
        nodeMap.set(edge.sender_account, {
          data: {
            id: edge.sender_account,
            shortId: edge.sender_account.slice(-6),
            layer: edge.hop_number - 1,
            account_id: edge.sender_account,
          },
        })
      }
      if (!nodeMap.has(edge.receiver_account)) {
        nodeMap.set(edge.receiver_account, {
          data: {
            id: edge.receiver_account,
            shortId: edge.receiver_account.slice(-6),
            layer: edge.hop_number,
            account_id: edge.receiver_account,
          },
        })
      }
      edgeList.push({
        data: {
          id: edge.transaction_id,
          source: edge.sender_account,
          target: edge.receiver_account,
          amountLabel: `₹${parseFloat(edge.amount).toLocaleString('en-IN', { notation: 'compact' })}`,
          amount: edge.amount,
          ts: edge.ts,
          payment_mode: edge.payment_mode,
          hop: edge.hop_number,
        },
        classes: highlightStep != null && edgeIdx === highlightStep ? 'active' : '',
      })
      edgeIdx++
    }
  }

  return [...nodeMap.values(), ...edgeList]
}

function buildTimeline(trace: FourHopTrace): HopEdge[] {
  const all: HopEdge[] = []
  const seen = new Set<string>()
  for (const path of trace.paths) {
    for (const edge of path.edges) {
      if (!seen.has(edge.transaction_id)) {
        seen.add(edge.transaction_id)
        all.push(edge)
      }
    }
  }
  return all.sort((a, b) => a.ts.localeCompare(b.ts))
}

export default function Investigation() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const cyRef = useRef<Cytoscape.Core | null>(null)

  const [victim, setVictim] = useState(searchParams.get('victim') || '')
  const [maxHops, setMaxHops] = useState(4)
  const [trace, setTrace] = useState<FourHopTrace | null>(null)
  const [loading, setLoading] = useState(false)
  const [timeline, setTimeline] = useState<HopEdge[]>([])
  const [timelineStep, setTimelineStep] = useState(-1)
  const [playing, setPlaying] = useState(false)
  const [selectedEdge, setSelectedEdge] = useState<HopEdge | null>(null)
  const [selectedAccount, setSelectedAccount] = useState<string | null>(null)
  const playRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [activePath, setActivePath] = useState<number>(0)

  useEffect(() => {
    const v = searchParams.get('victim')
    if (v) { setVictim(v); runTrace(v) }
    return () => { if (playRef.current) clearInterval(playRef.current) }
  }, [])

  async function runTrace(v?: string) {
    const acct = (v || victim).trim().toUpperCase()
    if (!acct) { toast.error('Enter a victim account ID'); return }
    setLoading(true)
    setTrace(null)
    setTimeline([])
    setTimelineStep(-1)
    setPlaying(false)
    if (playRef.current) clearInterval(playRef.current)
    try {
      const result = await detectionApi.trace({ victim_account: acct, max_hops: maxHops, max_paths: 30 })
      const traceData: FourHopTrace = result
      setTrace(traceData)
      const tl = buildTimeline(traceData)
      setTimeline(tl)
      if (traceData.paths_truncated) toast('Trace truncated — showing bounded paths', { icon: '⚡' })
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Trace failed — check account ID')
    } finally {
      setLoading(false)
    }
  }

  function play() {
    if (!timeline.length) return
    setPlaying(true)
    let step = timelineStep < 0 ? 0 : timelineStep
    setTimelineStep(step)
    playRef.current = setInterval(() => {
      step++
      if (step >= timeline.length) {
        clearInterval(playRef.current!)
        setPlaying(false)
        return
      }
      setTimelineStep(step)
      setSelectedEdge(timeline[step])
    }, 1200)
  }

  function pause() {
    setPlaying(false)
    if (playRef.current) clearInterval(playRef.current)
  }

  function stepFwd() {
    const next = Math.min(timeline.length - 1, timelineStep + 1)
    setTimelineStep(next)
    setSelectedEdge(timeline[next])
  }

  function stepBack() {
    const prev = Math.max(0, timelineStep - 1)
    setTimelineStep(prev)
    setSelectedEdge(timeline[prev])
  }

  function reset() {
    pause()
    setTimelineStep(-1)
    setSelectedEdge(null)
  }

  const fmtAmt = (v: string) => `₹${parseFloat(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
  const elements = trace ? buildElements(trace, timelineStep) : []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, height: 'calc(100vh - 100px)' }}>
      {/* Header */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <h1 style={{ color: 'var(--gold)', fontSize: 18, fontWeight: 700, margin: 0, flex: '0 0 auto' }}>
          <Shield size={18} style={{ display: 'inline', marginRight: 6 }} />
          Investigation Workspace
        </h1>
        <input
          className="input"
          style={{ flex: 1, maxWidth: 300 }}
          placeholder="Victim Account ID"
          value={victim}
          onChange={e => setVictim(e.target.value.toUpperCase())}
          onKeyDown={e => e.key === 'Enter' && runTrace()}
        />
        <select className="input" style={{ width: 120 }} value={maxHops} onChange={e => setMaxHops(Number(e.target.value))}>
          <option value={1}>1 Hop</option>
          <option value={2}>2 Hops</option>
          <option value={3}>3 Hops</option>
          <option value={4}>4 Hops</option>
        </select>
        <button className="btn btn-primary" onClick={() => runTrace()} disabled={loading} style={{ minWidth: 120 }}>
          {loading ? <Spinner size={13} /> : <Shield size={13} />}
          TRACE MONEY
        </button>
        {trace && (
          <button className="btn btn-ghost" onClick={() => navigate(`/ai-officer?victim=${victim}`)}>
            Generate Case Diary →
          </button>
        )}
      </div>

      {/* Three-panel layout */}
      <div style={{ display: 'flex', gap: 12, flex: 1, overflow: 'hidden' }}>
        {/* LEFT: Case context */}
        <div className="card" style={{ width: 240, flexShrink: 0, overflow: 'auto' }}>
          <div className="card-header">
            <span className="card-title">Case Summary</span>
          </div>
          {!trace && !loading && (
            <p style={{ color: 'var(--text-muted)', fontSize: 12 }}>
              Enter a victim account and click TRACE MONEY.
            </p>
          )}
          {loading && <Spinner label="Tracing…" />}
          {trace && (
            <>
              <div style={{ fontSize: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
                <div>
                  <div style={{ color: 'var(--text-muted)', fontSize: 10 }}>VICTIM ACCOUNT</div>
                  <div className="mono" style={{ color: 'var(--gold)', wordBreak: 'break-all' }}>{trace.victim_account}</div>
                </div>
                <div className="divider" />
                {[
                  ['Paths Found', trace.total_paths_found],
                  ['Max Depth', trace.max_depth_reached],
                  ['Reached Accounts', trace.all_reached_accounts.length],
                  ['Trace Time', trace.duration_seconds ? `${(trace.duration_seconds * 1000).toFixed(0)}ms` : '—'],
                  ['Truncated', trace.paths_truncated ? 'YES ⚠' : 'NO'],
                ].map(([k, v]) => (
                  <div key={k as string} style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ color: 'var(--text-muted)' }}>{k}</span>
                    <span style={{ color: String(v).includes('YES') ? 'var(--risk-medium)' : 'var(--text-primary)' }}>{String(v)}</span>
                  </div>
                ))}
              </div>

              <div className="divider" />

              {/* Layer legend */}
              <div style={{ fontSize: 11 }}>
                <div style={{ color: 'var(--text-muted)', marginBottom: 6, fontWeight: 600 }}>LAYER LEGEND</div>
                {[
                  { layer: 0, label: 'Victim', color: '#d4af37' },
                  { layer: 1, label: 'Layer 1 (Collector)', color: '#ef4444' },
                  { layer: 2, label: 'Layer 2 (Distributor)', color: '#f59e0b' },
                  { layer: 3, label: 'Layer 3', color: '#6366f1' },
                  { layer: 4, label: 'Layer 4 (Terminal)', color: '#22c55e' },
                ].map(({ layer, label, color }) => (
                  <div key={layer} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                    <div style={{ width: 12, height: 12, borderRadius: '50%', background: color, flexShrink: 0 }} />
                    <span style={{ color: 'var(--text-muted)' }}>{label}</span>
                  </div>
                ))}
              </div>

              <div className="divider" />
              <div style={{ fontSize: 10, color: 'var(--text-muted)', fontStyle: 'italic' }}>
                {trace.limitation}
              </div>
            </>
          )}
        </div>

        {/* CENTER: Graph */}
        <div className="card" style={{ flex: 1, position: 'relative', padding: 0, overflow: 'hidden' }}>
          {loading && (
            <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(13,21,38,0.85)', zIndex: 10 }}>
              <Spinner size={32} label="Tracing fund flow…" />
            </div>
          )}
          {elements.length > 0 ? (
            <CytoscapeComponent
              elements={elements}
              stylesheet={CY_STYLE}
              style={{ width: '100%', height: '100%', background: 'var(--bg-primary)' }}
              layout={{ name: 'breadthfirst', directed: true, animate: false, spacingFactor: 1.5, padding: 40 } as any}
              cy={(cy: Cytoscape.Core) => {
                cyRef.current = cy
                cy.on('tap', 'node', (evt: any) => {
                  setSelectedAccount(evt.target.data('account_id'))
                  setSelectedEdge(null)
                })
                cy.on('tap', 'edge', (evt: any) => {
                  const edge = timeline.find(e => e.transaction_id === evt.target.id())
                  if (edge) setSelectedEdge(edge)
                  setSelectedAccount(null)
                })
              }}
            />
          ) : !loading ? (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-muted)' }}>
              <Shield size={48} style={{ opacity: 0.2, marginBottom: 12 }} />
              <p style={{ fontSize: 13 }}>Graph will appear after trace</p>
            </div>
          ) : null}

          {/* Zoom controls */}
          {elements.length > 0 && (
            <div style={{ position: 'absolute', top: 8, right: 8, display: 'flex', flexDirection: 'column', gap: 4 }}>
              <button className="btn btn-ghost" style={{ padding: 6, width: 30, height: 30 }} onClick={() => cyRef.current?.zoom(cyRef.current.zoom() * 1.2)}>+</button>
              <button className="btn btn-ghost" style={{ padding: 6, width: 30, height: 30 }} onClick={() => cyRef.current?.zoom(cyRef.current.zoom() * 0.8)}>−</button>
              <button className="btn btn-ghost" style={{ padding: 6, width: 30, height: 30 }} onClick={() => cyRef.current?.fit()} title="Fit">⊞</button>
            </div>
          )}
        </div>

        {/* RIGHT: Evidence inspector */}
        <div className="card" style={{ width: 280, flexShrink: 0, overflow: 'auto' }}>
          <div className="card-header">
            <span className="card-title">{selectedEdge ? 'Transaction' : selectedAccount ? 'Account' : 'Evidence'}</span>
          </div>

          {selectedEdge && (
            <div style={{ fontSize: 12 }}>
              <div className="mono" style={{ color: 'var(--gold)', marginBottom: 8, wordBreak: 'break-all' }}>
                {selectedEdge.transaction_id}
              </div>
              {[
                ['Layer', `Hop ${selectedEdge.hop_number}`],
                ['From', selectedEdge.sender_account],
                ['To', selectedEdge.receiver_account],
                ['Amount', fmtAmt(selectedEdge.amount)],
                ['Timestamp', new Date(selectedEdge.ts).toLocaleString('en-IN')],
                ['Mode', selectedEdge.payment_mode],
                ['Sender IFSC', selectedEdge.sender_ifsc || '—'],
                ['Receiver IFSC', selectedEdge.receiver_ifsc || '—'],
                ['Device', selectedEdge.device_type || '—'],
                ['IP', selectedEdge.source_ip || '—'],
              ].map(([k, v]) => (
                <div key={k as string} style={{ display: 'flex', justifyContent: 'space-between', padding: '3px 0', borderBottom: '1px solid var(--border)', gap: 8 }}>
                  <span style={{ color: 'var(--text-muted)', flexShrink: 0 }}>{k}</span>
                  <span className="mono" style={{ wordBreak: 'break-all', textAlign: 'right' }}>{String(v)}</span>
                </div>
              ))}
              {selectedEdge.narration_raw && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ color: 'var(--text-muted)', fontSize: 10, marginBottom: 4 }}>NARRATION (UNTRUSTED DATA)</div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', fontStyle: 'italic' }}>{selectedEdge.narration_raw}</div>
                </div>
              )}
              <div className="divider" />
              <button className="btn btn-ghost" style={{ width: '100%', fontSize: 11 }}
                onClick={() => navigate(`/investigation?victim=${selectedEdge.sender_account}`)}>
                Trace from Sender
              </button>
            </div>
          )}

          {selectedAccount && !selectedEdge && (
            <div style={{ fontSize: 12 }}>
              <div className="mono" style={{ color: 'var(--gold)', marginBottom: 8, wordBreak: 'break-all' }}>
                {selectedAccount}
              </div>
              <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
                <button className="btn btn-primary" style={{ flex: 1, fontSize: 11 }}
                  onClick={() => navigate(`/investigation?victim=${selectedAccount}`)}>
                  Trace
                </button>
                <button className="btn btn-ghost" style={{ flex: 1, fontSize: 11 }}
                  onClick={() => navigate(`/network?account=${selectedAccount}`)}>
                  Graph
                </button>
              </div>
              <button className="btn btn-ghost" style={{ width: '100%', fontSize: 11 }}
                onClick={() => navigate(`/ai-officer?victim=${selectedAccount}`)}>
                AI Case Officer →
              </button>
            </div>
          )}

          {!selectedEdge && !selectedAccount && (
            <p style={{ color: 'var(--text-muted)', fontSize: 12 }}>
              Click a node or edge in the graph to inspect evidence.
            </p>
          )}

          {/* Paths list */}
          {trace && (
            <>
              <div className="divider" />
              <div style={{ fontSize: 11 }}>
                <div style={{ color: 'var(--text-muted)', fontWeight: 600, marginBottom: 8 }}>
                  PATHS ({trace.paths.length})
                </div>
                {trace.paths.slice(0, 8).map((path, i) => (
                  <div
                    key={path.path_id}
                    onClick={() => setActivePath(i)}
                    style={{
                      background: activePath === i ? 'rgba(212,175,55,0.1)' : 'transparent',
                      border: `1px solid ${activePath === i ? 'var(--gold)' : 'var(--border)'}`,
                      borderRadius: 4,
                      padding: '4px 8px',
                      marginBottom: 4,
                      cursor: 'pointer',
                    }}
                  >
                    <span style={{ color: 'var(--text-muted)' }}>Path {i + 1}</span>
                    {' · '}
                    <span style={{ color: 'var(--gold)' }}>Depth {path.depth}</span>
                    {' · '}
                    <span style={{ color: 'var(--risk-low)' }}>
                      {fmtAmt(path.total_amount_traced)}
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Bottom: Timeline */}
      {trace && timeline.length > 0 && (
        <div className="card" style={{ flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
            <span style={{ color: 'var(--gold)', fontSize: 11, fontWeight: 600 }}>TIMELINE</span>
            <div style={{ display: 'flex', gap: 6 }}>
              <button className="btn btn-ghost" style={{ padding: '4px 8px' }} onClick={reset}><RotateCcw size={12} /></button>
              <button className="btn btn-ghost" style={{ padding: '4px 8px' }} onClick={stepBack} disabled={timelineStep <= 0}><SkipBack size={12} /></button>
              {playing
                ? <button className="btn btn-primary" style={{ padding: '4px 12px' }} onClick={pause}><Pause size={12} /> Pause</button>
                : <button className="btn btn-primary" style={{ padding: '4px 12px' }} onClick={play}><Play size={12} /> Play</button>
              }
              <button className="btn btn-ghost" style={{ padding: '4px 8px' }} onClick={stepFwd} disabled={timelineStep >= timeline.length - 1}><SkipForward size={12} /></button>
            </div>
            <input
              type="range" min={-1} max={timeline.length - 1} value={timelineStep}
              onChange={e => { const s = Number(e.target.value); setTimelineStep(s); setSelectedEdge(s >= 0 ? timeline[s] : null) }}
              style={{ flex: 1, accentColor: 'var(--gold)' }}
            />
            <span style={{ fontSize: 11, color: 'var(--text-muted)', width: 80, textAlign: 'right' }}>
              {timelineStep >= 0 ? `${timelineStep + 1} / ${timeline.length}` : `0 / ${timeline.length}`}
            </span>
          </div>

          {/* Timeline events */}
          <div style={{ display: 'flex', gap: 4, overflow: 'auto' }}>
            {timeline.map((edge, i) => (
              <div
                key={edge.transaction_id}
                onClick={() => { setTimelineStep(i); setSelectedEdge(edge) }}
                style={{
                  flexShrink: 0,
                  background: i === timelineStep ? 'rgba(212,175,55,0.15)' : 'var(--bg-card)',
                  border: `1px solid ${i === timelineStep ? 'var(--gold)' : 'var(--border)'}`,
                  borderRadius: 4,
                  padding: '4px 8px',
                  cursor: 'pointer',
                  minWidth: 120,
                  fontSize: 10,
                }}
              >
                <div style={{ color: 'var(--text-muted)' }}>Hop {edge.hop_number}</div>
                <div style={{ color: 'var(--gold)', fontWeight: 600 }}>
                  {parseFloat(edge.amount).toLocaleString('en-IN', { notation: 'compact', maximumFractionDigits: 1 })}
                </div>
                <div style={{ color: 'var(--text-muted)' }}>
                  {new Date(edge.ts).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
                </div>
                <div className="mono" style={{ fontSize: 9, color: 'var(--text-muted)' }}>
                  {edge.sender_account.slice(-4)} → {edge.receiver_account.slice(-4)}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
