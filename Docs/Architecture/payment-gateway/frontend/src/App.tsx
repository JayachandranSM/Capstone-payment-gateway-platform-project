// App.tsx — AI Power Payment Gateway Dashboard
import { useState } from 'react';
import { SummaryCards }    from './components/SummaryCards';
import { PaymentsTable }   from './components/PaymentsTable';
import { DetailPanel }     from './components/DetailPanel';
import { RAGPanel }        from './components/RAGPanel';
import { usePayments }     from './hooks/usePayments';
import type { Transaction, TxnStatus } from './types';

type View = 'payments' | 'assistant';

export default function App() {
  const [view,     setView]     = useState<View>('payments');
  const [selected, setSelected] = useState<Transaction | null>(null);
  const [filter,   setFilter]   = useState<TxnStatus | ''>('');

  const {
    transactions,
    loading,
    error,
    nextCursor,
    refresh,
    fetchNext,
  } = usePayments({ limit: 25, status: filter, autoRefreshMs: 30_000 });

  return (
    <div className="app">
      {/* Sidebar */}
      <nav className="sidebar" aria-label="Main navigation">
        <div className="sidebar__brand">
          <span className="sidebar__logo" aria-hidden="true">
            <svg width="32" height="32" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
              <rect width="32" height="32" rx="8" fill="rgba(56,189,248,0.1)"/>
              <circle cx="16" cy="16" r="10" stroke="#38bdf8" strokeWidth="1.5" fill="none"/>
              <path d="M11 16h10M16 11l5 5-5 5" stroke="#38bdf8" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </span>
          <div className="sidebar__brand-text">
            <span className="sidebar__name">AI Powered</span>
            <span className="sidebar__sub">Payment Gateway</span>
            <span className="sidebar__tagline">Intelligent. Secure. Trusted.</span>
          </div>
        </div>

        <ul className="nav-list">
          <li>
            <button
              type="button"
              className={`nav-item ${view === 'payments' ? 'nav-item--active' : ''}`}
              onClick={() => setView('payments')}
            >
              <span className="nav-item__icon" aria-hidden="true">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <rect x="1" y="3" width="14" height="10" rx="2" stroke="currentColor" strokeWidth="1.4"/>
                  <path d="M1 6h14" stroke="currentColor" strokeWidth="1.4"/>
                  <rect x="3" y="8.5" width="4" height="1.5" rx="0.5" fill="currentColor"/>
                </svg>
              </span>
              Transaction Monitor
            </button>
          </li>
          <li>
            <button
              type="button"
              className={`nav-item ${view === 'assistant' ? 'nav-item--active' : ''}`}
              onClick={() => setView('assistant')}
            >
              <span className="nav-item__icon" aria-hidden="true">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M2 2.5A1.5 1.5 0 0 1 3.5 1h9A1.5 1.5 0 0 1 14 2.5v8A1.5 1.5 0 0 1 12.5 12H9l-3 3v-3H3.5A1.5 1.5 0 0 1 2 10.5v-8z" stroke="currentColor" strokeWidth="1.4"/>
                </svg>
              </span>
              Policy Assistant
            </button>
          </li>
        </ul>

        <div className="sidebar__footer">
          <button
            type="button"
            className="btn btn--ghost btn--sm sidebar__refresh"
            onClick={refresh}
            title="Refresh payments"
          >
            <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
              <path d="M11 6.5A4.5 4.5 0 1 1 9.5 3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
              <path d="M9.5 1v2h2" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            Refresh
          </button>
        </div>
      </nav>

      {/* Main content */}
      <div className="main-content">
        <header className="top-bar">
          <div className="top-bar__left">
            <h1 className="top-bar__title">
              {view === 'payments' ? 'Transaction Monitor' : 'Policy Knowledge Base'}
            </h1>
          </div>
          <div className="top-bar__right">
            {view === 'payments' && (
              <select
                className="filter-select"
                value={filter}
                onChange={e => { setFilter(e.target.value as TxnStatus | ''); setSelected(null); }}
                aria-label="Filter by status"
              >
                <option value="">All statuses</option>
                <option value="success">Success</option>
                <option value="failed">Failed</option>
                <option value="flagged">Flagged</option>
                <option value="pending">Pending</option>
                <option value="reversed">Reversed</option>
              </select>
            )}
          </div>
        </header>

        <div className="content-area">
          {view === 'payments' && (
            <>
              <SummaryCards transactions={transactions} loading={loading} />
              <PaymentsTable
                transactions={transactions}
                loading={loading}
                error={error}
                onSelect={setSelected}
                selectedId={selected?.transaction_id ?? null}
                onRetry={refresh}
                hasMore={!!nextCursor}
                onLoadMore={fetchNext}
              />
            </>
          )}

          {view === 'assistant' && <RAGPanel />}
        </div>
      </div>

      {/* Slide-in detail panel */}
      <DetailPanel
        transaction={selected}
        onClose={() => setSelected(null)}
      />
    </div>
  );
}
