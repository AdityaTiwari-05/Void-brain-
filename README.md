# OPERATION ABHEDYA-CHAKRA
### Void Hacks() 8.0 — Cyber Security & Digital Forensics

> **Mission:** A locally deployable financial-forensics engine that ingests 2M+ banking transactions,
> detects multi-tier money-mule networks, traces stolen funds 4 hops downstream, visualises the
> network interactively, and generates evidence-grounded, AI-validated investigation documents.
>
> **SAFE CITIZENS · SECURE TRANSACTIONS · STRONGER SOCIETY**

---

## Architecture

```
Bank CSV/XLSX
      ↓
MODULE A  — Stream ingestion + normalisation + DuckDB (single source of truth)
      ↓
MODULE B  — Pass-through, Fan-in/out, Terminal, Cycles, Risk 0–100, 4-hop trace
      ↓
MODULE C  — React investigator UI (network graph, timeline, transaction explorer)
      ↓
MODULE D  — Evidence packet → Local AI → Programmatic validator → Case documents
```

**Golden rule:** Database = source of truth. AI may never invent evidence.

---

## Quick Start (Windows)

```powershell
# Run the guided startup script
.\start.ps1
```

Or manually:

### 1 — Backend

```bash
cd backend

# Install Python dependencies
pip install -r requirements.txt

# Copy config (defaults work out of the box)
cp .env.example .env

# Start API server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

- API: http://localhost:8000
- Swagger: http://localhost:8000/docs

### 2 — Frontend

```bash
cd frontend

npm install
npm run dev
```

- UI: http://localhost:5173

### 3 — Ingest Data

```bash
# Generate 100K synthetic rows for quick testing
python scripts/generate_synthetic.py --rows 100000 --output data/raw/test.csv

# Ingest via CLI
cd backend
python -m app.ingestion ingest --input data/raw/test.csv

# OR use the dashboard: enter file path → click Ingest
```

### 4 — Optional: Enable AI Generation

```bash
# Install Ollama (https://ollama.ai) then pull a model
ollama pull mistral
# The system auto-detects Ollama on http://localhost:11434
# Without Ollama → deterministic fallback (always works offline)
```

---

## Demo Flow (Hackathon Judge Walkthrough)

```
1.  Enter victim account ID in Investigation Workspace
2.  Click TRACE MONEY (≤ 2s on hardware-conformant setup)
3.  See Layer 1 → 2 → 3 → 4 coloured graph
4.  Play timeline — watch fund flow chronologically
5.  Click a node → Mule Risk Index + detection reasons
6.  Click an edge → exact transaction evidence
7.  Navigate to Network Analysis → expand subgraph
8.  Navigate to Suspicious Accounts → IP visible in table
9.  Expand columns → full account data
10. Export CSV / Excel
11. Navigate to AI Case Officer → click Evidence Packet
12. Click Case Diary → validates instantly, shows DRAFT
13. Click Freeze Requisition → backend-identified candidates only
14. Click Validate Document → paste fabricated account → REJECTED
15. Click Injection Safety → narration treated as data, never executed
```

---

## API Reference

### Module A — Ingestion
| Method | Route | Description |
|--------|-------|-------------|
| `POST` | `/v1/ingestion/ingest` | Trigger CSV ingestion (background) |
| `GET`  | `/v1/ingestion/status` | Readiness + row counts |
| `GET`  | `/v1/ingestion/job-status` | Current background job |
| `GET`  | `/v1/ingestion/runs` | Ingestion run history |

### Module B — Detection
| Method | Route | Description |
|--------|-------|-------------|
| `GET`  | `/v1/detection/accounts/{id}/risk` | Mule Risk Index 0–100 |
| `GET`  | `/v1/detection/accounts/{id}/features` | Feature vector |
| `GET`  | `/v1/detection/accounts/{id}/evidence` | Full detection evidence |
| `POST` | `/v1/detection/trace` | 4-hop downstream trace |
| `GET`  | `/v1/detection/suspicious` | Pre-filtered accounts |
| `GET`  | `/v1/detection/model/evaluation` | ML model status |

### Module C — Accounts & Transactions
| Method | Route | Description |
|--------|-------|-------------|
| `GET`  | `/v1/accounts` | Paginated account list |
| `GET`  | `/v1/accounts/summary` | Dataset statistics |
| `GET`  | `/v1/accounts/{id}` | Account profile |
| `GET`  | `/v1/accounts/{id}/transactions` | Cursor-paginated transactions |
| `GET`  | `/v1/accounts/{id}/counterparties` | Aggregated counterparties |
| `GET`  | `/v1/accounts/{id}/timeline` | Chronological timeline |
| `GET`  | `/v1/accounts/{id}/graph` | **Bounded** focused subgraph |
| `GET`  | `/v1/transactions` | Filtered + paginated transactions |
| `GET`  | `/v1/transactions/stats` | Aggregate statistics |
| `GET`  | `/v1/transactions/{id}` | Single transaction detail |

### Module D — AI Case Officer
| Method | Route | Description |
|--------|-------|-------------|
| `GET`  | `/v1/investigations/ai-status` | Local AI availability |
| `POST` | `/v1/investigations/evidence-packet` | Build validated evidence packet |
| `POST` | `/v1/investigations/case-diary` | Generate + validate case diary |
| `POST` | `/v1/investigations/freeze-requisition` | Generate + validate freeze requisition |
| `POST` | `/v1/investigations/validate-document` | Validate any text against evidence |
| `POST` | `/v1/investigations/test-injection` | Prompt injection safety test |

---

## Running Tests

```bash
cd backend

