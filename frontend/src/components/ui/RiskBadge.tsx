import React from 'react'

interface Props {
  level: string
  score?: number
  size?: 'sm' | 'md'
}

export default function RiskBadge({ level, score, size = 'md' }: Props) {
  const normalized = level?.toUpperCase()
  const cls = normalized === 'HIGH' ? 'badge-high' : normalized === 'MEDIUM' ? 'badge-medium' : 'badge-low'
  const fs  = size === 'sm' ? '10px' : '11px'
  return (
    <span className={`badge ${cls}`} style={{ fontSize: fs }}>
      {score !== undefined ? `${score.toFixed(0)}` : normalized}
    </span>
  )
}
