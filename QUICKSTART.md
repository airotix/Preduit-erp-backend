# Backend Quickstart (Windows, local dev)

Goal: run the API on your machine and see real catalog data from your
`Preduit-ERP` database — no Entra login needed yet (a dev bypass is enabled in
`.env`).

## 0. Prerequisites (one-time)

1. **SQL scripts applied** — apply the migrations in **`backend/db/`** (`V001`
   through the latest `V036`) and create the `erp_app` / `erp_system` users. Now
   also run **`backend/db/seed_dev_data.sql`** in SSMS so there's demo data to see.
   (The `db/`, `Docs/` and `infra/` folders live inside `backend/`.)
2. **ODBC Driver 18 for SQL Server** — download & install from Microsoft:
   https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server
3. **Python 3.11 or newer** — https://www.python.org/downloads/
   (During install, tick **"Add python.exe to PATH"**.)

## 1. Install the backend (in a terminal)

Open **PowerShell**, then:

```powershell
cd "C:\Users\GamaZone\OneDrive\Desktop\Preduit\preduit-erp\backend"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. Check `.env`

The `.env` file is already filled in for local dev. Open it and confirm:

- `SQL_SERVER` matches your server. Default `localhost`. If you use SQL Server
  Express, set `SQL_SERVER=localhost\SQLEXPRESS`.
- `SQL_DATABASE=Preduit-ERP`
- The two passwords match what you set when creating the users.

## 3. Run it

```powershell
uvicorn app.main:app --reload
```

You should see `Uvicorn running on http://127.0.0.1:8000`.

## 4. See it work

Open your browser at **http://127.0.0.1:8000/docs** — this is an interactive
API page.

- Try **GET `/health`** → should return `{"status":"ok"}`.
- Try **GET `/api/v1/catalog/products/screen`** (click *Try it out* →
  *Execute*). You should get JSON listing the three demo products
  (Merino Crew Knit, Tailored Chino, Oxford Shirt) with variant counts and
  prices. **That JSON is the exact shape the frontend expects** — proving the
  full stack: FastAPI → SQL Server (scoped to the demo tenant by row-security)
  → the frontend's screen format.
- Try **POST `/api/v1/catalog/products`** with a body like
  `{"title":"Test Hoodie","status":"Draft"}` → creates a real row (re-run the
  GET to see it appear).

## Troubleshooting

| Message | Fix |
|---|---|
| `Can't open lib 'ODBC Driver 18...'` | Driver not installed — do step 0.2. |
| `Login failed for user 'erp_app'` | Password in `.env` doesn't match the one you set in `create_users.sql`. |
| `A network-related... server was not found` | Wrong `SQL_SERVER`. Try `localhost\SQLEXPRESS` or your instance name. |
| `SSL Provider... certificate chain` | Ensure `SQL_TRUST_SERVER_CERT=yes` in `.env`. |
| Products list is empty `[]` | Run `backend/db/seed_dev_data.sql`, and confirm `DEV_TENANT_ID` in `.env` is the demo GUID. |
| `python not recognized` | Reinstall Python with "Add to PATH", reopen the terminal. |
| `Failed building wheel for pyodbc` | Make sure `requirements.txt` pins `pyodbc==5.2.0` (has a prebuilt wheel for Python 3.13). Then re-run `pip install -r requirements.txt`. Older pyodbc tries to compile from source and fails on 3.13/3.14. |
