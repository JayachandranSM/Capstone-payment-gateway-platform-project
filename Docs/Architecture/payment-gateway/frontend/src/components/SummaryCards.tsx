// components/SummaryCards.tsx
import type { Transaction } from '../types';
import { Spinner } from './Spinner';

interface Props {
  transactions: Transaction[];
  loading: boolean;
}

function fmt(n: number) {
  return n.toLocaleString('en-IN', { maximumFractionDigits: 0 });
}

function fmtAmount(total: number, currency: string) {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: currency || 'INR',
    maximumFractionDigits: 0,
  }).format(total);
}

// Minimal SVG icons for each card
function IconTransactions() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <rect x="2" y="5" width="16" height="11" rx="2" stroke="currentColor" strokeWidth="1.4"/>
      <path d="M2 8h16" stroke="currentColor" strokeWidth="1.4"/>
      <rect x="4" y="11" width="4" height="2" rx="0.5" fill="currentColor"/>
    </svg>
  );
}
function IconVolume() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M10 3v14M5 8l5-5 5 5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M4 17h12" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
    </svg>
  );
}
function IconFlagged() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M5 3v14M5 3h10l-2.5 4L15 11H5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}
function IconFailed() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="1.4"/>
      <path d="M7 7l6 6M13 7l-6 6" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
    </svg>
  );
}

// ── AI Insights card ──────────────────────────────────────────────────────────

interface Insight {
  label: string;
  value: string;
  detail: string;
  color: string;
  icon: React.ReactNode;
}

function IconTrend() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M1 11l4-4 3 3 4-5 3-3" stroke="currentColor" strokeWidth="1.4"
        strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}
function IconShield() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M8 1.5L2 4v4c0 3.3 2.5 5.8 6 7 3.5-1.2 6-3.7 6-7V4L8 1.5z"
        stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/>
      <path d="M5.5 8l2 2 3-3" stroke="currentColor" strokeWidth="1.3"
        strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}
function IconAlert() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M8 2L1 13h14L8 2z" stroke="currentColor" strokeWidth="1.3"
        strokeLinejoin="round"/>
      <path d="M8 6v4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
      <circle cx="8" cy="11.5" r="0.75" fill="currentColor"/>
    </svg>
  );
}

function deriveInsights(transactions: Transaction[]): Insight[] {
  const total    = transactions.length;
  const flagged  = transactions.filter(t => t.status === 'flagged').length;
  const reversed = transactions.filter(t => t.status === 'reversed').length;
  const charged  = transactions.filter(t => t.chargeback_flag).length;
  const settled  = transactions.filter(t => t.settlement_status === 'settled').length;

  // Fraud trend
  const fraudRate = total ? (flagged / total) * 100 : 0;
  const fraudTrend = fraudRate < 5 ? 'Low'
    : fraudRate < 10 ? 'Moderate'
    : 'Elevated';
  const fraudColor = fraudRate < 5 ? 'var(--green)'
    : fraudRate < 10 ? 'var(--amber)'
    : 'var(--red)';

  // Settlement health
  const eligibleForSettle = transactions.filter(
    t => t.status === 'success' || t.status === 'reversed'
  ).length;
  const settleRate = eligibleForSettle
    ? Math.round((settled / eligibleForSettle) * 100)
    : 100;
  const settleColor = settleRate >= 80 ? 'var(--green)'
    : settleRate >= 50 ? 'var(--amber)'
    : 'var(--red)';
  const settleLabel = settleRate >= 80 ? 'Healthy' : settleRate >= 50 ? 'Partial' : 'Lagging';

  // Chargeback risk
  const cbRate = total ? (charged / total) * 100 : 0;
  const cbRisk = cbRate < 1 ? 'Low' : cbRate < 3 ? 'Moderate' : 'High';
  const cbColor = cbRate < 1 ? 'var(--green)' : cbRate < 3 ? 'var(--amber)' : 'var(--red)';

  return [
    {
      label:  'Fraud Trend',
      value:  fraudTrend,
      detail: `${flagged} flagged of ${total} transactions (${fraudRate.toFixed(1)}%)`,
      color:  fraudColor,
      icon:   <IconTrend />,
    },
    {
      label:  'Settlement Health',
      value:  settleLabel,
      detail: `${settleRate}% of eligible transactions settled`,
      color:  settleColor,
      icon:   <IconShield />,
    },
    {
      label:  'Chargeback Risk',
      value:  cbRisk,
      detail: `${charged} chargeback${charged !== 1 ? 's' : ''} · ${reversed} reversed`,
      color:  cbColor,
      icon:   <IconAlert />,
    },
  ];
}

