# Master Prompt — Alemeno Backend Assignment
# AI-Powered Transaction Processing Pipeline

---

## CONTEXT

You are building a production-quality backend system for an internship assignment. Every
detail below is a hard requirement — do not deviate from the stack, endpoint signatures,
pipeline steps, or data model. This will be evaluated by cloning the repo and running
`docker compose up` followed by direct curl hits to the endpoints.

---

## REPOSITORY STRUCTURE

Generate the following layout exactly:

```
/
├── docker-compose.yml
├── .env.example
├── README.md
├── transactions.csv            # provided sample file, commit it
├── app/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                 # FastAPI entrypoint
│   ├── database.py             # SQLAlchemy engine + session
│   ├── models.py               # ORM models
│   ├── schemas.py              # Pydantic schemas
│   ├── routers/
│   │   └── jobs.py             # all four endpoints
│   ├── worker/
│   │   ├── celery_app.py       # Celery + Redis config
│   │   └── tasks.py            # process_job task
│   └── services/
│       ├── cleaner.py          # data cleaning logic
│       ├── anomaly.py          # anomaly detection logic
│       └── llm.py              # LLM calls (Gemini 2.5 Flash)
```

---

## REQUIRED STACK — NO SUBSTITUTIONS

| Component     | Choice                          |
|---------------|---------------------------------|
| API framework | **FastAPI**                     |
| Database      | **PostgreSQL** via SQLAlchemy   |
| Job queue     | **Celery + Redis**              |
| LLM           | **Gemini 2.5 Flash** (free tier, `google-generativeai` SDK) |
| Containers    | **Docker + Docker Compose v2**  |

The entire system must boot with a single `docker compose up` — no manual DB migrations,
no separate setup scripts. Use Alembic or run `Base.metadata.create_all()` inside a
startup event so tables are created automatically on first boot.

---

## ENVIRONMENT VARIABLES

`docker-compose.yml` must read these from an `.env` file (provide `.env.example`):

```
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=transactions_db
DATABASE_URL=postgresql://postgres:postgres@db:5432/transactions_db
REDIS_URL=redis://redis:6379/0
GEMINI_API_KEY=<user fills this in>
```

---

## DOCKER COMPOSE

Four services, all on one internal network:

```yaml
services:
  db:          # postgres:15-alpine
  redis:       # redis:7-alpine
  api:         # built from app/Dockerfile, depends_on db + redis
               # uvicorn app.main:app --host 0.0.0.0 --port 8000
               # restart: on-failure (for DB race condition on first boot)
  worker:      # same image as api
               # command: celery -A app.worker.celery_app worker --loglevel=info
               # depends_on db + redis
```

Add a `healthcheck` on the `db` service so `api` and `worker` wait for Postgres to be ready.
Use `depends_on: db: condition: service_healthy`.

---

## DATABASE MODELS (`app/models.py`)

### Job

```python
id            UUID primary key (default uuid4)
filename      String
status        String   # pending | processing | completed | failed
row_count_raw Integer  # nullable
row_count_clean Integer # nullable
created_at    DateTime (default utcnow)
completed_at  DateTime nullable
error_message Text nullable
```

### Transaction

```python
id            UUID primary key
job_id        UUID FK → Job.id
txn_id        String nullable
date          Date nullable
merchant      String nullable
amount        Numeric(14,2) nullable
currency      String nullable       # normalised to uppercase
status        String nullable       # normalised to uppercase
category      String nullable
account_id    String nullable
is_anomaly    Boolean default False
anomaly_reason Text nullable
llm_category  String nullable
llm_raw_response Text nullable
llm_failed    Boolean default False
```

### JobSummary

```python
id              UUID primary key
job_id          UUID FK → Job.id (unique)
total_spend_inr Numeric(18,2) nullable
total_spend_usd Numeric(18,2) nullable
top_merchants   JSON nullable        # list of {merchant, total_amount}
anomaly_count   Integer nullable
narrative       Text nullable
risk_level      String nullable      # low | medium | high
```

---

## API ENDPOINTS (`app/routers/jobs.py`)

### POST /jobs/upload

- Accept `multipart/form-data` with field `file` (CSV).
- Validate: must be a `.csv`, must have the required columns:
  `txn_id, date, merchant, amount, currency, status, category, account_id, notes`.
  Return 422 with a clear message if validation fails.
