// api/client.ts — typed fetch client for core-api and ai-service.
//
// Proxy map (nginx prod / vite dev):
//   /api/core/*  →  core-api:8000/*
//   /api/ai/*    →  ai-service:8100/*
//
// All amounts stay as decimal strings. Errors are normalised to
// ApiError so callers never need to inspect raw Response.

import type {
  CreatePaymentBody,
  FraudScoreRequest,
  FraudScoreResponse,
  PagedTransactionResponse,
  RAGQueryRequest,
  RAGQueryResponse,
  Transaction,
} from '../types';

// ── Error type ────────────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly body?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

// ── Base fetch ────────────────────────────────────────────────────────────────

async function request<T>(
  url: string,
  options: RequestInit = {},
  timeoutMs = 10_000,
): Promise<T> {
  const ctrl = new AbortController();
  const tid  = window.setTimeout(() => ctrl.abort(), timeoutMs);

  let res: Response;
  try {
    res = await fetch(url, { ...options, signal: ctrl.signal });
  } catch (err) {
    if ((err as Error).name === 'AbortError') {
      throw new ApiError(408, `Request timed out after ${timeoutMs}ms`);
    }
    throw new ApiError(0, `Network error: ${(err as Error).message}`);
  } finally {
    window.clearTimeout(tid);
  }

  // Problem+json or JSON error body
  if (!res.ok) {
    let body: unknown;
    try { body = await res.json(); } catch { body = undefined; }
    const detail =
      typeof body === 'object' && body !== null && 'detail' in body
        ? String((body as Record<string, unknown>).detail)
        : res.statusText;
    throw new ApiError(res.status, detail, body);
  }

  return res.json() as Promise<T>;
}

function get<T>(url: string, timeoutMs?: number) {
  return request<T>(url, { method: 'GET' }, timeoutMs);
}

function post<T>(url: string, body: unknown, timeoutMs?: number) {
  return request<T>(
    url,
    {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(body),
    },
    timeoutMs,
  );
}

// ── core-api ──────────────────────────────────────────────────────────────────

const CORE = '/api/core';

export interface ListPaymentsParams {
  merchant_id?: string;
  user_id?:     string;
  status?:      string;
  from?:        string;
  to?:          string;
  cursor?:      string;
  limit?:       number;
}

function buildQuery(params: Partial<Record<string, string | number>>): string {
  const q = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join('&');
  return q ? `?${q}` : '';
}

export const coreApi = {
  listPayments(params: ListPaymentsParams = {}): Promise<PagedTransactionResponse> {
    const q: Partial<Record<string, string | number>> = {};
    if (params.limit      !== undefined) q.limit       = params.limit;
    if (params.status     !== undefined) q.status      = params.status;
    if (params.merchant_id !== undefined) q.merchant_id = params.merchant_id;
    if (params.user_id    !== undefined) q.user_id     = params.user_id;
    if (params.from       !== undefined) q.from        = params.from;
    if (params.to         !== undefined) q.to          = params.to;
    if (params.cursor     !== undefined) q.cursor      = params.cursor;
    return get(`${CORE}/v1/payments${buildQuery(q)}`);
  },

  getPayment(id: string): Promise<Transaction> {
    return get(`${CORE}/v1/payments/${encodeURIComponent(id)}`);
  },

  createPayment(body: CreatePaymentBody): Promise<Transaction> {
    return post(`${CORE}/v1/payments`, body, 15_000);
  },
};

// ── ai-service ────────────────────────────────────────────────────────────────

const AI = '/api/ai';

export const aiApi = {
  scoreFraud(req: FraudScoreRequest): Promise<FraudScoreResponse> {
    return post(`${AI}/v1/fraud/score`, req, 8_000);
  },

  ragQuery(req: RAGQueryRequest): Promise<RAGQueryResponse> {
    return post(`${AI}/v1/rag/query`, req, 12_000);
  },
};
