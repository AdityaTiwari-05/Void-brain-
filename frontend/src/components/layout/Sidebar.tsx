import React from 'react'
import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard, ArrowUpDown, AlertTriangle, Network,
  Search, Bot, FileText, Settings
} from 'lucide-react'

const NAV_ITEMS = [
  { to: '/dashboard',    icon: LayoutDashboard, label: 'Command Overview' },
  { to: '/transactions', icon: ArrowUpDown,      label: 'Transactions' },
  { to: '/suspicious',   icon: AlertTriangle,    label: 'Suspicious Accounts' },
  { to: '/network',      icon: Network,          label: 'Network Analysis' },
  { to: '/investigation',icon: Search,           label: 'Investigation' },
  { to: '/ai-officer',   icon: Bot,              label: 'AI Case Officer' },
  { to: '/reports',      icon: FileText,         label: 'Reports & Exports' },
]

export default function Sidebar() {
  return (
    <aside style={{
      width: 220,
      background: 'var(--bg-secondary)',
      borderRight: '1px solid var(--border)',
      display: 'flex',
      flexDirection: 'column',
      flexShrink: 0,
    }}>
      {/* Logo */}
      <div style={{
        padding: '16px 12px',
        borderBottom: '1px solid var(--border)',
        textAlign: 'center',
      }}>
        <div style={{
          background: 'rgba(212,175,55,0.1)',
          border: '2px solid var(--gold)',
          borderRadius: '50%',
          width: 56, height: 56,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          margin: '0 auto 8px',
          fontSize: 22,
        }}>🛡️</div>
        <div style={{ color: 'var(--gold)', fontWeight: 700, fontSize: 11, letterSpacing: '0.1em' }}>
          INDORE CYBER CELL
        </div>
        <div style={{ color: 'var(--text-muted)', fontSize: 10, marginTop: 2 }}>
          ABHEDYA-CHAKRA
        </div>
      </div>

      {/* Navigation */}
      <nav style={{ flex: 1, padding: '8px 8px', overflowY: 'auto' }}>
        {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
            style={{ marginBottom: 2 }}
          >
            <Icon size={15} className="nav-icon" />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div style={{
        padding: '12px',
        borderTop: '1px solid var(--border)',
        fontSize: 10,
        color: 'var(--text-muted)',
        textAlign: 'center',
        lineHeight: 1.5,
      }}>
        SAFE CITIZENS · SECURE TRANSACTIONS<br />
        STRONGER SOCIETY
      </div>
    </aside>
  )
}