- Save the file temporarily (e.g. `/tmp/<job_id>.csv`).
- Create a `Job` record with `status="pending"`, `row_count_raw=<count of rows in CSV>`.
- Enqueue `process_job.delay(str(job_id), filepath)`.
- Return immediately:
  ```json
  { "job_id": "<uuid>", "status": "pending", "message": "Job enqueued successfully" }
  ```

### GET /jobs/{job_id}/status

- Return 404 if job not found.
- Response:
  ```json
  {
    "job_id": "...",
    "status": "pending|processing|completed|failed",
    "row_count_raw": 90,
    "row_count_clean": 82,
    "created_at": "2024-01-01T00:00:00",
    "completed_at": "2024-01-01T00:01:00",
    "error_message": null,
    "summary": {               // only present when status == "completed"
      "total_spend_inr": 123456.78,
      "total_spend_usd": 1234.56,
      "anomaly_count": 7,
      "risk_level": "medium",
      "narrative": "..."
    }
  }
  ```

### GET /jobs/{job_id}/results

- Return 404 if job not found.
- Return 400 with `{"detail": "Job not yet completed"}` if status != "completed".
- Response:
  ```json
  {
    "job_id": "...",
    "summary": {
      "total_spend_inr": ...,
      "total_spend_usd": ...,
      "top_merchants": [{"merchant": "Flipkart", "total_amount": 45000.0}, ...],
      "anomaly_count": 7,
      "narrative": "...",
      "risk_level": "medium"
    },
    "anomalies": [
      {
        "txn_id": "TXN2003",
        "merchant": "IRCTC",
        "amount": 193647.29,
        "currency": "INR",
        "anomaly_reason": "Amount exceeds 3x account median"
      }
    ],
    "category_breakdown": {
      "Food": 52000.0,
      "Shopping": 38000.0,
      ...
    },
    "transactions": [
      {
        "txn_id": "TXN1065",
        "date": "2024-09-04",
        "merchant": "Flipkart",
        "amount": 10882.55,
        "currency": "INR",
        "status": "SUCCESS",
        "category": "Shopping",
        "account_id": "ACC003",
        "is_anomaly": false,
        "anomaly_reason": null,
        "llm_category": null,
        "llm_failed": false
      }
    ]
  }
  ```

### GET /jobs

- List all jobs, newest first.
- Optional query param `?status=pending|processing|completed|failed`.
- Response:
  ```json
  [
    {
      "job_id": "...",
      "filename": "transactions.csv",
      "status": "completed",
      "row_count_raw": 90,
      "row_count_clean": 82,
      "created_at": "..."
    }
  ]
  ```

---

## CELERY TASK: `process_job` (`app/worker/tasks.py`)

Execute these steps **in order** inside a single Celery task. Wrap the whole task in a
try/except — if anything unhandled raises, update `job.status = "failed"` and
`job.error_message = str(e)` before re-raising.

### Step 0 — Mark processing

```python
job.status = "processing"
db.commit()
```

### Step 1 — Data Cleaning (`app/services/cleaner.py`)

Use **pandas** for all cleaning. Operate on a DataFrame loaded from the saved CSV.

1. **Date normalisation**: Parse both `DD-MM-YYYY` and `YYYY/MM/DD` into Python `date`
   objects. Also handle `YYYY-MM-DD` (already ISO). Use `pd.to_datetime` with `dayfirst`
   logic — try format `%d-%m-%Y` first, then `%Y/%m/%d`, then `%Y-%m-%d`. If a date
   cannot be parsed after all attempts, set it to `None`.
   **Important edge case**: `2024/02/29` does not exist (2024 is a leap year so it does
   exist — but handle `ValueError` on invalid dates gracefully and set to `None`).

2. **Amount cleaning**: Strip leading `$` from the `amount` column, then cast to `float`.
   Rows where amount is still non-numeric after stripping → set amount to `None`.

3. **Currency normalisation**: `currency.str.upper().str.strip()` → `INR` or `USD`.

4. **Status normalisation**: `status.str.upper().str.strip()`.

5. **Category fill**: Where `category` is blank/NaN → fill with string `"Uncategorised"`.
   (These rows are candidates for LLM classification later.)

6. **txn_id blanks**: Leave as `None`/empty — do not drop these rows.

7. **Exact duplicate removal**: Drop rows where ALL columns are identical. Keep first.

Return the cleaned DataFrame and the original raw count.

### Step 2 — Anomaly Detection (`app/services/anomaly.py`)

Operate on the cleaned DataFrame.

**Rule 1 — Statistical outlier**: For each `account_id`, compute the **median** of
`amount` (ignoring `None`). Flag a row `is_anomaly=True` with
`anomaly_reason="Amount exceeds 3x account median (median=X)"` if:
`row.amount > 3 * account_median`.

