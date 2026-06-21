// components/SummaryCards.tsx — four at-a-glance metrics derived live from the
// fetched transaction list. No separate API call; reuses what the table already loaded.
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

export function SummaryCards({ transactions, loading }: Props) {
  const total   = transactions.length;
  const success = transactions.filter(t => t.status === 'success').length;
  const flagged = transactions.filter(t => t.status === 'flagged').length;
  const failed  = transactions.filter(t => t.status === 'failed').length;

  const volume = transactions
    .filter(t => t.status === 'success')
    .reduce((sum, t) => sum + parseFloat(t.amount), 0);

  // Detect dominant currency
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
    },
    {
      label:   'Volume',
      value:   loading ? null : fmtAmount(volume, currency),
      sub:     'successful payments',
      accent:  'var(--green)',
    },
    {
      label:   'Flagged',
      value:   loading ? null : fmt(flagged),
      sub:     'awaiting review',
      accent:  'var(--amber)',
    },
    {
      label:   'Failed',
      value:   loading ? null : fmt(failed),
      sub:     `of ${fmt(total)} attempts`,
      accent:  'var(--red)',
    },
  ];

  return (
    <div className="summary-cards">
      {cards.map(card => (
        <div key={card.label} className="summary-card" style={{ '--card-accent': card.accent } as React.CSSProperties}>
          <p className="summary-card__label">{card.label}</p>
          <p className="summary-card__value">
            {card.value === null ? <Spinner size="sm" /> : card.value}
          </p>
          <p className="summary-card__sub">{card.sub}</p>
        </div>
      ))}
    </div>
  );
}