# Full test suite
python -m pytest tests/ -v

# Module D only (anti-hallucination + injection)
python -m pytest tests/test_module_d.py -v

# Integration (API endpoints)
python -m pytest tests/test_api_integration.py -v

# Specific test
python -m pytest tests/test_detection.py::TestHopTracer -v
```

---

## Running Benchmarks

```bash
# 100K rows (quick — ~5s)
python scripts/benchmark_ingestion.py --rows 100000

# 2M rows (full hackathon target)
python scripts/benchmark_ingestion.py --rows 2000000 --output data/raw/synthetic_2m.csv

# Use existing file
python scripts/benchmark_ingestion.py --use-existing data/raw/transactions.csv
```

Results are written to `data/benchmarks/benchmark_*.json`.

---

## Hackathon Targets

| Requirement | Target | Implementation |
|-------------|--------|----------------|
| Transaction dataset | 2,000,000+ records | DuckDB streaming CSV ingestion |
| Ingestion time | ≤ 60 seconds | Vectorised SQL — no Python row loops |
| RAM budget | 16 GB | `SET memory_limit='10GB'` + streaming |
| Money-trail trace | ≤ 2 seconds | Indexed DuckDB graph traversal |
| Trace depth | 4 hops | Chronologically validated BFS |
| Browser graph | Bounded 500+ nodes | Focused subgraph — never 2M rows |
| Core processing | Offline/local | No cloud dependencies whatsoever |
| AI generation | Local | Ollama → llama.cpp → deterministic |

### If a target is missed

The benchmark script reports:
- `TARGET` value
- `ACTUAL` measured value
- `BOTTLENECK` description
- `POSSIBLE_OPTIMIZATION` note

Results are never fabricated.

---

## Module D — Anti-Hallucination Architecture

```
Investigation request
        ↓
Backend evidence retrieval (DuckDB only)
        ↓
Deterministic calculations (exact decimal arithmetic)
        ↓
Structured evidence packet (bounded, validated, whitelisted)
        ↓
Local AI model [Ollama/llama.cpp] OR deterministic fallback
        ↓  ← narration placed in DATA section only (never as instruction)
Generated structured JSON draft
        ↓
