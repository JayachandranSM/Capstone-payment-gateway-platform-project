// components/DetailPanel.tsx — premium slide-in drawer: transaction fields + fraud scoring.
import { useEffect, useRef, useState } from 'react';
import type { FraudScoreResponse, RuleCategory, RuleHit, Transaction } from '../types';
import { aiApi } from '../api/client';
import { useAsync } from '../hooks/useAsync';
import { ErrorBanner } from './ErrorBanner';
import { Spinner } from './Spinner';
import { StatusBadge } from './StatusBadge';

interface Props {
  transaction: Transaction | null;
  onClose: () => void;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="detail-row">
      <dt className="detail-row__label">{label}</dt>
      <dd className="detail-row__value">{children}</dd>
    </div>
  );
}

function fmtMoney(amount: string, currency: string) {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
  }).format(parseFloat(amount));
}

function fmtFull(iso: string) {
  return new Date(iso).toLocaleString('en-IN', {
    dateStyle: 'medium',
    timeStyle: 'medium',
  });
}

function fmtTime(iso: string) {
  return new Date(iso).toLocaleTimeString('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

// ── Category colour map ───────────────────────────────────────────────────────

const CATEGORY_CLASS: Record<RuleCategory, string> = {
  amount:    'cat--amount',
  velocity:  'cat--velocity',
  geo:       'cat--geo',
  method:    'cat--method',
  merchant:  'cat--merchant',
  behaviour: 'cat--behaviour',
  identity:  'cat--identity',
};

// ── Animated score meter ──────────────────────────────────────────────────────

function ScoreMeter({ score, color }: { score: number; color: string }) {
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      requestAnimationFrame(() => setWidth(score));
    });
    return () => cancelAnimationFrame(raf);
  }, [score]);

  return (
    <div className="fraud-meter">
      <div className="fraud-meter__track" role="meter" aria-valuenow={score} aria-valuemin={0} aria-valuemax={100}>
        <div
          className="fraud-meter__fill"
          style={{ width: `${width}%`, background: color }}
        />
      </div>
      <span className="fraud-meter__label" style={{ color }}>
        {score}<span className="fraud-meter__denom">/100</span>
      </span>
    </div>
  );
}

// ── Rule hit card ─────────────────────────────────────────────────────────────

