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

## Notes

Gemini calls are retried three times with exponential backoff. If classification fails, the job continues and marks the affected rows with `llm_failed=true`. If narrative generation fails, the service stores a deterministic local summary so the job can still complete.
