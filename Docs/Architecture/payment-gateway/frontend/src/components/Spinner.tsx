// components/Spinner.tsx
interface Props { size?: 'sm' | 'md' | 'lg'; }
export function Spinner({ size = 'md' }: Props) {
  return <span className={`spinner spinner--${size}`} aria-label="Loading" />;
}