function RuleHitCard({ hit }: { hit: RuleHit }) {
  const [open, setOpen] = useState(false);
  const hasEvidence = Object.keys(hit.evidence).length > 0;

  return (
    <div className={`rule-hit ${CATEGORY_CLASS[hit.category] ?? ''}`}>
      <div className="rule-hit__top">
        <div className="rule-hit__left">
          <span className={`rule-hit__cat ${CATEGORY_CLASS[hit.category] ?? ''}`}>
            {hit.category}
          </span>
          <code className="rule-hit__id">{hit.rule_id}</code>
        </div>
        <div className="rule-hit__right">
          <span className="rule-hit__weight">+{hit.weight}</span>
          {hasEvidence && (
            <button
              type="button"
              className="rule-hit__toggle"
              onClick={() => setOpen(o => !o)}
              aria-expanded={open}
              aria-label={open ? 'Hide evidence' : 'Show evidence'}
            >
              {open ? '▲' : '▼'}
            </button>
          )}
        </div>
      </div>

      <p className="rule-hit__reason">{hit.reason}</p>

      {open && hasEvidence && (
        <dl className="rule-hit__evidence">
          {Object.entries(hit.evidence).map(([k, v]) => (
            <div key={k} className="rule-hit__ev-row">
              <dt className="rule-hit__ev-key">{k}</dt>
              <dd className="rule-hit__ev-val">{String(v)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

// ── Fraud result ──────────────────────────────────────────────────────────────

function FraudResult({ result }: { result: FraudScoreResponse }) {
  const barColor =
    result.decision === 'allow'  ? 'var(--green)' :
    result.decision === 'review' ? 'var(--amber)' :
    'var(--red)';

  const bandLabel =
    result.risk_score < 40 ? 'LOW RISK' :
    result.risk_score < 75 ? 'MEDIUM RISK' :
    'HIGH RISK';

  return (
    <div className="fraud-result">
      {/* Score + decision */}
      <div className="fraud-result__top">
        <div className="fraud-result__score-block">
          <div className="fraud-result__score-row">
            <span className="fraud-result__band" style={{ color: barColor }}>
              {bandLabel}
            </span>
            <StatusBadge value={result.decision} />
          </div>
          <ScoreMeter score={result.risk_score} color={barColor} />
        </div>
      </div>

      {/* LLM explanation */}
      {result.explanation && (
        <div className="fraud-explanation-block">
          <p className="fraud-explanation-block__label">AI Analysis</p>
          <p className="fraud-explanation">{result.explanation}</p>
          {result.llm_used && <span className="fraud-result__llm-tag">LLM ✓</span>}
        </div>
      )}

      {/* Flat reasons — when no rule_hits detail */}
      {result.reasons.length > 0 && result.rule_hits.length === 0 && (
        <ul className="fraud-reasons">
          {result.reasons.map((r, i) => (
            <li key={i} className="fraud-reason">{r}</li>
          ))}
        </ul>
      )}

      {/* Rule hits */}
      {result.rule_hits.length > 0 && (
        <div className="rule-hits">
          <p className="rule-hits__title">
            {result.rule_hits.length} rule{result.rule_hits.length !== 1 ? 's' : ''} fired
          </p>
          {result.rule_hits.map(hit => (
            <RuleHitCard key={hit.rule_id} hit={hit} />
          ))}
        </div>
      )}

      {/* Footer */}
      <div className="fraud-result__footer">
        <span className="fraud-result__meta muted">{result.model_version}</span>
        <span className="fraud-result__meta muted">Scored {fmtTime(result.scored_at)}</span>
      </div>
    </div>
  );
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function FraudSkeleton() {
  return (
    <div className="fraud-skeleton" aria-hidden="true">
      <div className="fraud-skeleton__row">
        <div className="skel skel--wide" />
        <div className="skel skel--badge" />
      </div>
      <div className="skel skel--bar" />
      <div className="skel skel--line" />
      <div className="skel skel--line skel--line-short" />
      <div className="fraud-skeleton__hits">
        <div className="skel skel--hit" />
        <div className="skel skel--hit" />
      </div>
    </div>
  );
}

// ── Section divider ───────────────────────────────────────────────────────────

function SectionDivider({ label }: { label: string }) {
  return (
    <div className="detail-section-divider">
      <span className="detail-section-divider__label">{label}</span>
    </div>
  );
}

// ── Main export ───────────────────────────────────────────────────────────────

export function DetailPanel({ transaction: txn, onClose }: Props) {
  const { state: fraudState, run: runFraud, reset: resetFraud } = useAsync<FraudScoreResponse>();
  const scoreButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    resetFraud();
  }, [txn?.transaction_id, resetFraud]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  const scoreFraud = () => {
    if (!txn) return;
    runFraud(
      aiApi.scoreFraud({
        transaction_id: txn.transaction_id,
        user_id:        txn.user_id ?? '00000000-0000-0000-0000-000000000000',
        merchant_id:    txn.merchant_id ?? 'unknown',
        amount:         txn.amount,
        currency:       txn.currency,
        payment_method: txn.payment_method,
        metadata:       txn.metadata ?? {},
      }),
    );
  };

  const isLoading = fraudState.status === 'loading';
  const hasResult = fraudState.status === 'success';
  const buttonLabel = hasResult ? 'Re-score' : 'Score now';

  return (
    <>
      {/* Backdrop */}
      <div
        className={`panel-backdrop ${txn ? 'panel-backdrop--visible' : ''}`}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer */}
      <aside
        className={`detail-panel ${txn ? 'detail-panel--open' : ''}`}
        aria-label="Transaction detail"
        role="complementary"
      >
        {/* Premium header with amount hero */}
        <div className="detail-panel__header detail-panel__header--premium">
          <div className="detail-panel__header-main">
            {txn ? (
              <>
                <div className="detail-panel__hero-amount">
                  {fmtMoney(txn.amount, txn.currency)}
                </div>
                <div className="detail-panel__hero-meta">
                  <StatusBadge value={txn.status} />
                  <span className="detail-panel__hero-method">{txn.payment_method}</span>
                  {txn.merchant_id && (
                    <span className="detail-panel__hero-merchant">{txn.merchant_id}</span>
                  )}
                </div>
              </>
            ) : (
              <h2 className="detail-panel__title">Transaction Detail</h2>
            )}
          </div>
          <button
            type="button"
            className="panel-close"
            onClick={onClose}
            aria-label="Close detail panel"
          >
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
              <path d="M4 4l10 10M14 4L4 14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/>
            </svg>
          </button>
        </div>

        {txn && (
          <div className="detail-panel__body">

            {/* ── Transaction fields ── */}
            <SectionDivider label="Transaction" />
            <dl className="detail-list">
              <Row label="ID">
                <code className="mono">{txn.transaction_id}</code>
              </Row>
              <Row label="Status">
                <StatusBadge value={txn.status} />
                {txn.failure_reason && (
                  <span className="detail-failure-reason">{txn.failure_reason}</span>
                )}
              </Row>
              <Row label="Settlement">
                <StatusBadge value={txn.settlement_status} />
                {txn.chargeback_flag && (
                  <span className="badge badge--red" style={{ marginLeft: '0.35rem' }}>Chargeback</span>
                )}
              </Row>
              {txn.parent_transaction && (
                <Row label="Refund of">
                  <code className="mono sm">{txn.parent_transaction}</code>
                </Row>
              )}
              {txn.fraud_score && (
                <Row label="Stored fraud score">
                  <span className="fraud-score">
                    {(parseFloat(txn.fraud_score) * 100).toFixed(1)}
                  </span>
                  <span className="muted">/100</span>
                </Row>
              )}
            </dl>

            {/* ── Parties ── */}
            <SectionDivider label="Parties" />
            <dl className="detail-list">
              <Row label="User">
                <code className="mono sm">{txn.user_id ?? '—'}</code>
              </Row>
              <Row label="Merchant">{txn.merchant_id ?? '—'}</Row>
            </dl>

            {/* ── Timestamps ── */}
            <SectionDivider label="Timeline" />
            <dl className="detail-list">
              <Row label="Created">{fmtFull(txn.created_at)}</Row>
              <Row label="Updated">{fmtFull(txn.updated_at)}</Row>
              {txn.idempotency_key && (
                <Row label="Idempotency key">
                  <code className="mono sm">{txn.idempotency_key}</code>
                </Row>
              )}
            </dl>

            {/* ── Metadata ── */}
            {Object.keys(txn.metadata ?? {}).length > 0 && (
              <details className="metadata-block">
                <summary>Metadata</summary>
                <pre className="metadata-json">
                  {JSON.stringify(txn.metadata, null, 2)}
                </pre>
              </details>
            )}

            {/* ── Fraud scoring panel ── */}
            <div className="fraud-panel">
              <div className="fraud-panel__header">
                <h3 className="fraud-panel__title">
                  Fraud Score
                  <span className="fraud-panel__ai-tag">AI</span>
                </h3>
                <button
                  ref={scoreButtonRef}
                  type="button"
                  className="btn btn--sm"
                  onClick={scoreFraud}
                  disabled={isLoading}
                  aria-label={isLoading ? 'Scoring in progress' : buttonLabel}
                >
                  {isLoading
                    ? <><Spinner size="sm" /><span>Scoring…</span></>
                    : buttonLabel}
                </button>
              </div>

              {fraudState.status === 'idle' && (
                <p className="fraud-panel__hint muted">
                  Click "Score now" to run real-time fraud analysis via the AI service.
                </p>
              )}

              {isLoading && <FraudSkeleton />}

              {fraudState.status === 'error' && (
                <ErrorBanner message={fraudState.message} onRetry={scoreFraud} />
              )}

              {fraudState.status === 'success' && (
                <FraudResult result={fraudState.data} />
              )}
            </div>

          </div>
        )}
      </aside>
    </>
  );
}
