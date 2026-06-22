// components/ErrorBanner.tsx
interface Props {
  message: string;
  onRetry?: () => void;
}
export function ErrorBanner({ message, onRetry }: Props) {
  return (
    <div className="error-banner" role="alert">
      <span className="error-banner__icon">⚠</span>
      <span className="error-banner__msg">{message}</span>
      {onRetry && (
        <button type="button" className="btn btn--sm btn--ghost" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