function AIInsightsCard({ transactions, loading }: Props) {
  if (loading || transactions.length === 0) return null;

  const insights = deriveInsights(transactions);

  return (
    <div className="ai-insights">
      <div className="ai-insights__header">
        <span className="ai-insights__icon" aria-hidden="true">
          <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
            <circle cx="7.5" cy="7.5" r="6.5" stroke="currentColor" strokeWidth="1.2"/>
            <path d="M5 7.5h5M7.5 5v5" stroke="currentColor" strokeWidth="1.2"
              strokeLinecap="round"/>
          </svg>
        </span>
        <span className="ai-insights__title">AI Insights</span>
        <span className="ai-insights__subtitle">Derived from current transaction window</span>
      </div>
      <div className="ai-insights__grid">
        {insights.map(ins => (
          <div key={ins.label} className="ai-insight-item">
            <div className="ai-insight-item__top">
              <span className="ai-insight-item__icon" style={{ color: ins.color }}>
                {ins.icon}
              </span>
              <span className="ai-insight-item__label">{ins.label}</span>
            </div>
            <p className="ai-insight-item__value" style={{ color: ins.color }}>{ins.value}</p>
            <p className="ai-insight-item__detail">{ins.detail}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

export function SummaryCards({ transactions, loading }: Props) {
  const total   = transactions.length;
  const success = transactions.filter(t => t.status === 'success').length;
  const flagged = transactions.filter(t => t.status === 'flagged').length;
  const failed  = transactions.filter(t => t.status === 'failed').length;

  const volume = transactions
    .filter(t => t.status === 'success')
    .reduce((sum, t) => sum + parseFloat(t.amount), 0);

  const currencies = transactions.map(t => t.currency);
  const currency = currencies.sort(
    (a, b) => currencies.filter(v => v === b).length - currencies.filter(v => v === a).length,
  )[0] ?? 'INR';

  const successRate = total ? Math.round((success / total) * 100) : 0;

  const cards = [
    {
      label:   'Transactions',
      value:   loading ? null : fmt(total),
      sub:     `${successRate}% success rate`,
      accent:  'var(--accent)',
      icon:    <IconTransactions />,
    },
    {
      label:   'Volume',
      value:   loading ? null : fmtAmount(volume, currency),
      sub:     'successful payments',
      accent:  'var(--green)',
      icon:    <IconVolume />,
    },
    {
      label:   'Flagged',
      value:   loading ? null : fmt(flagged),
      sub:     'awaiting review',
      accent:  'var(--amber)',
      icon:    <IconFlagged />,
    },
    {
      label:   'Failed',
      value:   loading ? null : fmt(failed),
      sub:     `of ${fmt(total)} attempts`,
      accent:  'var(--red)',
      icon:    <IconFailed />,
    },
  ];

  return (
    <>
      <div className="summary-cards">
        {cards.map(card => (
          <div
            key={card.label}
            className="summary-card"
            style={{ '--card-accent': card.accent } as React.CSSProperties}
          >
            <div className="summary-card__header-row">
              <p className="summary-card__label">{card.label}</p>
              <span className="summary-card__icon" style={{ color: card.accent }}>
                {card.icon}
              </span>
            </div>
            <p className="summary-card__value">
              {card.value === null ? <Spinner size="sm" /> : card.value}
            </p>
            <p className="summary-card__sub">{card.sub}</p>
          </div>
        ))}
      </div>
      <AIInsightsCard transactions={transactions} loading={loading} />
    </>
  );
}
