import React, { useEffect, useState } from 'react'
import { Bell, User, Wifi, WifiOff } from 'lucide-react'
import { healthApi } from '../../services/api'

export default function TopBar() {
  const [online, setOnline] = useState<boolean | null>(null)
  const [time, setTime] = useState(new Date())

  useEffect(() => {
    healthApi.check()
      .then(() => setOnline(true))
      .catch(() => setOnline(false))
  }, [])

  useEffect(() => {
    const timer = setInterval(() => setTime(new Date()), 1000)
    return () => clearInterval(timer)
  }, [])

  return (
    <header style={{
      height: 48,
      background: 'var(--bg-secondary)',
      borderBottom: '1px solid var(--border)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '0 16px',
      flexShrink: 0,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {online === true && (
          <span style={{ display: 'flex', alignItems: 'center', gap: 4, color: 'var(--risk-low)', fontSize: 11 }}>
            <Wifi size={12} /> ONLINE
          </span>
        )}
        {online === false && (
          <span style={{ display: 'flex', alignItems: 'center', gap: 4, color: 'var(--risk-high)', fontSize: 11 }}>
            <WifiOff size={12} /> BACKEND OFFLINE
          </span>
        )}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>
          {time.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })}
          {' '}
          {time.toLocaleTimeString('en-IN')}
        </span>
        <Bell size={16} style={{ color: 'var(--text-muted)', cursor: 'pointer' }} />
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '4px 10px',
          background: 'var(--bg-card)',
          borderRadius: 6,
          border: '1px solid var(--border)',
          cursor: 'pointer',
        }}>
          <User size={14} style={{ color: 'var(--gold)' }} />
          <span style={{ fontSize: 12, color: 'var(--text-primary)' }}>Inspector</span>
        </div>
      </div>
    </header>
  )
}