**Rule 2 — Currency mismatch**: Flag `is_anomaly=True` with
`anomaly_reason="USD transaction for domestic-only merchant"` if:
`currency == "USD"` AND `merchant` is in the domestic-only list:
`["Swiggy", "Ola", "IRCTC", "Jio Recharge", "BookMyShow", "Flipkart",
  "Amazon", "HDFC ATM", "Zomato"]`.

> Note: In the provided CSV, Zomato and Amazon have USD rows — flag them. The
> assignment says Swiggy/Ola/IRCTC explicitly but extending to all merchants that appear
> to be India-only is fine. Flag conservatively — it's better to flag than miss.

A row can have both anomaly flags — concatenate reasons with `"; "`.

### Step 3 — Bulk insert Transactions

Insert all cleaned rows into the `Transaction` table linked to `job_id`. Set `is_anomaly`
and `anomaly_reason` from Step 2. Flush so we have DB ids for later update if needed.

### Step 4 — LLM Classification (`app/services/llm.py`)

Only classify rows where `category == "Uncategorised"` (i.e., originally blank).

**Batch all uncategorised transactions into a single LLM call** (not one call per row).

Prompt format (send as user message):

```
You are a financial transaction categoriser.

Classify each transaction into exactly one of these categories:
Food, Shopping, Travel, Transport, Utilities, Cash Withdrawal, Entertainment, Other

Return ONLY a valid JSON array with no extra text, markdown, or explanation.
Each element must be an object: {"txn_index": <int>, "category": "<category>"}

Transactions to classify:
[
  {"txn_index": 0, "merchant": "Swiggy", "amount": 423.91, "currency": "INR"},
  ...
]
```

Parse the response as JSON. For each returned item, update the corresponding
`Transaction.llm_category = result["category"]` and also update
`Transaction.category = result["category"]` (so category_breakdown uses LLM result).
Store `Transaction.llm_raw_response = <raw LLM response string>`.

**Retry logic**: Wrap the Gemini API call in a retry loop — up to **3 attempts** with
**exponential backoff** (wait 2, 4, 8 seconds). If all 3 fail, set `llm_failed=True` on
all rows in that batch, log the error, and **continue** — do not fail the job.

Use `google.generativeai` SDK:
```python
import google.generativeai as genai
genai.configure(api_key=settings.GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")
response = model.generate_content(prompt)
text = response.text
```

### Step 5 — LLM Narrative Summary

Make **one separate LLM call** (with the same retry logic) with this prompt:

```
You are a financial analyst. Given the following transaction summary data, produce a JSON
object with exactly these fields:
- total_spend_inr: number (sum of all INR transaction amounts with status SUCCESS)
- total_spend_usd: number (sum of all USD transaction amounts with status SUCCESS)
- top_merchants: array of 3 objects {merchant: string, total_amount: number}, sorted desc
- anomaly_count: number
- narrative: string (2-3 sentences describing spending patterns and risks)
- risk_level: string, one of "low", "medium", "high"

Return ONLY valid JSON. No markdown, no explanation.

Data:
{json.dumps(summary_data_dict)}
```

Where `summary_data_dict` is computed from the DB (you can pre-compute totals in Python
and pass them to the LLM for narrative + risk assessment):

```python
summary_data_dict = {
    "total_transactions": len(df_clean),
    "total_spend_inr": float(df_clean[df_clean.currency=="INR"].amount.sum()),
    "total_spend_usd": float(df_clean[df_clean.currency=="USD"].amount.sum()),
    "anomaly_count": int(df_clean.is_anomaly.sum()),
    "merchants_by_spend": df_clean.groupby("merchant")["amount"].sum()
                              .sort_values(ascending=False).head(5).to_dict(),
    "category_breakdown": df_clean.groupby("category")["amount"].sum().to_dict(),
}
```

Parse the LLM response as JSON. Create a `JobSummary` record.

### Step 6 — Finalise

```python
job.status = "completed"
job.completed_at = datetime.utcnow()
job.row_count_clean = len(cleaned_rows)
db.commit()
```

---

## REQUIREMENTS.TXT

```
fastapi==0.111.0
uvicorn[standard]==0.30.1
sqlalchemy==2.0.30
psycopg2-binary==2.9.9
alembic==1.13.1
celery==5.4.0
redis==5.0.4
pandas==2.2.2
google-generativeai==0.7.2
python-multipart==0.0.9
pydantic-settings==2.3.0
tenacity==8.3.0
```

