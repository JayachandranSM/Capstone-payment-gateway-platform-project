// hooks/usePayments.ts — manages payment list state with live refresh.
import { useCallback, useEffect, useRef, useState } from 'react';
import { coreApi, ApiError } from '../api/client';
import type { Transaction, TxnStatus } from '../types';

interface UsePaymentsOpts {
  limit?:       number;
  status?:      TxnStatus | '';
  merchant_id?: string;
  autoRefreshMs?: number;
}

export interface UsePaymentsResult {
  transactions: Transaction[];
  loading:      boolean;
  error:        string | null;
  nextCursor:   string | null;
  total:        number;
  fetchNext:    () => void;
  refresh:      () => void;
}

export function usePayments(opts: UsePaymentsOpts = {}): UsePaymentsResult {
  const { limit = 20, status = '', merchant_id = 'm_swiggy', autoRefreshMs = 0 } = opts;

  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [loading,      setLoading]      = useState(true);
  const [error,        setError]        = useState<string | null>(null);
  const [nextCursor,   setNextCursor]   = useState<string | null>(null);
  const [total,        setTotal]        = useState(0);

  // Track cursor list for forward-only pagination
  const cursorRef = useRef<string | undefined>(undefined);

  const load = useCallback(async (cursor?: string, append = false) => {
    setLoading(true);
    setError(null);
    try {
      const res = await coreApi.listPayments({
        limit,
        ...(status        ? { status }      : {}),
        ...(merchant_id   ? { merchant_id } : {}),
        ...(cursor        ? { cursor }      : {}),
      });
      setTransactions(prev => append ? [...prev, ...res.items] : res.items);
      setNextCursor(res.next_cursor);
      setTotal(prev => append ? prev + res.count : res.count);
      cursorRef.current = cursor;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [limit, status, merchant_id]);

  // Initial + refresh
  const refresh = useCallback(() => {
    cursorRef.current = undefined;
    load(undefined, false);
  }, [load]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Auto-refresh
  useEffect(() => {
    if (!autoRefreshMs) return;
    const id = window.setInterval(refresh, autoRefreshMs);
    return () => window.clearInterval(id);
  }, [autoRefreshMs, refresh]);

  const fetchNext = useCallback(() => {
    if (nextCursor) load(nextCursor, true);
  }, [load, nextCursor]);

  return { transactions, loading, error, nextCursor, total, fetchNext, refresh };
}
