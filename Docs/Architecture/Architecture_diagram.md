# AI-Powered Payment Gateway — Architecture Diagrams

**Companion file to:** `ARCHITECTURE_REVIEW.md`
**Purpose:** All architectural diagrams in Mermaid. Render natively in GitHub, GitLab, Notion, VS Code preview, and Confluence (with plugin).

> **Reading the diagrams:** Solid arrows are synchronous calls. Dotted arrows are asynchronous / observational. Subgraphs represent deployment or logical groupings, not network boundaries unless labelled as such.

---

## 1. High-Level Architecture

The eight-layer production architecture from `ARCHITECTURE_REVIEW.md §3`. Layers are stacked top-down — each layer depends only on layers below it.

```mermaid
flowchart TB
    Client["Web / Mobile / Merchant API Clients"]

    subgraph L0["L0 — Edge"]
        direction LR
        CDN[CDN]
        WAF[WAF + DDoS]
        GLB[Global LB - Anycast]
    end

    subgraph L1["L1 — Ingress"]
        direction LR
        APIGW["API Gateway<br/>Kong / Envoy"]
        RL[Rate Limit + Auth]
    end

    subgraph L2["L2 — Service Mesh"]
        MESH["Istio Sidecars<br/>mTLS · retries · tracing"]
    end

    subgraph L3["L3 — Transactional Microservices on K8s"]
        direction LR
        IDENT[identity]
        WALLET[wallet]
        PAY[payment]
        LEDGER_S[ledger]
        FRAUD[fraud]
        SETTLE[settlement]
        DISP[dispute]
        MERCH[merchant]
        NOTIF[notification]
        AUDIT[audit]
    end

    subgraph L3AI["L3 — AI Microservices on K8s"]
        direction LR
        ASSIST[ai-assistant]
        INGEST[ingestion]
        EVAL[evaluation]
    end

    subgraph L4["L4 — Event Backbone"]
        KAFKA["Kafka<br/>Schema Registry + DLQ"]
    end

    subgraph L5["L5 — AI / ML Plane"]
        direction LR
        MODELS["Model Serving<br/>vLLM / KServe"]
        EMBED[Embedding Service]
        RERANK[Reranker]
        FEAT["Feature Store<br/>Feast"]
    end

    subgraph L6["L6 — Data Plane"]
        direction LR
        PG[("Postgres OLTP")]
        LDB[("Ledger Cluster<br/>CockroachDB")]
        REDIS[("Redis Cluster")]
        OS[("OpenSearch")]
        VDB[("Milvus Vector DB")]
        TS[("TimescaleDB")]
        BLOB[("S3 / Blob")]
        TVAULT["Tokenisation Vault"]
    end

    subgraph L7["L7 — Cross-cutting"]
        direction LR
        OBS["OTel · Prometheus · Grafana · Loki"]
        SEC["Vault · KMS / HSM · OPA"]
        MLOPS["MLflow · Evidently · DeepEval"]
    end

    Client --> L0
    L0 --> L1
    L1 --> L2
    L2 --> L3
    L2 --> L3AI
    L3 <--> L4
    L3AI <--> L4
    L3 --> L6
    L3AI --> L5
    L5 --> L6
    L3 -.OTel + secrets.-> L7
    L3AI -.OTel + MLflow.-> L7
```

---

## 2. Payment Flow

End-to-end happy path with the synchronous critical section (steps 1–11) and the asynchronous tail (steps 12+). Failure handling is shown as alt branches.

```mermaid
sequenceDiagram
    autonumber
    participant C as Client / Merchant
    participant GW as API Gateway
    participant ID as identity-service
    participant PS as payment-service
    participant FS as fraud-service
    participant LS as ledger-service
    participant PR as Provider sandbox
    participant K as Kafka
    participant N as notification-service
    participant S as settlement-service
    participant A as audit-service

    C->>GW: POST /v1/payments + Idempotency-Key
    GW->>GW: Pydantic schema validate
    GW->>GW: Redis idempotency lookup

    alt Key already seen + completed
        GW-->>C: 200 cached response
    else New request
        GW->>ID: Verify JWT + scopes
        ID-->>GW: claims
        GW->>PS: CreatePayment gRPC
        PS->>FS: ScoreTransaction sync, 80ms budget
        FS->>FS: rules → GBT model
        FS-->>PS: fraud_score + decision

        alt fraud_score above 0.8
            PS->>K: payment.flagged
            PS-->>GW: 202 flagged for review
            GW-->>C: 202 transaction_id, pending review
        else proceed
            PS->>LS: PostEntries atomic
            LS-->>PS: posting_id
            PS->>PR: charge with provider idempotency key

            alt provider timeout
                PR--xPS: timeout
                PS->>K: payment.pending
                PS-->>GW: 202 pending reconcile
            else provider success
                PR-->>PS: succeeded
                PS->>K: payment.succeeded
                PS-->>GW: 201 transaction
                GW-->>C: 201 transaction_id + status
            end
        end
    end

    K-->>N: email / SMS / webhook delivery
    K-->>S: settlement aggregation
    K-->>A: immutable audit append
```