---

## CONFIGURATION (`app/config.py`)

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    redis_url: str
    gemini_api_key: str

    class Config:
        env_file = ".env"

settings = Settings()
```

---

## CELERY APP (`app/worker/celery_app.py`)

```python
from celery import Celery
from app.config import settings

celery_app = Celery(
    "worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.worker.tasks"],
)
celery_app.conf.task_track_started = True
```

---

## KNOWN DATA QUIRKS TO HANDLE (from the actual CSV)

These are real issues in the provided `transactions.csv` — make sure the pipeline handles
all of them without crashing:

1. `TXN1054` — amount is `$11325.79` (dollar prefix on an INR row).
2. `TXN1057` — amount is `$12092.64`.
3. `TXN1009` appears twice (exact duplicate — remove one).
4. `TXN1033`, `TXN1044`, `TXN1016`, `TXN1035`, `TXN1042`, `TXN1029`, `TXN1079` —
   all appear twice (exact duplicates).
5. `TXN1000` appears twice (exact duplicate).
6. Three rows have blank `txn_id` (do not drop, store with `txn_id=None`).
7. `TXN1078` has date `2024/02/29` — 2024 IS a leap year so this is valid; parse it
   correctly.
8. Currency `inr` (lowercase) appears on Ola rows — normalise to `INR`.
9. `TXN2000`, `TXN2001`, `TXN2002`, `TXN2003`, `TXN2004` — huge amounts (100k–200k
   INR), these will be flagged as outliers by the 3x median rule.
10. Several Zomato rows have `currency=USD` — flag as currency-mismatch anomaly.
11. `category` blank on 10+ rows — these go to LLM classification.
12. `TXN2003` and `TXN2004` have blank `category` AND are anomalies — handle both flags.

---

## README REQUIREMENTS

The README must include:

### Setup

```bash
cp .env.example .env
# Fill in GEMINI_API_KEY in .env
docker compose up --build
```

### Example curl requests

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

---

## CODE QUALITY REQUIREMENTS

- No hardcoded secrets anywhere — all from env vars.
- All endpoints return proper HTTP status codes (200, 400, 404, 422, 500).
- Database sessions managed via FastAPI `Depends(get_db)` generator pattern.
- Celery task creates its own DB session (do not share the FastAPI session).
- All LLM calls wrapped in try/except with retry logic (use `tenacity` or manual loop).
- Log key events: job received, pipeline steps start/complete, LLM call attempts/failures.
- `docker compose up` must reach a ready state with no errors. The `api` service must
  handle the case where Postgres is not yet ready by retrying the connection on startup
  (use `restart: on-failure` + a short sleep-and-retry in `database.py`).

---

## WHAT THE EVALUATOR WILL DO

```bash
git clone <your-repo>
cd <your-repo>
cp .env.example .env
# add GEMINI_API_KEY
docker compose up --build -d
sleep 10
curl -X POST http://localhost:8000/jobs/upload -F "file=@transactions.csv"
# note job_id
curl http://localhost:8000/jobs/<job_id>/status
# wait for completed
curl http://localhost:8000/jobs/<job_id>/results
curl http://localhost:8000/jobs
```

Every one of these must work perfectly.

---

## SUMMARY CHECKLIST BEFORE YOU FINISH

- [ ] `docker compose up` starts all 4 services with no errors
- [ ] Tables created automatically on boot (no manual migration step)
- [ ] `POST /jobs/upload` returns `job_id` immediately
- [ ] Celery worker picks up the job and runs all 5 pipeline steps
- [ ] Duplicate rows are removed (90 raw → ~76 clean after dedup)
- [ ] `$` prefix stripped from amounts correctly
- [ ] All currencies uppercased (`inr` → `INR`)
- [ ] Anomalies flagged: TXN2000–TXN2004 (stat outliers) + USD domestic merchants
- [ ] LLM classifies blank-category rows (batched, not one-call-per-row)
- [ ] LLM narrative summary stored in `JobSummary`
- [ ] LLM failures do not crash the job (graceful degradation)
- [ ] `GET /jobs/{job_id}/status` returns `summary` field when completed
- [ ] `GET /jobs/{job_id}/results` returns all 4 sections: summary, anomalies, category_breakdown, transactions
- [ ] `GET /jobs` supports `?status=` filter
- [ ] `.env.example` committed, `.env` in `.gitignore`
- [ ] `transactions.csv` committed to repo
- [ ] README with setup + curl examples
