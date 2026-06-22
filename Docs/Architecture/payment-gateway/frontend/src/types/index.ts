// types/index.ts — canonical types derived from backend Pydantic schemas.
// These mirror core-api/app/payment/api/schemas.py and ai-service schemas.
// Amounts are strings to avoid JS float precision loss.

// ── core-api ─────────────────────────────────────────────────────────────────

export type PaymentMethod = 'card' | 'bank_transfer' | 'wallet' | 'upi';
export type TxnStatus     = 'pending' | 'success' | 'failed' | 'flagged' | 'reversed';
export type SettleStatus  = 'settled' | 'pending' | 'disputed' | 'reversed';

export interface Transaction {
  transaction_id:     string;
  user_id:            string | null;
  merchant_id:        string | null;
  amount:             string;   // decimal string e.g. "250.0000"
  currency:           string;
  payment_method:     PaymentMethod;
  status:             TxnStatus;
  failure_reason:     string | null;
  fraud_score:        string | null;  // decimal string 0..1
  chargeback_flag:    boolean;
  settlement_status:  SettleStatus;
  idempotency_key:    string | null;
  parent_transaction: string | null;
  fx_quote_id:        string | null;
  metadata:           Record<string, unknown>;
  created_at:         string;  // ISO-8601
  updated_at:         string;
}

export interface PagedTransactionResponse {
  items:       Transaction[];
  next_cursor: string | null;
  count:       number;
}

export interface CreatePaymentBody {
  user_id:          string;
  merchant_id:      string;
  amount:           string;
  currency:         string;
  payment_method:   PaymentMethod;
  idempotency_key?: string;
  metadata?:        Record<string, unknown>;
}

// ── ai-service — fraud ────────────────────────────────────────────────────────

export type FraudDecision  = 'allow' | 'review' | 'reject';
export type RuleCategory   = 'amount' | 'velocity' | 'geo' | 'method' | 'merchant' | 'behaviour' | 'identity';

export interface RuleHit {
  rule_id:   string;
  category:  RuleCategory;
  weight:    number;
  reason:    string;
  evidence:  Record<string, unknown>;
}

export interface FraudScoreRequest {
  transaction_id: string;
  user_id:        string;
  merchant_id:    string;
  amount:         string;
  currency:       string;
  payment_method: PaymentMethod;
  metadata:       Record<string, unknown>;
}

export interface FraudScoreResponse {
  transaction_id: string;
  user_id:        string;
  risk_score:     number;   // 0–100
  decision:       FraudDecision;
  reasons:        string[];
  rule_hits:      RuleHit[];
  explanation:    string;
  model_version:  string;
  llm_used:       boolean;
  scored_at:      string;
}

// ── ai-service — RAG ─────────────────────────────────────────────────────────

export type SearchMode        = 'vector' | 'keyword' | 'hybrid';
export type KnowledgeCategory = 'refund' | 'chargeback' | 'fraud' | 'settlement' | 'payment_failure' | 'general';

export interface KnowledgeChunk {
  chunk_id:        string;
  category:        KnowledgeCategory;
  source_document: string;
  section_title:   string;
  content:         string;
  score:           number;
}

export interface RAGQueryRequest {
  query:           string;
  top_k?:          number;
  category_filter?: KnowledgeCategory;
  min_score?:      number;
}

export interface RAGQueryResponse {
  query:                  string;
  chunks:                 KnowledgeChunk[];
  search_mode:            SearchMode;
  total_chunks_searched:  number;
  model_version:          string;
  embedding_used:         boolean;
  queried_at:             string;
}

// ── UI helpers ────────────────────────────────────────────────────────────────

export type AsyncState<T> =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; data: T }
  | { status: 'error'; message: string };