---

## 3. Fraud Analysis Flow

This is the **asynchronous** investigation that runs after `payment.flagged`. The synchronous fraud score in §2 step 5 is rules + GBT only — no LLM blocks the money path.

```mermaid
flowchart TB
    Start(["payment.flagged event"]) --> Orch["Orchestrator Agent"]

    Orch --> Plan["Decompose into sub-tasks"]

    Plan --> P1["tool: get_transaction"]
    Plan --> P2["tool: get_user_history 30d"]
    Plan --> P3["tool: get_device_fingerprint"]
    Plan --> P4["tool: velocity_check Redis"]

    P1 --> Signals
    P2 --> Signals
    P3 --> Signals
    P4 --> Signals["Aggregated signals"]

    Signals --> FI["Fraud Investigator Agent<br/>LLM reasoning over signals"]
    Signals --> CA["Compliance Agent<br/>AML pattern check"]

    FI --> Hyp["Ranked root-cause hypotheses<br/>+ recommended actions"]
    CA --> AML["AML / sanctions flags"]

    Hyp --> Synth["Synthesise case note"]
    AML --> Synth

    Synth --> Judge{"LLM-as-Judge<br/>score 1–5"}

    Judge -->|score >= 4| Auto["Write fraud.cases<br/>Notify analyst"]
    Judge -->|score below 4| Esc["Escalate directly<br/>to human analyst"]

    Auto --> FB["Analyst labels outcome"]
    Esc --> FB

    FB --> Loop[("Episodic Memory<br/>Postgres + Vector")]
    Loop -. feeds future cases .-> FI

    style Orch fill:#dbeafe,stroke:#1e40af
    style FI fill:#fef3c7,stroke:#b45309
    style CA fill:#fef3c7,stroke:#b45309
    style Judge fill:#fee2e2,stroke:#991b1b
    style Loop fill:#dcfce7,stroke:#166534
```

---

## 4. RAG Flow

Hybrid retrieval with reciprocal rank fusion, cross-encoder rerank, and recency boost. This is the flow behind `POST /v1/assistant/query`.

```mermaid
flowchart TB
    Q(["Support agent query"]) --> Intent{"Intent classifier"}

    Intent -->|failure-explain| F1["filter: status in failed/flagged"]
    Intent -->|settlement| F2["filter: settlement_status"]
    Intent -->|dispute| F3["filter: chargeback_flag = true"]
    Intent -->|generic| F4["no filter"]

    F1 --> Embed
    F2 --> Embed
    F3 --> Embed
    F4 --> Embed["Embed query<br/>bge-base-en-v1.5"]

    Embed --> Para["Parallel retrieval"]

    Para --> BM25["BM25 over OpenSearch<br/>top 30"]
    Para --> ANN["ANN over Milvus<br/>top 30, metadata pre-filter"]

    BM25 --> RRF
    ANN --> RRF["Reciprocal Rank Fusion → top 50"]

    RRF --> Rerank["Cross-encoder rerank<br/>bge-reranker-base"]

    Rerank --> Boost["Recency + resolution-<br/>effectiveness boost → top 5"]

    Boost --> Compose["Compose prompt<br/>with citations"]

    Compose --> Router{"LLM Router"}

    Router -->|primary| LLM2["Tier 2: Haiku / GPT-4o-mini"]
    Router -.fallback.-> LLM3["Tier 3: Flan-T5 local"]

    LLM2 --> Stream["SSE stream answer + citations"]
    LLM3 --> Stream

    Stream --> User(["User"])

    Stream -. async .-> AJudge["LLM-as-Judge<br/>offline quality scoring"]
    Stream -. async .-> Log["Log query, IDs,<br/>latency, tokens"]

    AJudge --> Dash["Quality dashboard"]
    Log --> Dash

    style Para fill:#dbeafe,stroke:#1e40af
    style Rerank fill:#fef3c7,stroke:#b45309
    style Router fill:#fee2e2,stroke:#991b1b
    style Stream fill:#dcfce7,stroke:#166534
```

---

## 5. Agent Communication Flow

The hierarchical multi-agent topology with A2A messaging, shared tool layer, dual memory, and LLM-as-judge gating.