Programmatic evidence validator (code-level, not prompt-level)
  • Every account number checked against whitelist
  • Every IFSC code checked against whitelist
  • Every transaction ID checked against whitelist
  • Every holding candidate verified as backend-identified
        ↓
PASS → Final DRAFT document (human review required)
FAIL → REJECTED with specific unsupported entities listed
```

### Prompt Injection Defense

Transaction narrations are untrusted data. They are:
- Placed in a clearly delimited `=== DATA SECTION ===` of the prompt
- Never embedded in system instructions
- Never executed as SQL, shell commands, or instructions
- Tested via `POST /v1/investigations/test-injection`

---

## Project Structure

```
Void-brain--main/
│
├── backend/
│   ├── app/
│   │   ├── ai/                     Module D: evidence_packet, local_model, hallucination_validator
│   │   ├── api/
│   │   │   └── routes/             accounts, transactions, detection, ingestion, investigations
│   │   ├── config/                 Pydantic settings
│   │   ├── core/                   analytics.py (DuckDB read-only connection)
│   │   ├── database/               schema.py (DuckDB DDL)
│   │   ├── detection/              fan_in, fan_out, pass_through, terminal, cycles, hop_tracer, risk_scorer
│   │   │   └── ml/                 predictor.py, trainer.py
│   │   ├── ingestion/              engine.py (Module A), query.py
│   │   ├── schemas/                Pydantic models
│   │   ├── validation/             schema_check.py
│   │   └── main.py                 FastAPI app factory
│   ├── tests/
│   │   ├── conftest.py
│   │   ├── test_detection.py       Module B tests
│   │   ├── test_ingestion.py       Module A tests
│   │   ├── test_module_d.py        Module D: hallucination, injection, evidence packet
│   │   ├── test_api_integration.py Full API endpoint tests
│   │   └── test_validation.py
│   ├── .env.example
│   ├── pytest.ini
│   └── requirements.txt
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── layout/             Layout, Sidebar, TopBar
│   │   │   └── ui/                 RiskBadge, Spinner, EmptyState
│   │   ├── pages/
│   │   │   ├── dashboard/          Command Overview + ingestion trigger
│   │   │   ├── transactions/       Server-side filtered transaction explorer
│   │   │   ├── suspicious/         Flagged mule account table + risk inspector
│   │   │   ├── network/            Cytoscape.js network graph (bounded subgraph)
│   │   │   ├── investigation/      4-hop workspace + timeline playback
│   │   │   ├── ai-officer/         Module D: evidence, case diary, freeze requisition
│   │   │   └── reports/            CSV/JSON exports
│   │   ├── services/api.ts         Centralised API layer
│   │   └── types/index.ts          Full TypeScript types
│   ├── index.html
│   ├── package.json
│   ├── tailwind.config.js
│   ├── tsconfig.json
│   └── vite.config.ts
│
├── scripts/
│   ├── generate_synthetic.py       Synthetic CSV generator (100K–2M rows)
│   ├── benchmark_ingestion.py      Performance benchmarks
│   └── verify_setup.py             Pre-flight environment check
│
├── start.ps1                       Windows PowerShell startup guide
└── README.md
```

---

## Security Notes

- No secrets in source code — use `.env` file
- All SQL uses parameterised queries (no injection risk)
- Transaction narrations are untrusted data throughout
- AI model has no database credentials, no shell access, no SQL access
- All generated documents are marked **DRAFT — FOR HUMAN REVIEW**
- No cloud AI APIs — all AI runs locally
- No financial data is transmitted to external services

---

## Correctness Guarantees

The system never:
- Fabricates transaction IDs, account numbers, or IFSCs
- Claims exact stolen-fund attribution when funds may be mixed
- Presents risk scores as criminal findings
- Allows AI output to become evidence without validation
- Silently discards malformed rows (they go to `ingestion_errors` table)
- Loads 2M transactions into the browser

---

*Void Hacks() 8.0 — Cyber Security & Digital Forensics*
