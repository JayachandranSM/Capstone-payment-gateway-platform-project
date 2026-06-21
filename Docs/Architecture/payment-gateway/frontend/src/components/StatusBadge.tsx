// components/StatusBadge.tsx
import type { TxnStatus, SettleStatus, FraudDecision, SearchMode } from '../types';

type AnyStatus = TxnStatus | SettleStatus | FraudDecision | SearchMode | string;

const COLOR_MAP: Record<string, string> = {
  // TxnStatus
  success:  'badge--green',
  failed:   'badge--red',
  pending:  'badge--amber',
  flagged:  'badge--amber',
  reversed: 'badge--muted',
  // SettleStatus
  settled:  'badge--green',
  disputed: 'badge--red',
  // FraudDecision
  allow:    'badge--green',
  review:   'badge--amber',
  reject:   'badge--red',
  // SearchMode
  vector:   'badge--blue',
  keyword:  'badge--muted',
  hybrid:   'badge--blue',
};

interface Props {
  value: AnyStatus;
  prefix?: string;
}

export function StatusBadge({ value, prefix }: Props) {
  const cls = COLOR_MAP[value] ?? 'badge--muted';
  const label = prefix ? `${prefix}: ${value}` : value;
  return <span className={`badge ${cls}`}>{label}</span>;
}
