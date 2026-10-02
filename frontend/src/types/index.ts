// ── Core types matching backend schemas ──────────────────────────────────── //

export interface Transaction {
  transaction_id: string;
  sender_account: string;
  receiver_account: string;
  sender_ifsc?: string;
  receiver_ifsc?: string;
  amount: number;
  ts: string;
  payment_mode: string;
  device_type?: string;
  narration?: string;
  ip_address?: string;
  source_row?: number;
  run_id?: string;
}

export interface Account {
  account_id: string;
  account_node_id?: number;
  first_seen?: string;
  last_seen?: string;
  incoming_count: number;
  outgoing_count: number;
  incoming_amount: number;
  outgoing_amount: number;
  unique_senders: number;
  unique_receivers: number;
  pass_through_ratio?: number;
}

export interface RiskComponent {
  component: string;
  score: number;
  weight: number;
  triggered: boolean;
  reason: string;
  evidence_tx_ids: string[];
}

export interface MuleRiskResult {
  account_id: string;
  risk_index: number;
  risk_level: 'LOW' | 'MEDIUM' | 'HIGH';
  components: RiskComponent[];
  pass_through?: PassThroughFinding;
  fan_in?: FanInFinding;
  fan_out?: FanOutFinding;
  terminal?: TerminalFinding;
  cycles?: CycleFinding;
  ml_probability?: number;
  detection_version: string;
  explanation: string;
  limitations: string[];
}

export interface PassThroughFinding {
  detected: boolean;
  pass_through_ratio?: number;
  window_minutes: number;
  incoming_amount_in_window: string;
  outgoing_amount_in_window: string;
  outgoing_tx_count: number;
  unique_receivers_in_window: number;
  explanation: string;
  limitation: string;
}

export interface FanInFinding {
  detected: boolean;
  unique_senders: number;
  incoming_tx_count: number;
  incoming_amount: string;
  explanation: string;
}

export interface FanOutFinding {
  detected: boolean;
  unique_receivers: number;
  outgoing_tx_count: number;
  outgoing_amount: string;
  splitting_detected: boolean;
  explanation: string;
}

export interface TerminalFinding {
  detected: boolean;
  linux_script_tx_count: number;
  web_emulator_tx_count: number;
  crypto_narration_count: number;
  explanation: string;
  limitation: string;
}

export interface CycleFinding {
  detected: boolean;
  cycle_count: number;
  cycle_paths: string[][];
  explanation: string;
}

export interface HopEdge {
  transaction_id: string;
  sender_account: string;
  receiver_account: string;
  amount: string;
  ts: string;
  payment_mode: string;
  sender_ifsc?: string;
  receiver_ifsc?: string;
  device_type?: string;
  source_ip?: string;
  hop_number: number;
}

export interface HopNode {
  account_id: string;
  account_node_id?: number;
  layer: number;
  risk_index?: number;
  risk_level?: string;
}

export interface HopPath {
  path_id: string;
  nodes: HopNode[];
  edges: HopEdge[];
  depth: number;
  total_amount_traced: string;
}

export interface FourHopTrace {
  victim_account: string;
  paths: HopPath[];
  all_reached_accounts: string[];
  max_depth_reached: number;
  total_paths_found: number;
  paths_truncated: boolean;
  duration_seconds?: number;
  limitation: string;
}

export interface GraphNode {
  id: string;
  node_id?: number;
  is_focus: boolean;
  incoming_amount?: number;
  outgoing_amount?: number;
  incoming_count: number;
  outgoing_count: number;
  unique_senders: number;
  unique_receivers: number;
  first_seen?: string;
  last_seen?: string;
  pass_through_ratio?: number;
  // Added by trace
  layer?: number;
  risk_index?: number;
  risk_level?: string;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  amount: number;
  ts: string;
  payment_mode: string;
  sender_ifsc?: string;
  receiver_ifsc?: string;
  device_type?: string;
  ip_address?: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  focus_account: string;
  node_count: number;
  edge_count: number;
  truncated: boolean;
  hops: number;
}

