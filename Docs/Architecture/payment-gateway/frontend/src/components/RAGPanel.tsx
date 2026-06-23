// components/RAGPanel.tsx — Policy Knowledge Base assistant.
// Renders retrieved policy chunks as clean cards, stripping markdown symbols.
import { useRef, useState } from 'react';
import type { KnowledgeCategory, KnowledgeChunk, RAGQueryResponse } from '../types';
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

/**
 * Strip common Markdown artifacts so policy text reads cleanly.
 * Handles: headings (# ## ###), bold (**text**), italics (*text*),
 * table rows (|col|col|), horizontal rules (---), inline code (`text`),
 * leading list bullets (- item), and excess blank lines.
 */
function stripMarkdown(text: string): string {
  return text
    // Remove heading markers
    .replace(/^#{1,6}\s+/gm, '')
    // Bold and italic — unwrap content
    .replace(/\*{2,3}([^*]+)\*{2,3}/g, '$1')
    .replace(/_([^_]+)_/g, '$1')
    // Table separator rows (|---|---|)
    .replace(/^\|[-:\s|]+\|$/gm, '')
    // Table row pipes — convert to readable spacing
    .replace(/^\|(.+)\|$/gm, (_m, inner: string) =>
      inner.split('|').map((c: string) => c.trim()).filter(Boolean).join('  ·  ')
    )
    // Horizontal rules
    .replace(/^(-{3,}|\*{3,}|_{3,})$/gm, '')
    // Inline code — unwrap
    .replace(/`([^`]+)`/g, '$1')
    // List bullets
    .replace(/^[\-\*\+]\s+/gm, '• ')
    // Collapse multiple blank lines
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/** Convert a snake_case source filename into a readable label. */
function formatDocName(name: string): string {
  return name
    .replace(/_/g, ' ')
    .replace(/\.md$/, '')
    .replace(/\b\w/g, c => c.toUpperCase());
}

/** Score percentage colour based on value. */
function scoreColor(score: number): string {
  if (score >= 0.8) return 'var(--green)';
  if (score >= 0.6) return 'var(--accent)';
  return 'var(--muted-2)';
}

/** Score label text. */
function scoreLabel(score: number): string {
  if (score >= 0.85) return 'High match';
  if (score >= 0.70) return 'Good match';
  if (score >= 0.55) return 'Partial match';
  return 'Low match';
}

// ── Chunk card ────────────────────────────────────────────────────────────────

function ChunkCard({ chunk }: { chunk: KnowledgeChunk }) {
  const cleanContent = stripMarkdown(chunk.content);
  const docLabel     = formatDocName(chunk.source_document);
  const pct          = Math.round(chunk.score * 100);
  const color        = scoreColor(chunk.score);
  const label        = scoreLabel(chunk.score);

  return (
    <article className="chunk-card chunk-card--premium">
      {/* Header row: category + doc + score */}
      <div className="chunk-card__header">
        <div className="chunk-card__badges">
          <StatusBadge value={chunk.category} />
          <span className="chunk-card__doc-label">{docLabel}</span>
        </div>
        <div className="chunk-card__score-pill" style={{ color }}>
          <span className="chunk-card__score-pct">{pct}%</span>
          <span className="chunk-card__score-label">{label}</span>
        </div>
      </div>

      {/* Section title */}
      <h3 className="chunk-card__title">{chunk.section_title}</h3>

      {/* Relevance bar */}
      <div className="chunk-card__bar-wrap">
        <div className="chunk-card__bar">
          <div
            className="chunk-card__bar-fill"
            style={{ width: `${pct}%`, background: color }}
          />
        </div>
      </div>

      {/* Clean policy text */}
      <div className="chunk-card__content-clean">
        {cleanContent.split('\n\n').map((para, i) => (
          para.trim()
            ? <p key={i} className="chunk-card__para">{para.trim()}</p>
            : null
        ))}
      </div>
    </article>
  );
}

// ── AI Summary card ───────────────────────────────────────────────────────────

/**
 * Derives a plain-English summary from the top retrieved chunks.
 * Pure client-side — no extra API call. Synthesises the query topic,
 * the dominant category, and key sentences from the highest-scoring chunks.
 */
function buildSummary(query: string, chunks: KnowledgeChunk[]): string {
  if (chunks.length === 0) return '';

  const top = chunks.slice(0, 3);

  // Determine dominant category from top chunks
  const catCounts: Record<string, number> = {};
  for (const c of top) catCounts[c.category] = (catCounts[c.category] ?? 0) + 1;
  const domCategory = Object.entries(catCounts).sort((a, b) => b[1] - a[1])[0][0];

  // Pull first meaningful sentence from each top chunk (cleaned)
  const sentences = top
    .map(c => {
      const clean = stripMarkdown(c.content);
      const first = clean.split(/[.!?]\s+/)[0]?.trim();
      return first && first.length > 20 ? first : null;
    })
    .filter((s): s is string => s !== null)
    .slice(0, 2);

  const topScore = Math.round(top[0].score * 100);
  const sourceCount = new Set(top.map(c => c.source_document)).size;

  return [
    `Based on your query about "${query}", the most relevant policy guidance comes from the ${domCategory.replace('_', ' ')} domain (top match: ${topScore}% relevance, across ${sourceCount} policy document${sourceCount > 1 ? 's' : ''}).`,
    ...sentences.map(s => s.endsWith('.') ? s : s + '.'),
  ].join(' ');
}

function AISummaryCard({ query, chunks }: { query: string; chunks: KnowledgeChunk[] }) {
  const summary = buildSummary(query, chunks);
  if (!summary) return null;

  return (
    <div className="rag-ai-summary">
      <div className="rag-ai-summary__header">
        <span className="rag-ai-summary__icon" aria-hidden="true">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.3"/>
            <path d="M5.5 8.5l2 2 3-4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </span>
        <span className="rag-ai-summary__label">AI Summary</span>
        <span className="rag-ai-summary__badge">Synthesised from top results</span>
      </div>
      <p className="rag-ai-summary__text">{summary}</p>
    </div>
  );
}

// ── Results container ─────────────────────────────────────────────────────────

function RAGResults({ query, res }: { query: string; res: RAGQueryResponse }) {
  const modeLabel = res.embedding_used ? 'Semantic search' : 'Keyword search';

  return (
    <div className="rag-results">
      {/* Meta bar */}
      <div className="rag-results__meta">
        <StatusBadge value={res.search_mode} />
        <span className="rag-meta-text">
          {modeLabel} · {res.chunks.length} result{res.chunks.length !== 1 ? 's' : ''} of {res.total_chunks_searched} sections
        </span>
      </div>

      {res.chunks.length > 0 && (
        <AISummaryCard query={query} chunks={res.chunks} />
      )}

      {res.chunks.length === 0 && (
        <div className="rag-empty-state">
          <p className="rag-empty-state__title">No matching policy sections found</p>
          <p className="rag-empty-state__hint">Try rephrasing your question or selecting a specific category.</p>
        </div>
      )}

      <div className="rag-chunks-list">
        {res.chunks.map(chunk => (
          <ChunkCard key={chunk.chunk_id} chunk={chunk} />
        ))}
      </div>
    </div>
  );
}

// ── Main panel ────────────────────────────────────────────────────────────────

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
      {/* Header */}
      <div className="rag-panel__header">
        <div className="rag-panel__title-row">
          <h2 className="section-title">Policy Knowledge Base</h2>
          <span className="rag-panel__powered-by">AI-Powered Operational Knowledge Assistant</span>
        </div>
        <p className="rag-panel__hint muted">
          Ask about refunds, chargebacks, fraud rules, settlement cycles, or payment failures.
        </p>
      </div>

      {/* Suggested queries */}
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

      {/* Controls + input */}
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

      {state.status === 'error' && (
        <ErrorBanner message={state.message} onRetry={() => submit()} />
      )}

      {state.status === 'success' && (
        <RAGResults query={query} res={state.data} />
      )}
    </section>
  );
}
