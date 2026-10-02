import React from 'react'

interface Props { size?: number; label?: string }

export default function Spinner({ size = 20, label }: Props) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div className="loading-spinner" style={{ width: size, height: size }} />
      {label && <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>{label}</span>}
    </div>
  )
}
