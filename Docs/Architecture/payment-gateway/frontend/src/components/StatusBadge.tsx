// components/StatusBadge.tsx
import type { TxnStatus, SettleStatus, FraudDecision, SearchMode, KnowledgeCategory } from '../types';

type AnyStatus = TxnStatus | SettleStatus | FraudDecision | SearchMode | KnowledgeCategory | string;

const COLOR_MAP: Record<string, string> = {
  // TxnStatus
  success:          'badge--green',
  failed:           'badge--red',
  pending:          'badge--amber',
  flagged:          'badge--amber',
  reversed:         'badge--muted',
  // SettleStatus
  settled:          'badge--green',
  disputed:         'badge--red',
  // FraudDecision
  allow:            'badge--green',
  review:           'badge--amber',
  reject:           'badge--red',
  // SearchMode
  vector:           'badge--blue',
  keyword:          'badge--muted',
  hybrid:           'badge--blue',
  // KnowledgeCategory
  refund:           'badge--teal',
  chargeback:       'badge--purple',
  fraud:            'badge--red',
  settlement:       'badge--blue',
  payment_failure:  'badge--amber',
  general:          'badge--muted',
};

interface Props {
  value: AnyStatus;
  prefix?: string;
}

const LABEL_MAP: Record<string, string> = {
  payment_failure: 'failure',
};

export function StatusBadge({ value, prefix }: Props) {
  const cls   = COLOR_MAP[value] ?? 'badge--muted';
  const raw   = LABEL_MAP[value] ?? value;
  const label = prefix ? `${prefix}: ${raw}` : raw;
  return <span className={`badge ${cls}`}>{label}</span>;
}
