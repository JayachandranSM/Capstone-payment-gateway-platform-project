// src/App.tsx — bootstrap status dashboard.
// Polls /healthz and /readyz on both backends and renders a status grid.
// Intentionally tiny — the real consoles (UserConsole, MerchantDashboard,
// SupportConsole) replace this on the frontend day.

import { useCallback, useEffect, useState } from 'react';
import { api, type ReadinessResponse } from './api/health';

type ServiceStatus = {
  name: string;
  alive: boolean;
  ready: boolean;
  checks: Record<string, string>;
  llmAvailable?: boolean;
  lastError?: string;
};

const initial = (name: string): ServiceStatus => ({
  name,
  alive: false,
  ready: false,
  checks: {},
});

export default function App() {
  const [core, setCore] = useState<ServiceStatus>(initial('core-api'));
  const [ai, setAi] = useState<ServiceStatus>(initial('ai-service'));
  const [refreshAt, setRefreshAt] = useState<Date | null>(null);

  const probe = useCallback(async () => {
    // core-api
    try {
      await api.coreHealth();
      const r = await api.coreReady();
      setCore({
        name: 'core-api',
        alive: true,
        ready: r.status === 'ready',
        checks: r.checks,
      });
    } catch (e) {
      setCore((s) => ({ ...s, alive: false, ready: false, lastError: String(e) }));
    }

    // ai-service
    try {
      await api.aiHealth();
      const r: ReadinessResponse = await api.aiReady();
      setAi({
        name: 'ai-service',
        alive: true,
        ready: r.status === 'ready',
        checks: r.checks,
        llmAvailable: r.llm_available,
      });
    } catch (e) {
      setAi((s) => ({ ...s, alive: false, ready: false, lastError: String(e) }));
    }

    setRefreshAt(new Date());
  }, []);

  useEffect(() => {
    probe();
    const id = window.setInterval(probe, 5000);
    return () => window.clearInterval(id);
  }, [probe]);

  return (
    <main className="page">
      <header className="page__header">
        <h1>Payment Gateway — Platform Status</h1>
        <p className="muted">
          Bootstrap dashboard. Polls every 5 s. Real consoles replace this view in
          a later turn.
        </p>
      </header>

      <section className="grid">
        <ServiceCard s={core} />
        <ServiceCard s={ai} />
      </section>

      <footer className="page__footer">
        <button onClick={probe} type="button" className="btn">
          Refresh now
        </button>
        {refreshAt && (
          <span className="muted">Last refreshed {refreshAt.toLocaleTimeString()}</span>
        )}
      </footer>
    </main>
  );
}

function ServiceCard({ s }: { s: ServiceStatus }) {
  const overall =
    !s.alive ? 'down' : s.ready ? 'ready' : 'degraded';

  return (
    <article className={`card card--${overall}`}>
      <header className="card__head">
        <h2>{s.name}</h2>
        <StatusPill status={overall} />
      </header>

      {s.checks && Object.keys(s.checks).length > 0 && (
        <ul className="checks">
          {Object.entries(s.checks).map(([dep, status]) => (
            <li key={dep}>
              <span className="checks__dep">{dep}</span>
              <span className={`checks__val ${status === 'ok' ? 'ok' : 'bad'}`}>
                {status}
              </span>
            </li>
          ))}
        </ul>
      )}

      {s.llmAvailable === false && s.checks?.azure_openai && (
        <p className="banner">
          ⚠ Azure OpenAI {s.checks.azure_openai}. The ai-service is operating in
          degraded mode — see DECISIONS.md ADR-012.
        </p>
      )}

      {s.lastError && <p className="error">{s.lastError}</p>}
    </article>
  );
}

function StatusPill({ status }: { status: 'ready' | 'degraded' | 'down' }) {
  return <span className={`pill pill--${status}`}>{status.toUpperCase()}</span>;
}
