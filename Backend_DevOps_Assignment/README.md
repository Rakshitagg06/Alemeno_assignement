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

## Notes

Gemini calls are retried three times with exponential backoff. If classification fails, the job continues and marks the affected rows with `llm_failed=true`. If narrative generation fails, the service stores a deterministic local summary so the job can still complete.
