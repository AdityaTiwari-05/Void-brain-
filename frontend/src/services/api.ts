/**
 * Centralised API service layer — all HTTP calls go through here.
 * No raw axios/fetch scattered in components.
 *
 * Backend proxy is configured in vite.config.ts:
 *   /v1/* → http://localhost:8000/v1/*
 */

import axios, { AxiosInstance, CancelToken } from 'axios';

const BASE_URL = '/v1';

const http: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
});

// ── Response unwrapper ────────────────────────────────────────────────────── //
function unwrap<T>(response: { data: { success: boolean; data: T; [k: string]: unknown } }): T {
  return response.data.data;
}

// ── Health ────────────────────────────────────────────────────────────────── //
export const healthApi = {
  check: () => axios.get('/health').then(r => r.data),
  detectionHealth: () => http.get('/detection/health').then(r => r.data.data),
};

// ── Dataset / Ingestion ───────────────────────────────────────────────────── //
export const ingestionApi = {
  status: () => http.get('/ingestion/status').then(r => r.data.data),
  listRuns: () => http.get('/ingestion/runs').then(r => r.data.data),
  jobStatus: () => http.get('/ingestion/job-status').then(r => r.data.data),
  ingest: (filePath: string) =>
    http.post('/ingestion/ingest', { file_path: filePath }).then(r => r.data.data),
};

// ── Accounts ──────────────────────────────────────────────────────────────── //
export const accountsApi = {
  summary: () => http.get('/accounts/summary').then(r => r.data.data),

  list: (params?: {
    limit?: number;
    offset?: number;
    search?: string;
    sort_by?: string;
    order?: string;
  }) => http.get('/accounts', { params }).then(r => r.data),

  get: (id: string) => http.get(`/accounts/${id}`).then(r => r.data.data),

  transactions: (
    id: string,
    params?: { limit?: number; cursor?: string; direction?: string; start_time?: string; end_time?: string }
  ) => http.get(`/accounts/${id}/transactions`, { params }).then(r => r.data.data),

  counterparties: (id: string) =>
    http.get(`/accounts/${id}/counterparties`).then(r => r.data.data),

  timeline: (
    id: string,
    params?: { limit?: number; cursor?: string; start_time?: string; end_time?: string }
  ) => http.get(`/accounts/${id}/timeline`, { params }).then(r => r.data.data),

  graph: (
    id: string,
    params?: { hops?: number; max_nodes?: number; max_edges?: number; start_time?: string; end_time?: string }
  ) => http.get(`/accounts/${id}/graph`, { params }).then(r => r.data.data),
};

// ── Transactions ──────────────────────────────────────────────────────────── //
export const transactionsApi = {
  stats: () => http.get('/transactions/stats').then(r => r.data.data),

  list: (params?: {
    limit?: number;
    offset?: number;
    sender?: string;
    receiver?: string;
    transaction_id?: string;
    start_time?: string;
    end_time?: string;
    payment_mode?: string;
    device_type?: string;
    min_amount?: number;
    max_amount?: number;
  }, cancelToken?: CancelToken) =>
    http.get('/transactions', { params, cancelToken }).then(r => r.data),

  get: (id: string) => http.get(`/transactions/${id}`).then(r => r.data.data),
};

// ── Detection (Module B) ──────────────────────────────────────────────────── //
export const detectionApi = {
  risk: (accountId: string) =>
    http.get(`/detection/accounts/${accountId}/risk`).then(r => r.data),

  features: (accountId: string) =>
    http.get(`/detection/accounts/${accountId}/features`).then(r => r.data),

  evidence: (accountId: string) =>
    http.get(`/detection/accounts/${accountId}/evidence`).then(r => r.data.data),

  trace: (params: {
    victim_account: string;
    max_hops?: number;
    max_paths?: number;
    time_window_hours?: number;
  }, cancelToken?: CancelToken) =>
    http.post('/detection/trace', params, { cancelToken }).then(r => r.data),

  suspicious: (params?: { min_risk_index?: number; limit?: number }) =>
    http.get('/detection/suspicious', { params }).then(r => r.data),

  modelEvaluation: () =>
    http.get('/detection/model/evaluation').then(r => r.data),
};

// ── Module D — Investigations ─────────────────────────────────────────────── //
export const investigationsApi = {
  aiStatus: () => http.get('/investigations/ai-status').then(r => r.data.data),

  evidencePacket: (params: {
    victim_account: string;
    max_hops?: number;
    max_paths?: number;
    start_time?: string;
    end_time?: string;
    investigation_id?: string;
  }) => http.post('/investigations/evidence-packet', params).then(r => r.data),

  caseDiary: (params: {
    victim_account: string;
    max_hops?: number;
    force_deterministic?: boolean;
    investigation_id?: string;
  }) => http.post('/investigations/case-diary', params).then(r => r.data),

  freezeRequisition: (params: {
    victim_account: string;
    max_hops?: number;
    force_deterministic?: boolean;
    investigation_id?: string;
  }) => http.post('/investigations/freeze-requisition', params).then(r => r.data),

  validateDocument: (params: {
    victim_account: string;
    document_text: string;
    strict_amounts?: boolean;
    investigation_id?: string;
  }) => http.post('/investigations/validate-document', params).then(r => r.data),

  testInjection: (narration: string) =>
    http.post('/investigations/test-injection', { narration }).then(r => r.data.data),
};

export default http;
