// src/api/health.ts — typed health-endpoint client.
// In dev: Vite proxies /api/core and /api/ai to the local backends.
// In prod: nginx (frontend container) does the same.

export type HealthResponse = {
  status: 'ok' | 'ready' | 'degraded';
  service: string;
  environment?: string;
};

export type ReadinessChecks = Record<string, string>;

export type ReadinessResponse = {
  status: 'ready' | 'degraded';
  service: string;
  checks: ReadinessChecks;
  llm_available?: boolean;
};

async function getJson<T>(url: string, timeoutMs = 4000): Promise<T> {
  const ctrl = new AbortController();
  const tid = window.setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    if (!res.ok && res.status !== 503) {
      // 503 is meaningful (degraded) — read the body before throwing.
      throw new Error(`HTTP ${res.status}`);
    }
    return (await res.json()) as T;
  } finally {
    window.clearTimeout(tid);
  }
}

export const api = {
  coreHealth:  () => getJson<HealthResponse>('/api/core/healthz'),
  coreReady:   () => getJson<ReadinessResponse>('/api/core/readyz'),
  aiHealth:    () => getJson<HealthResponse>('/api/ai/healthz'),
  aiReady:     () => getJson<ReadinessResponse>('/api/ai/readyz'),
};