```mermaid
flowchart TB
    User(["Support agent / API"]) --> Orch["Orchestrator Agent<br/>planner + router"]

    Orch -. A2A bus .-> FraudA["Fraud Investigator"]
    Orch -. A2A bus .-> SettleA["Settlement Reconciler"]
    Orch -. A2A bus .-> DispA["Dispute Resolver"]
    Orch -. A2A bus .-> SupportA["Support Q&A Agent"]

    subgraph Tools["Shared Tool Layer — schema-validated"]
        direction LR
        T1[get_transaction]
        T2[get_user_history]
        T3[rules_engine_query]
        T4[vector_search]
        T5[keyword_search]
        T6[settlement_expected]
        T7[open_case]
        T8[notify_analyst]
    end

    FraudA --> Tools
    SettleA --> Tools
    DispA --> Tools
    SupportA --> Tools

    subgraph Memory["Memory Layer"]
        direction LR
        Sess[("Session Memory<br/>Redis · TTL 1h")]
        Epi[("Episodic Memory<br/>Postgres + Vector")]
    end

    Orch <--> Sess
    SupportA <--> Sess
    FraudA <--> Epi
    DispA <--> Epi

    FraudA --> Judge
    SettleA --> Judge
    DispA --> Judge
    SupportA --> Judge["LLM-as-Judge<br/>online · lightweight"]

    Judge -->|approved| Synth["Orchestrator synthesises<br/>final response"]
    Judge -->|rejected| Retry["Re-plan or escalate<br/>to human"]

    Synth --> User
    Retry --> Orch

    Synth -. async .-> Offline["DeepEval offline suite<br/>nightly"]
    Offline --> EvalDB[("Evaluation Service<br/>quality trends")]

    style Orch fill:#dbeafe,stroke:#1e40af
    style Judge fill:#fee2e2,stroke:#991b1b
    style Tools fill:#f3f4f6,stroke:#374151
    style Memory fill:#dcfce7,stroke:#166534
```

---

## 6. Azure Deployment Flow

The production architecture mapped onto concrete Azure services. Multi-region active-active with `Central India` as primary and `South India` as the warm pair (data-residency-friendly).

```mermaid
flowchart TB
    Users(["Users · Merchants · Mobile"])

    subgraph AzEdge["Azure Edge — Global"]
        AFD["Azure Front Door<br/>Global LB + CDN + WAF"]
        DDoS["Azure DDoS Protection<br/>Standard"]
    end

    subgraph Primary["Primary Region — Central India"]
        direction TB

        subgraph Ingress["Ingress"]
            APIM["API Management<br/>Premium tier"]
            AppGW["Application Gateway<br/>L7 + WAF"]
        end

        subgraph AKS["AKS Cluster — multi-AZ"]
            direction TB
            Istio["Istio Service Mesh"]

            subgraph TxNS["namespace: transactional"]
                direction LR
                IDp[identity]
                PAp[payment]
                LEp[ledger]
                FRp[fraud]
                SEp[settlement]
                DIp[dispute]
                Otherp[wallet · merchant · notif · audit]
            end

            subgraph AINS["namespace: ai"]
                direction LR
                AIp[ai-assistant]
                INp[ingestion]
                EVp[evaluation]
            end
        end

        subgraph DataAz["Data Services"]
            direction LR
            PGFlex[("Azure DB for PostgreSQL<br/>Flexible Server")]
            CosmosPg[("Azure Cosmos DB for PG<br/>Ledger cluster")]
            ARedis[("Azure Cache for Redis<br/>Enterprise")]
            ASearch[("Azure AI Search<br/>BM25 + vector hybrid")]
            ABlob[("Azure Blob Storage<br/>audit + artifacts")]
            ADX[("Azure Data Explorer<br/>time-series metrics")]
        end

        subgraph Messaging["Messaging"]
            EH["Event Hubs<br/>Kafka surface"]
            SB["Service Bus<br/>scheduled + DLQ"]
        end

        subgraph AIServices["AI Services"]
            AOAI["Azure OpenAI<br/>GPT-4o · 4o-mini"]
            MLW["Azure ML Workspace<br/>local model endpoints"]
            MLflow["MLflow on Azure ML"]
        end

        subgraph Sec["Security & Identity"]
            direction LR
            Entra["Microsoft Entra ID<br/>workload identity"]
            B2C["Entra External ID<br/>customer identity"]
            KV["Azure Key Vault"]
            HSM["Azure Managed HSM<br/>PCI tokenisation"]
        end

        subgraph Obs["Observability"]
            direction LR
            AppIns["Application Insights<br/>OTel ingest"]
            LAW["Log Analytics Workspace"]
            Monitor["Azure Monitor + Alerts"]
            MGrafana["Azure Managed Grafana"]
        end
    end

    subgraph Secondary["Secondary Region — South India · active-active"]
        AKS2["AKS Cluster<br/>identical workloads"]
        Data2[("Geo-replicated<br/>data services")]
    end

    Users --> AFD
    AFD --> DDoS
    DDoS --> APIM
    APIM --> AppGW
    AppGW --> Istio
    Istio --> TxNS
    Istio --> AINS

    TxNS --> PGFlex
    TxNS --> ARedis
    LEp --> CosmosPg

    TxNS <--> EH
    AINS <--> EH
    Otherp <--> SB

    AINS --> ASearch
    AINS --> AOAI
    AINS --> MLW
    INp --> ABlob
    EVp --> MLflow

    AKS -. workload identity .-> Entra
    AKS -. secrets via CSI .-> KV
    PAp -. tokenise PAN .-> HSM
    Users -. OIDC login .-> B2C

    AKS -. OTel .-> AppIns
    AppIns --> LAW
    LAW --> Monitor
    Monitor --> MGrafana

    PGFlex -. geo-replicate .-> Data2
    CosmosPg -. multi-region writes .-> Data2
    EH -. Geo-DR pairing .-> Secondary
    AKS -. GitOps · ArgoCD / Flux .-> AKS2

    style AFD fill:#0078d4,color:#fff,stroke:#004578
    style AKS fill:#0078d4,color:#fff,stroke:#004578
    style AKS2 fill:#0078d4,color:#fff,stroke:#004578
    style AOAI fill:#0078d4,color:#fff,stroke:#004578
    style PGFlex fill:#0078d4,color:#fff,stroke:#004578
    style CosmosPg fill:#0078d4,color:#fff,stroke:#004578
    style EH fill:#0078d4,color:#fff,stroke:#004578
```

