// components/RAGPanel.tsx — RAG knowledge base assistant.
// Sends queries to /api/ai/v1/rag/query and renders retrieved policy chunks.
import { useRef, useState } from 'react';
import type { KnowledgeCategory, RAGQueryResponse } from '../types';
import { aiApi } from '../api/client';
import { useAsync } from '../hooks/useAsync';
import { StatusBadge } from './StatusBadge';
import { Spinner } from './Spinner';
import { ErrorBanner } from './ErrorBanner';

const SUGGESTED: string[] = [
  'How long does a UPI refund take?',
  'What triggers a chargeback?',
  'How is fraud score calculated?',
  'What is the settlement cycle for credit cards?',
  'Can I refund a flagged transaction?',
];

const CATEGORIES: Array<{ value: KnowledgeCategory | ''; label: string }> = [
  { value: '',                label: 'All categories'  },
  { value: 'refund',          label: 'Refund'          },
  { value: 'chargeback',      label: 'Chargeback'      },
  { value: 'fraud',           label: 'Fraud'           },
  { value: 'settlement',      label: 'Settlement'      },
  { value: 'payment_failure', label: 'Payment failure' },
];

export function RAGPanel() {
  const [query,    setQuery]    = useState('');
  const [category, setCategory] = useState<KnowledgeCategory | ''>('');
  const [topK,     setTopK]     = useState(5);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const { state, run, reset } = useAsync<RAGQueryResponse>();

  const submit = (q = query) => {
    if (!q.trim() || q.length < 3) return;
    reset();
    setQuery(q);
    run(aiApi.ragQuery({
      query: q.trim(),
      top_k: topK,
      ...(category ? { category_filter: category } : {}),
    }));
  };

  const handleKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <section className="rag-panel">
      <div className="rag-panel__header">
        <h2 className="section-title">Policy Assistant</h2>
        <p className="rag-panel__hint muted">
          Ask about refunds, chargebacks, fraud rules, settlement, or payment failures.
        </p>
      </div>

      {/* Suggested chips */}
      <div className="rag-suggestions">
        {SUGGESTED.map(s => (
          <button
            key={s}
            type="button"
            className="suggestion-chip"
            onClick={() => { setQuery(s); submit(s); }}
          >
            {s}
          </button>
        ))}
      </div>

      {/* Query form */}
      <div className="rag-form">
        <div className="rag-form__controls">
          <select
            className="filter-select"
            value={category}
            onChange={e => setCategory(e.target.value as KnowledgeCategory | '')}
            aria-label="Category filter"
          >
            {CATEGORIES.map(c => (
              <option key={c.value} value={c.value}>{c.label}</option>
            ))}
          </select>
          <label className="topk-label">
            <span className="muted">Top</span>
            <select
              className="filter-select filter-select--sm"
              value={topK}
              onChange={e => setTopK(Number(e.target.value))}
              aria-label="Number of results"
            >
              {[3, 5, 8, 10].map(n => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
            <span className="muted">results</span>
          </label>
        </div>

        <div className="rag-input-row">
          <textarea
            ref={inputRef}
            className="rag-input"
            rows={2}
            placeholder="Ask a policy question… (Enter to search)"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKey}
            aria-label="Policy question"
          />
          <button
            type="button"
            className="btn"
            onClick={() => submit()}
            disabled={state.status === 'loading' || query.trim().length < 3}
          >
            {state.status === 'loading' ? <Spinner size="sm" /> : 'Search'}
          </button>
        </div>
      </div>

      {/* Results */}
      {state.status === 'error' && (
        <ErrorBanner message={state.message} onRetry={() => submit()} />
      )}

      {state.status === 'success' && (
        <RAGResults res={state.data} />
      )}
    </section>
  );
}

function RAGResults({ res }: { res: RAGQueryResponse }) {
  return (
    <div className="rag-results">
      <div className="rag-results__meta">
        <StatusBadge value={res.search_mode} />
        <span className="muted">
          {res.chunks.length} of {res.total_chunks_searched} chunks ·{' '}
          {res.embedding_used ? 'vector search' : 'keyword search'}
        </span>
      </div>

      {res.chunks.length === 0 && (
        <p className="rag-empty">No matching policy sections found. Try a different query.</p>
      )}

      {res.chunks.map(chunk => (
        <article key={chunk.chunk_id} className="chunk-card">
          <div className="chunk-card__header">
            <div className="chunk-card__meta">
              <StatusBadge value={chunk.category} />
              <span className="chunk-card__doc muted">{chunk.source_document}</span>
            </div>
            <div className="chunk-card__score" title="Relevance score">
              <span className="score-bar">
                <span
                  className="score-bar__fill"
                  style={{ width: `${Math.round(chunk.score * 100)}%` }}
                />
              </span>
              <span className="score-value">{(chunk.score * 100).toFixed(0)}%</span>
            </div>
          </div>
          <h3 className="chunk-card__title">{chunk.section_title}</h3>
          <p className="chunk-card__content">{chunk.content}</p>
        </article>
      ))}
    </div>
  );
}
