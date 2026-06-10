# AI-Powered Transaction Processing Pipeline

FastAPI backend for uploading transaction CSV files, cleaning the data, detecting anomalies, classifying uncategorised transactions with Gemini 2.5 Flash, and returning job summaries/results.

## Setup

```bash
cp .env.example .env
# Fill in GEMINI_API_KEY in .env
docker compose up --build
```

The stack starts four services: PostgreSQL, Redis, FastAPI, and a Celery worker. Database tables are created automatically when the API starts.

## Example curl requests

```bash
# Upload CSV
curl -X POST http://localhost:8000/jobs/upload \
  -F "file=@transactions.csv"

# Check status
curl http://localhost:8000/jobs/<job_id>/status

# Get results
curl http://localhost:8000/jobs/<job_id>/results

# List all jobs
curl http://localhost:8000/jobs
curl "http://localhost:8000/jobs?status=completed"
```

## API

`POST /jobs/upload` accepts a multipart CSV file under the `file` field. The CSV must contain these columns:

```text
txn_id, date, merchant, amount, currency, status, category, account_id, notes
```

`GET /jobs/{job_id}/status` returns the current job state and includes a compact summary once complete.

`GET /jobs/{job_id}/results` returns the persisted summary, anomalies, category breakdown, and cleaned transaction records. It returns `400` until the job has completed.

`GET /jobs` lists jobs newest first and supports `?status=pending|processing|completed|failed`.

## AI Classification Prompt

Transactions with a missing, blank, or `Uncategorised` category are classified in one batched Gemini 2.5 Flash call. The prompt used is:

```text
You are a financial transaction categoriser.

Classify each transaction into exactly one of these categories:
Food, Shopping, Travel, Transport, Utilities, Cash Withdrawal, Entertainment, Other

Return ONLY a valid JSON array with no extra text, markdown, or explanation.
Each element must be an object: {"txn_index": <int>, "category": "<category>"}
Return exactly one object for every transaction listed below.

Transactions to classify:
[
  {"txn_index": 0, "merchant": "Swiggy", "amount": 423.91, "currency": "INR"}
]
```

Expected response:

```json
[
  {"txn_index": 0, "category": "Food"}
]
```

The worker stores the selected category in both `category` and `llm_category`. If Gemini fails after all retries, the row is marked `llm_failed=true` and assigned `Other` so results never keep an empty category.

## AI Summary Generation Prompt

Once all transactions are processed and classified, a summary is generated using Gemini 2.5 Flash. The prompt used is:

```text
You are a financial analyst. Given the following transaction summary data, produce a JSON
object with exactly these fields:
- total_spend_inr: number (sum of all INR transaction amounts with status SUCCESS)
- total_spend_usd: number (sum of all USD transaction amounts with status SUCCESS)
- top_merchants: array of 3 objects {merchant: string, total_amount: number}, sorted desc
- anomaly_count: number
- narrative: string (2-3 sentences describing spending patterns and risks)
- risk_level: string, one of "low", "medium", "high"

Return ONLY valid JSON. No markdown, no explanation.
```

Expected response format:

```json
{
  "total_spend_inr": 5234.50,
  "total_spend_usd": 125.75,
  "top_merchants": [
    {"merchant": "Amazon", "total_amount": 2500.00},
    {"merchant": "Swiggy", "total_amount": 1200.00},
    {"merchant": "Uber", "total_amount": 800.00}
  ],
  "anomaly_count": 3,
  "narrative": "Spending patterns show concentration in shopping and food categories. Three anomalous transactions detected.",
  "risk_level": "medium"
}
```

## Web Frontend

A clean, minimal web interface is provided to interact with the API without using curl commands.

### Features

- 📤 **Upload CSV Files** - Select and upload transaction CSVs for processing
- 📋 **Check Job Status** - Monitor processing status by Job ID
- 📊 **View Job Results** - Retrieve summaries, anomalies, and categorized transactions
- 📑 **List All Jobs** - Browse all jobs with optional status filtering (pending/processing/completed/failed)
- 🎨 **Clean UI** - Modern, responsive design with real-time status updates
- ⚡ **No Authentication** - Quick access for testing and development

### Running the Frontend

1. **Open the HTML file directly in your browser:**
   ```bash
   # On Linux/macOS
   open ../frontend.html
   
   # On Linux with file manager
   xdg-open ../frontend.html
   
   # Or simply double-click the file in your file explorer
   ```

2. **Make sure the backend is running:**
   ```bash
   docker compose up --build
   ```

3. **The frontend will connect to the API** at `http://localhost:8001` (configurable in the interface)

### Frontend Capabilities