export interface DatasetSummary {
  total_transactions: number;
  unique_senders: number;
  unique_receivers: number;
  total_amount: string;
  earliest_transaction?: string;
  latest_transaction?: string;
  payment_modes: number;
  device_types: number;
  total_accounts: number;
}

export interface IngestionStatus {
  run_count: number;
  total_rows_loaded: number;
  last_run_at?: string;
  last_status?: string;
  transaction_count: number;
  account_count: number;
  ready: boolean;
}

// ── Module D types ──────────────────────────────────────────────────────── //

export interface EvidenceTransaction {
  transaction_id: string;
  sender_account: string;
  receiver_account: string;
  amount: string;
  ts: string;
  payment_mode: string;
  sender_ifsc?: string;
  receiver_ifsc?: string;
  device_type?: string;
  ip_address?: string;
  narration?: string;
  hop_layer?: number;
}

export interface EvidenceAccount {
  account_id: string;
  layer: number;
  incoming_count: number;
  outgoing_count: number;
  incoming_amount: string;
  outgoing_amount: string;
  unique_senders: number;
  unique_receivers: number;
  pass_through_ratio?: number;
  risk_index?: number;
  risk_level?: string;
  first_seen?: string;
  last_seen?: string;
  detection_indicators: string[];
  supporting_tx_ids: string[];
}

export interface EvidencePacket {
  investigation_id: string;
  generated_at: string;
  victim_account: string;
  observation_start?: string;
  observation_end?: string;
  max_hops_requested: number;
  accounts: EvidenceAccount[];
  transactions: EvidenceTransaction[];
  paths: Array<{
    path_id: string;
    depth: number;
    account_sequence: string[];
    transaction_ids: string[];
    timestamps: string[];
    amounts: string[];
    total_amount_on_path: string;
  }>;
  victim_total_outflow: string;
  layer_totals: Record<string, {
    amount_received: string;
    amount_sent: string;
    tx_count_in: number;
    tx_count_out: number;
    account_count: number;
    accounts: string[];
  }>;
  total_transactions_in_evidence: number;
  paths_truncated: boolean;
  allowed_account_ids: string[];
  allowed_transaction_ids: string[];
  allowed_ifsc_codes: string[];
  limitations: string[];
}

export interface CaseDiaryDocument {
  document_type: string;
  investigation_id: string;
  victim_account: string;
  generated_at: string;
  generation_method?: string;
  facts: Array<{
    fact_id: number;
    statement: string;
    evidence_refs: string[];
  }>;
  chronology: Array<{
    step: number;
    timestamp: string;
    from_account: string;
    to_account: string;
    amount: string;
    transaction_id: string;
    payment_mode?: string;
  }>;
  accounts: Array<{
    account_id: string;
    layer: number;
    role: string;
    risk_indicators: string[];
    risk_index?: number;
  }>;
  holding_candidates: Array<{
    account_id: string;
    reason: string;
    supporting_tx_ids: string[];
  }>;
  narrative: string;
  limitations: string[];
  human_review_required: boolean;
  draft_status: string;
}

export interface FreezeRequisitionDocument {
  document_type: string;
  investigation_id: string;
  victim_account: string;
  generated_at: string;
  generation_method?: string;
  legal_basis_note: string;
  disputed_transactions: Array<{
    transaction_id: string;
    amount: string;
    ts: string;
    from: string;
    to: string;
    payment_mode?: string;
  }>;
  freeze_candidates: Array<{
    account_id: string;
    ifsc: string;
    bank_name_if_known: string;
    reason: string;
    supporting_tx_ids: string[];
    requested_action: string;
  }>;
  total_amount_in_dispute: string;
  authorization_section: {
    prepared_by: string;
    designation: string;
    date: string;
    authorization_note: string;
  };
  limitations: string[];
  draft_status: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
  entities_found: {
    accounts: string[];
    ifscs: string[];
    transaction_ids: string[];
    amounts: string[];
  };
  unsupported_entities: {
    accounts: string[];
    ifscs: string[];
    transaction_ids: string[];
    amounts: string[];
  };
}

// ── API response wrapper ─────────────────────────────────────────────────── //

export interface ApiResponse<T> {
  success: boolean;
  data: T;
  total?: number;
  count?: number;
  message?: string;
}

export type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH';
