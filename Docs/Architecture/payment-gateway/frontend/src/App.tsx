// App.tsx — AI-Powered Payment Gateway Dashboard
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
          <span className="sidebar__logo" aria-hidden="true">⬡</span>
          <span className="sidebar__name">PayGateway</span>
        </div>

        <ul className="nav-list">
          <li>
            <button
              type="button"
              className={`nav-item ${view === 'payments' ? 'nav-item--active' : ''}`}
              onClick={() => setView('payments')}
            >
              <span className="nav-item__icon">◫</span>
              Payments
            </button>
          </li>
          <li>
            <button
              type="button"
              className={`nav-item ${view === 'assistant' ? 'nav-item--active' : ''}`}
              onClick={() => setView('assistant')}
            >
              <span className="nav-item__icon">◎</span>
              Policy Assistant
            </button>
          </li>
        </ul>

        <div className="sidebar__footer">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={refresh}
            title="Refresh payments"
          >
            ↺ Refresh
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