| Feature | Description |
|---------|-------------|
| Upload CSV | Select a CSV file with required columns and submit for processing |
| Job Status | Check real-time processing status, row counts, and error messages |
| Job Results | View final summary (spend totals, top merchants, risk level), anomalies, and categorization breakdown |
| Job Listing | Display all jobs in a sortable table with status badges and timestamps |

### CSV File Requirements

The CSV must contain exactly these 9 columns:
- `txn_id` - Transaction ID
- `date` - Transaction date
- `merchant` - Merchant name
- `amount` - Transaction amount
- `currency` - Currency (INR, USD, etc.)
- `status` - Transaction status (SUCCESS, FAILED, etc.)
- `category` - Transaction category (or leave blank for auto-classification)
- `account_id` - Account identifier
- `notes` - Additional notes

### Example Workflow

1. Click **"Upload CSV File"** and select your transaction file
2. Submit the form and receive a **Job ID**
3. Use the **Job ID** to check status or retrieve results
4. View the financial analysis with AI-generated insights
5. Filter and browse all jobs using the job listing

## WHY

### Directory Structure

The project follows **modular, layered architecture** for maintainability, testability, and scalability:

```
app/
├── main.py              # FastAPI application, router setup, CORS middleware
├── config.py            # Environment variables and settings (single source of truth)
├── database.py          # SQLAlchemy engine, SessionLocal (database connection management)
├── models.py            # ORM models (Job, Transaction, Anomaly, JobSummary)
├── schemas.py           # Pydantic request/response schemas (validation & serialization)
├── Dockerfile           # Container image definition
├── requirements.txt     # Python dependencies
├── routers/
│   └── jobs.py          # API endpoints (upload, status, results, list)
├── services/
│   ├── anomaly.py       # Anomaly detection logic (statistical analysis)
│   ├── cleaner.py       # Data cleaning pipeline (standardization, validation)
│   └── llm.py           # LLM calls to Gemini (classification, summarization)
└── worker/
    ├── celery_app.py    # Celery configuration & Redis connection
    └── tasks.py         # Async tasks (clean, anomaly_detect, classify, summarize)
```

**Why this structure?**
- **Separation of Concerns**: Business logic (`services/`) isolated from API handlers (`routers/`)
- **Async Processing**: `worker/` decoupled from FastAPI for independent scaling
- **Reusability**: `services/` modules can be used in other projects
- **Testability**: Each layer can be tested independently
- **Configuration Management**: `config.py` centralizes all settings (12-factor app principle)
- **Database Abstraction**: `database.py` and `models.py` abstract ORM details from routes

---

### Database Schema

Four core tables designed for **relational integrity, query efficiency, and audit trails**:

#### 1. **Jobs** table (parent table)
```sql
id (UUID primary key)
filename (text, audit trail)
status (enum: pending|processing|completed|failed)
created_at (timestamp with timezone, audit)
completed_at (timestamp with timezone, nullable)
error_message (text, nullable, debugging)
row_count (integer, metadata)
```
**Why?** Central registry for all uploads. Status tracking enables polling. Timestamps (UTC) allow SLA monitoring. `row_count` avoids expensive COUNT queries.

#### 2. **Transactions** table (core data)
```sql
id (UUID primary key)
job_id (UUID foreign key → Jobs)
txn_id (text, original transaction ID)
date (date, queryable)
merchant (text, indexed for aggregation)
amount (numeric, high precision for financial data)
currency (text, multi-currency support)
status (text, transaction-level state)
category (text, original category)
llm_category (text, Gemini-classified category)
account_id (text, customer segmentation)
notes (text, context)
llm_failed (boolean, fallback tracking)
created_at (timestamp with timezone)
```
**Why?** Denormalized merchant names for quick aggregations (top merchants query). Separate `llm_category` tracks AI classification vs. original, enabling accuracy measurement. `llm_failed` flag avoids retrying failed LLM calls. Numeric type for `amount` prevents rounding errors in financial calculations. Foreign key ensures referential integrity.

#### 3. **Anomalies** table (flagged transactions)
```sql
id (UUID primary key)
job_id (UUID foreign key → Jobs)
transaction_id (UUID foreign key → Transactions)
anomaly_type (text: "outlier"|"unusual_pattern"|"high_value")
severity (text: "low"|"medium"|"high")
details (jsonb, structured anomaly metadata)
created_at (timestamp with timezone)
```
**Why?** Separate table enables efficient anomaly querying without scanning all transactions. JSONB `details` stores variable anomaly metadata (mean, std dev, percentile) flexibly. Severity classification allows risk-based alerting.

#### 4. **JobSummaries** table (final results)
```sql
id (UUID primary key)
job_id (UUID foreign key → Jobs, unique)
total_spend_inr (numeric)
total_spend_usd (numeric)
top_merchants (jsonb, array of {merchant, total_amount})
anomaly_count (integer)
narrative (text, 2-3 sentences)
risk_level (text: "low"|"medium"|"high")
created_at (timestamp with timezone)
```
**Why?** Immutable record of AI-generated summary. Stores final output once, avoiding re-computation. Unique constraint on `job_id` enforces one summary per job. JSONB for flexible top merchants array (future: add timestamps, growth trends).