### Azure service mapping (cheat sheet)

| Architectural concept | Azure service | Tier / SKU notes |
|---|---|---|
| Global edge + WAF | Azure Front Door Premium + DDoS Standard | Premium for managed WAF rules |
| API gateway | Azure API Management Premium | Premium for VNet integration + multi-region |
| L7 + regional WAF | Application Gateway v2 + WAF | Behind APIM for VNet ingress to AKS |
| Compute | AKS, multi-AZ node pools | Separate node pools for `transactional` and `ai` |
| Service mesh | Istio (AKS managed add-on) | mTLS, retries, traffic shifting |
| OLTP database | Azure Database for PostgreSQL — Flexible Server | HA-enabled, zone-redundant |
| Ledger database | Azure Cosmos DB for PostgreSQL (Citus) | Distributed; isolation from OLTP |
| Cache + sessions + idempotency | Azure Cache for Redis Enterprise | Active geo-replication |
| Hybrid search | Azure AI Search | Native vector + BM25 in one service |
| Vector DB alternative | Milvus on AKS | Choose if AI Search ranking quality is insufficient |
| Event log | Event Hubs (Kafka protocol) | Geo-DR pairing |
| Task queue / DLQ | Azure Service Bus | Scheduled messages for webhook retries |
| Object storage | Azure Blob Storage + Lifecycle | Cool + Archive tiers for audit retention |
| Time-series metrics | Azure Data Explorer (Kusto) | Sub-second analytics over tx volumes |
| LLM provider | Azure OpenAI | Data residency, PTU for latency-sensitive |
| Local model serving | Azure ML managed endpoints | For Flan-T5 fallback + embedding service |
| Experiment tracking | Azure ML + MLflow | Native MLflow integration |
| Workload identity | Microsoft Entra ID + AKS workload identity | No secrets in pods |
| Customer identity | Microsoft Entra External ID (B2C) | Social + email + MFA |
| Secrets | Azure Key Vault + CSI driver | Auto-rotation |
| Tokenisation / PCI keys | Azure Managed HSM | FIPS 140-2 Level 3 |
| Tracing + logs + metrics | App Insights + Log Analytics + Managed Grafana | OTel-native ingestion |
| GitOps deployment | Flux or ArgoCD on AKS | Drives multi-region parity |

---

## How to render these

- **GitHub / GitLab:** renders automatically in markdown previews.
- **VS Code:** install "Markdown Preview Mermaid Support" extension.
- **Confluence:** install "Mermaid Diagrams for Confluence" plugin.
- **PowerPoint export:** use `mmdc` CLI (`@mermaid-js/mermaid-cli`) to export to PNG / SVG / PDF before pasting into slides.

```
npm install -g @mermaid-js/mermaid-cli
mmdc -i DIAGRAMS.md -o diagrams.pdf
```
