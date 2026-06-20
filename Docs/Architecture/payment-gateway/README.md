# AI Payment Gateway — Platform Bootstrap

Runnable scaffold for the AI-Powered Payment Gateway capstone.
This bootstrap brings up **five containers** with one command:

| Service     | Tech                                     | Port |
|-------------|------------------------------------------|------|
| postgres    | PostgreSQL 16 + pgvector                 | 5432 |
| redis       | Redis 7 (AOF)                            | 6379 |
| core-api    | FastAPI · asyncpg · redis.asyncio        | 8000 |
| ai-service  | FastAPI · Azure OpenAI client            | 8100 |
| frontend    | React 18 + TS + Vite, served by nginx    | 3000 |

All Python services share `shared/config.py` (Pydantic Settings) and
`shared/logging_config.py` (structlog JSON). Both are copied into each
image at build time — no `pip install -e ../shared` gymnastics.

---

## Prerequisites

- **Podman 4+** with `podman-compose` (or Docker 24+ with `docker compose`).
- Linux/macOS shell with `make` (optional but recommended).
- Ports 3000, 5432, 6379, 8000, 8100 free on the host.

> **Podman + SELinux:** The bind-mount for `infra/postgres/init.sql` uses the
> `:z` flag in `podman-compose.yml`. On SELinux-enforcing hosts (Fedora, RHEL)
> this is required; on others it's a harmless no-op. Docker users can leave it.

---

## Quick start

```bash
cp .env.example .env          # edit if you want to wire Azure OpenAI
make up                       # build + start everything in background
make health                   # hit /healthz on all three services
```

Then open:

- Frontend: <http://localhost:3000>
- Core API docs: <http://localhost:8000/docs>
- AI service docs: <http://localhost:8100/docs>

Tear down:

```bash
make down       # stop containers, keep data volumes
make clean      # stop + delete volumes (fresh DB on next 'up')
```

---

## Smoke test (without make)

```bash
# 1. Each service is alive
curl -sf http://localhost:8000/healthz | python3 -m json.tool
curl -sf http://localhost:8100/healthz | python3 -m json.tool

# 2. Each service has a working DB + Redis (readiness)
curl -sf http://localhost:8000/readyz  | python3 -m json.tool
curl -sf http://localhost:8100/readyz  | python3 -m json.tool

# 3. Frontend renders and proxies to both backends
curl -sf http://localhost:3000/                | head -1   # HTTP/1.1 200 OK
curl -sf http://localhost:3000/api/core/healthz
curl -sf http://localhost:3000/api/ai/healthz
```

---

## Repository layout

```
payment-gateway/
├── .env.example
├── Makefile
├── podman-compose.yml
├── infra/postgres/init.sql           Postgres extensions + base schemas
├── shared/                           Copied into each Python image
│   ├── config.py                       Pydantic BaseSettings shared by services
│   └── logging_config.py               structlog JSON, bound to service name
├── services/
│   ├── core-api/                     FastAPI · port 8000
│   │   ├── Containerfile
│   │   ├── requirements.txt
│   │   ├── main.py                     App factory + lifespan + routes
│   │   └── app/
│   │       ├── settings.py               Extends shared BaseAppSettings
│   │       ├── deps.py                   DB pool + Redis client lifespan helpers
│   │       └── health.py                 /healthz, /readyz, /metrics router
│   └── ai-service/                   FastAPI · port 8100
│       ├── Containerfile
│       ├── requirements.txt
│       ├── main.py
│       └── app/
│           ├── settings.py
│           ├── deps.py
│           ├── health.py                 Includes Azure OpenAI readiness
│           └── llm/client.py             Async AzureOpenAI client, lazy-init
└── frontend/
    ├── Containerfile                   Multi-stage: node build → nginx serve
    ├── nginx.conf                      SPA fallback + SSE-safe API proxy
    ├── package.json
    ├── vite.config.ts                  Dev proxy mirrors nginx routes
    ├── tsconfig.json / tsconfig.node.json
    ├── index.html
    └── src/
        ├── main.tsx
        ├── App.tsx                       Status dashboard hitting both services
        ├── styles.css
        └── api/health.ts                 Typed fetch wrapper
```

---

## Health endpoint contract

Every Python service exposes the same three endpoints:

| Endpoint    | Semantics    | Behaviour                                                    |
|-------------|--------------|--------------------------------------------------------------|
| `/healthz`  | Liveness     | Returns 200 as long as the process is running.               |
| `/readyz`   | Readiness    | Pings Postgres + Redis (+ Azure OpenAI on ai-service). Returns 200 only if all critical deps respond. |
| `/metrics`  | Observability | Plain-text process metrics (stub — Prometheus exporter to be wired later). |

`/healthz` is what the container runtime polls. `/readyz` is what a load
balancer / service mesh would poll to gate traffic.

---

## Azure OpenAI wiring

The `ai-service` boots **without** Azure OpenAI credentials and runs in
*degraded* mode: `/healthz` is 200, but `/readyz` reports
`"azure_openai": "not_configured"`. Fill in `AZURE_OPENAI_ENDPOINT` and
`AZURE_OPENAI_API_KEY` in `.env`, then `make restart` and `/readyz`
reports `"azure_openai": "ok"`.

This is the same pattern used for the production "graceful degradation"
requirement — see `docs/decisions/DECISIONS.md` ADR-007 and ADR-012.

---

## Known good versions (pinned in `requirements.txt` / `package.json`)

- Python 3.12, FastAPI 0.115, asyncpg 0.30, redis-py 5.2, structlog 24.4, openai 1.54
- Node 20 (LTS), React 18.3, Vite 5.4, TypeScript 5.6
- Postgres 16 (pgvector image tag `pg16`), Redis 7-alpine, nginx 1.27-alpine

Upgrade these in lockstep, not piecemeal.
