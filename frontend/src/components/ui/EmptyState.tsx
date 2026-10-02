import React from 'react'
import { Search } from 'lucide-react'

interface Props {
  icon?: React.ReactNode
  title: string
  description?: string
  action?: React.ReactNode
}

export default function EmptyState({ icon, title, description, action }: Props) {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      padding: '48px 24px', gap: 12, color: 'var(--text-muted)', textAlign: 'center',
    }}>
      <div style={{ color: 'var(--border)', opacity: 0.6 }}>{icon || <Search size={40} />}</div>
      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>{title}</div>
      {description && <div style={{ fontSize: 12, maxWidth: 360 }}>{description}</div>}
      {action && <div style={{ marginTop: 8 }}>{action}</div>}
    </div>
  )
}
