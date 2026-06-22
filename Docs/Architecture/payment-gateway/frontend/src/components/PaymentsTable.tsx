// components/PaymentsTable.tsx
// The signature design element: a left-border "risk spine" that encodes fraud
// risk at a glance. Each row's left border is cyan (allow/no score), amber
// (review / flagged), or red (rejected / failed). Reviewers can scan 100 rows
// in seconds by pattern-matching the spine colours.
import { useState } from 'react';
import type { Transaction, TxnStatus } from '../types';
import { StatusBadge } from './StatusBadge';
import { Spinner } from './Spinner';
import { ErrorBanner } from './ErrorBanner';

function riskClass(t: Transaction): string {
  if (t.status === 'failed' || t.status === 'reversed') return 'row--risk-red';
  if (t.status === 'flagged') return 'row--risk-amber';
  if (t.fraud_score) {
    const s = parseFloat(t.fraud_score);
    if (s >= 0.75) return 'row--risk-red';
    if (s >= 0.40) return 'row--risk-amber';
  }
  return 'row--risk-cyan';
}

function fmtAmount(amount: string, currency: string) {
  const n = parseFloat(amount);
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n);
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString('en-IN', {
    day:    '2-digit',
    month:  'short',
    hour:   '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function truncate(s: string | null, len = 8) {
  if (!s) return '—';
  return s.length > len ? `${s.slice(0, len)}…` : s;
}

interface Props {
  transactions: Transaction[];
  loading:      boolean;
  error:        string | null;
  onSelect:     (t: Transaction) => void;
  selectedId:   string | null;
  onRetry:      () => void;
  hasMore:      boolean;
  onLoadMore:   () => void;
}

const STATUS_FILTERS: Array<TxnStatus | ''> = ['', 'success', 'failed', 'flagged', 'pending', 'reversed'];

export function PaymentsTable({
  transactions,
  loading,
  error,
  onSelect,
  selectedId,
  onRetry,
  hasMore,
  onLoadMore,
}: Props) {
  const [statusFilter, setStatusFilter] = useState<TxnStatus | ''>('');

  const filtered = statusFilter
    ? transactions.filter(t => t.status === statusFilter)
    : transactions;

  return (
    <section className="payments-section">
      <div className="section-header">
        <h2 className="section-title">Recent Payments</h2>
        <div className="section-controls">
          <select
            className="filter-select"
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value as TxnStatus | '')}
            aria-label="Filter by status"
          >
            {STATUS_FILTERS.map(s => (
              <option key={s} value={s}>{s || 'All statuses'}</option>
            ))}
          </select>
        </div>
      </div>

      {error && <ErrorBanner message={error} onRetry={onRetry} />}

      <div className="table-wrap">
        <table className="data-table" aria-label="Payment transactions">
          <thead>
            <tr>
              <th className="col-risk" aria-label="Risk level" />
              <th>Transaction</th>
              <th>Merchant</th>
              <th className="col-amount">Amount</th>
              <th>Method</th>
              <th>Status</th>
              <th>Fraud</th>
              <th>Date</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && !loading && (
              <tr>
                <td colSpan={8} className="table-empty">
                  No transactions found.
                </td>
              </tr>
            )}
            {filtered.map(t => (
              <tr
                key={t.transaction_id}
                className={`data-row ${riskClass(t)} ${selectedId === t.transaction_id ? 'data-row--selected' : ''}`}
                onClick={() => onSelect(t)}
                role="button"
                tabIndex={0}
                onKeyDown={e => e.key === 'Enter' && onSelect(t)}
                aria-selected={selectedId === t.transaction_id}
              >
                <td className="col-risk-cell" />
                <td className="col-id">
                  <code title={t.transaction_id}>{truncate(t.transaction_id, 12)}</code>
                </td>
                <td className="col-merchant">{t.merchant_id ?? '—'}</td>
                <td className="col-amount-val">
                  {fmtAmount(t.amount, t.currency)}
                </td>
                <td>{t.payment_method}</td>
                <td><StatusBadge value={t.status} /></td>
                <td>
                  {t.fraud_score
                    ? <span className="fraud-score">{(parseFloat(t.fraud_score) * 100).toFixed(0)}</span>
                    : <span className="muted">—</span>}
                </td>
                <td className="col-date">{fmtDate(t.created_at)}</td>
              </tr>
            ))}
            {loading && (
              <tr>
                <td colSpan={8} className="table-loading">
                  <Spinner size="sm" /> Loading…
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {hasMore && !loading && (
        <div className="load-more-wrap">
          <button type="button" className="btn btn--ghost" onClick={onLoadMore}>
            Load more
          </button>
        </div>
      )}
    </section>
  );
}
