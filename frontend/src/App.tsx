import React, { Suspense, lazy } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { Toaster } from 'react-hot-toast'
import Layout from './components/layout/Layout'

// Lazy-load pages for performance
const Dashboard       = lazy(() => import('./pages/dashboard/Dashboard'))
const Transactions    = lazy(() => import('./pages/transactions/Transactions'))
const SuspiciousAccounts = lazy(() => import('./pages/suspicious/SuspiciousAccounts'))
const NetworkAnalysis = lazy(() => import('./pages/network/NetworkAnalysis'))
const Investigation   = lazy(() => import('./pages/investigation/Investigation'))
const AIofficer       = lazy(() => import('./pages/ai-officer/AIofficer'))
const Reports         = lazy(() => import('./pages/reports/Reports'))

function Loading() {
  return (
    <div className="flex items-center justify-center h-full" style={{ color: 'var(--text-muted)' }}>
      <div className="loading-spinner" />
      <span className="ml-3 text-sm">Loading…</span>
    </div>
  )
}

export default function App() {
  return (
    <>
      <Toaster
        position="top-right"
        toastOptions={{
          style: {
            background: '#162548',
            color: '#e8eaf0',
            border: '1px solid #1e2e52',
            fontSize: '13px',
          },
        }}
      />
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard"    element={<Suspense fallback={<Loading />}><Dashboard /></Suspense>} />
          <Route path="transactions" element={<Suspense fallback={<Loading />}><Transactions /></Suspense>} />
          <Route path="suspicious"   element={<Suspense fallback={<Loading />}><SuspiciousAccounts /></Suspense>} />
          <Route path="network"      element={<Suspense fallback={<Loading />}><NetworkAnalysis /></Suspense>} />
          <Route path="investigation" element={<Suspense fallback={<Loading />}><Investigation /></Suspense>} />
          <Route path="ai-officer"   element={<Suspense fallback={<Loading />}><AIofficer /></Suspense>} />
          <Route path="reports"      element={<Suspense fallback={<Loading />}><Reports /></Suspense>} />
        </Route>
      </Routes>
    </>
  )
}
