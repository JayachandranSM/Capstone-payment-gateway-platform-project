// components/DetailPanel.tsx — slide-in drawer: transaction fields + fraud scoring.
import { useEffect, useRef, useState } from 'react';
import type { FraudScoreResponse, RuleCategory, RuleHit, Transaction } from '../types';
import { aiApi } from '../api/client';
import { useAsync } from '../hooks/useAsync';
import { ErrorBanner } from './ErrorBanner';
import { Spinner } from './Spinner';
import { StatusBadge } from './StatusBadge';

// ── Helpers ──────────────────────────────────────────────────────────────────

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
// Mounts at 0 width, then transitions to target on the next frame.
// This ensures the CSS transition actually plays even when the component
// mounts with its final value already set.

function ScoreMeter({ score, color }: { score: number; color: string }) {
  const [width, setWidth] = useState(0);

  useEffect(() => {
    // Two-frame delay: first frame mounts at 0, second frame animates to target.
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

      {/* Score + decision row */}
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
        <p className="fraud-explanation">{result.explanation}</p>
      )}

      {/* Flat reasons list — when there are reasons but no rule_hits detail */}
      {result.reasons.length > 0 && result.rule_hits.length === 0 && (
        <ul className="fraud-reasons">
          {result.reasons.map((r, i) => (
            <li key={i} className="fraud-reason">{r}</li>
          ))}
        </ul>
      )}

      {/* Rule hits with category badges + collapsible evidence */}
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

      {/* Footer meta */}
      <div className="fraud-result__footer">
        <span className="fraud-result__meta muted">
          {result.model_version}
          {result.llm_used && (
            <span className="fraud-result__llm-tag"> · LLM ✓</span>
          )}
        </span>
        <span className="fraud-result__meta muted">
          Scored {fmtTime(result.scored_at)}
        </span>
      </div>
    </div>
  );
}

// ── Skeleton shown while the API call is in-flight ────────────────────────────

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

// ── Main export ───────────────────────────────────────────────────────────────

export function DetailPanel({ transaction: txn, onClose }: Props) {
  const { state: fraudState, run: runFraud, reset: resetFraud } = useAsync<FraudScoreResponse>();
  const scoreButtonRef = useRef<HTMLButtonElement>(null);

  // Reset fraud state when the selected transaction changes.
  useEffect(() => {
    resetFraud();
  }, [txn?.transaction_id, resetFraud]);

  // Close on Escape key.
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
        <div className="detail-panel__header">
          <h2 className="detail-panel__title">Transaction Detail</h2>
          <button
            type="button"
            className="panel-close"
            onClick={onClose}
            aria-label="Close detail panel"
          >
            ×
          </button>
        </div>

        {txn && (
          <div className="detail-panel__body">

            {/* ── Core transaction fields ── */}
            <dl className="detail-list">
              <Row label="ID">
                <code className="mono">{txn.transaction_id}</code>
              </Row>
              <Row label="Amount">
                <strong>{fmtMoney(txn.amount, txn.currency)}</strong>
                <span className="muted"> {txn.currency}</span>
              </Row>
              <Row label="Status">
                <StatusBadge value={txn.status} />
              </Row>
              <Row label="Method">{txn.payment_method}</Row>
              <Row label="Merchant">{txn.merchant_id ?? '—'}</Row>
              <Row label="User">
                <code className="mono sm">{txn.user_id ?? '—'}</code>
              </Row>
              <Row label="Settlement">
                <StatusBadge value={txn.settlement_status} />
              </Row>
              {txn.failure_reason && (
                <Row label="Failure">{txn.failure_reason}</Row>
              )}
              {txn.fraud_score && (
                <Row label="Stored fraud score">
                  <span className="fraud-score">
                    {(parseFloat(txn.fraud_score) * 100).toFixed(1)}
                  </span>
                  /100
                </Row>
              )}
              {txn.parent_transaction && (
                <Row label="Refund of">
                  <code className="mono sm">{txn.parent_transaction}</code>
                </Row>
              )}
              <Row label="Created">{fmtFull(txn.created_at)}</Row>
              <Row label="Updated">{fmtFull(txn.updated_at)}</Row>
              {txn.idempotency_key && (
                <Row label="Idempotency key">
                  <code className="mono sm">{txn.idempotency_key}</code>
                </Row>
              )}
              {txn.chargeback_flag && (
                <Row label="Chargeback">
                  <span className="badge badge--red">Yes</span>
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

              {/* Idle hint */}
              {fraudState.status === 'idle' && (
                <p className="fraud-panel__hint muted">
                  Click "Score now" to run real-time fraud analysis via the AI service.
                </p>
              )}

              {/* In-flight skeleton */}
              {isLoading && <FraudSkeleton />}

              {/* Error */}
              {fraudState.status === 'error' && (
                <ErrorBanner message={fraudState.message} onRetry={scoreFraud} />
              )}

              {/* Result */}
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