**Relationship Design:**
- One Job → Many Transactions (1:N)
- One Job → Many Anomalies (1:N)
- One Job → One Summary (1:1 unique constraint)
- Transaction → Anomaly (1:N, each anomaly references one transaction)

**Timezone Strategy:** All timestamps use `DateTime(timezone=True)` with UTC storage. Browser displays local time for UX; API returns UTC for consistency.

---

### Specific Libraries Used

#### **FastAPI** (HTTP framework)
```
pip install fastapi uvicorn
```
**Why?**
- **Async native**: Handles 100+ concurrent requests with single thread (vs Flask + threading)
- **Auto API docs**: Swagger UI + OpenAPI spec generated automatically
- **Type hints validation**: Pydantic integrations catch invalid requests before business logic
- **Performance**: ~3-5x faster than Flask for I/O-bound operations (async database calls, LLM waits)

#### **SQLAlchemy + PostgreSQL** (ORM + Database)
```
pip install sqlalchemy psycopg2-binary
```
**Why?**
- **ORM abstraction**: Write Python classes instead of raw SQL; port to MySQL/SQLite trivially
- **Lazy loading & eager loading**: Optimize N+1 query problems (e.g., load job + all transactions in one query)
- **ACID transactions**: Ensures financial data consistency (all-or-nothing writes)
- **PostgreSQL features**: JSONB, UUID types, full-text search (future enhancements)
- **Connection pooling**: Reuses DB connections (~10 pool size) vs. creating per-request

#### **Celery + Redis** (Async Task Queue)
```
pip install celery redis
```
**Why?**
- **Decoupled processing**: Client upload returns immediately; backend processes in background
- **Retry logic**: Automatic exponential backoff for failed tasks (Gemini API rate limits)
- **Multi-stage pipeline**: Tasks chained sequentially (clean → anomaly → classify → summarize)
- **Scalability**: Run 10+ workers on separate machines; horizontally scale
- **Persistence**: Failed tasks survive worker crashes (stored in Redis)
- **Alternative**: RabbitMQ better for 10,000+ msg/sec; Redis sufficient here

#### **Pydantic** (Data Validation)
```
pip install pydantic
```
**Why?**
- **Type safety**: Catches invalid JSON schema before reaching business logic
- **Performance**: Parsing/validation at HTTP layer (fail fast)
- **Auto serialization**: Convert ORM objects → JSON automatically (no manual mapping)

#### **Google Gemini 2.5 Flash API** (LLM Classification & Summary)
```
pip install google-generativeai
```
**Why?**
- **Cost-effective**: Flash model (cheaper than Pro) sufficient for categorization & summary
- **Batch support**: Single API call for 100+ transactions (saves latency vs. per-transaction calls)
- **Structured output**: JSON mode ensures parseable responses (no hallucinated text)
- **Retry-safe**: Stateless LLM calls can safely retry on network failures

#### **Python-multipart** (File Upload)
```
pip install python-multipart
```
**Why?**
- **Streaming**: Parse large CSV files without loading into memory
- **MIME validation**: Ensure only `.csv` files uploaded

#### **Python-dotenv** (Environment Config)
```
pip install python-dotenv
```
**Why?**
- **Secrets management**: Load `GEMINI_API_KEY` from `.env` (never hardcode)
- **Environment parity**: Same `.env` schema for dev/staging/prod (swap values only)

---

### Architecture Decisions Rationale

| Decision | Why Not Alternative | Trade-off |
|----------|---------------------|-----------|
| **Async Celery pipeline** | Sync processing blocks API, user waits 2-5 min | Higher infrastructure complexity (Redis + workers) |
| **PostgreSQL over SQLite** | SQLite locks entire DB during writes; 10 concurrent requests fail | More DevOps (manage server, backups) |
| **Redis queue over direct DB** | Polling DB for tasks creates high latency & CPU spike | Another service to monitor (Redis) |
| **Gemini 2.5 Flash** | Open-source models hallucinate category names; GPT-4 expensive | Vendor lock-in (Google API) |
| **Modular services/ folder** | Single monolithic file easier to start | Hard to test/scale; violates SRP |
| **JSONB for anomaly details** | Fixed SQL columns more rigid; duplicates data | Query JSONB less efficient (use indexes) |

## Notes

Gemini calls are retried three times with exponential backoff. If classification fails, the job continues and marks the affected rows with `llm_failed=true`. If narrative generation fails, the service stores a deterministic local summary so the job can still complete.
