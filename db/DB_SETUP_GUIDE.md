# Preduit ERP — Database setup on a new machine

This gets a fresh Windows machine from nothing to a fully populated
`Preduit-ERP` database that the backend can talk to.

The stack is **Microsoft SQL Server** (T-SQL, row-level security via
`SESSION_CONTEXT`), so **SSMS** (SQL Server Management Studio) is the right tool.
SSMS is only a *client*, though — it needs a SQL Server *engine* to connect to.
So there are two installs, then one script.

---

## Step 1 — Install the SQL Server engine

First check whether an engine is already there. Open **PowerShell** and run:

```powershell
Get-Service | Where-Object { $_.Name -like "MSSQL*" }
```

If you see a running service like `MSSQLSERVER` (default instance) or
`MSSQL$SQLEXPRESS` (Express instance), you already have an engine — skip to
Step 2. If nothing lists, install one:

- **SQL Server 2022 Developer edition** (free, full-featured — recommended for
  dev): https://www.microsoft.com/sql-server/sql-server-downloads → *Developer*
  → run the installer → choose **Basic**.
- Or **Express edition** (free, lighter) from the same page.

Note which instance you get:
- Developer "Basic" install → default instance → server name is **`localhost`**.
- Express → usually named instance → server name is **`localhost\SQLEXPRESS`**.

## Step 2 — Install SSMS and the ODBC driver

- **SSMS**: https://learn.microsoft.com/sql/ssms/download-sql-server-management-studio-ssms
  → install → open it once and connect to your server (`localhost` or
  `localhost\SQLEXPRESS`) using **Windows Authentication** to confirm it works.
- **ODBC Driver 18 for SQL Server** (the backend needs this to connect):
  https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server
- **sqlcmd** (used by the setup script) usually ships with SSMS/SQL tools. If
  the script says it's missing, run: `winget install Microsoft.Sqlcmd`

## Step 3 — Run the automated setup script

Everything in `backend/db/` is bundled into one script,
**`db_setup.ps1`**, which creates the database + users and runs all 36
migrations plus all demo-data seeds in the correct order.

In PowerShell:

```powershell
cd "<your-repo>\backend\db"

# If you connect with Windows Authentication (you're a local admin) — simplest:
.\db_setup.ps1

# If your server is a named Express instance:
.\db_setup.ps1 -Server "localhost\SQLEXPRESS"

# If you use SQL login 'sa' instead of Windows auth:
.\db_setup.ps1 -SaPassword "YourSaPassword"
```

If PowerShell blocks the script, allow it for this session first:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

The script prints each file as it runs and finishes by listing the three demo
products (Merino Crew Knit, Tailored Chino, Oxford Shirt). That confirms the
schema, the row-level-security policy, and the seed data all work.

Options:
- `-SchemaOnly` — create the schema but load no demo data.
- `-SystemPassword` / `-AppPassword` — override the default passwords (they
  **must** match `backend\.env`, see Step 4).

## Step 4 — Point the backend `.env` at it

In `backend\.env` (copy from `.env.example` if it doesn't exist), confirm:

```
SQL_SERVER=localhost            # or localhost\SQLEXPRESS
SQL_DATABASE=Preduit-ERP
SQL_APP_USER=erp_app
SQL_APP_PASSWORD=ChangeMe_App#2026        # must match the script
SQL_SYSTEM_USER=erp_system
SQL_SYSTEM_PASSWORD=ChangeMe_System#2026  # must match the script
SQL_TRUST_SERVER_CERT=yes
DEV_AUTH_BYPASS=true
DEV_TENANT_ID=33333333-3333-3333-3333-333333333333
```

The `DEV_TENANT_ID` is the fixed demo-tenant GUID the seed data uses, so the
dev-login bypass lands on the company that actually has data.

---

## What the script actually does (for reference)

1. Creates two server logins — `erp_system` (admin/provisioning, exempt from
   row-level security) and `erp_app` (the runtime app user, restricted to one
   tenant's rows).
2. Creates the **`Preduit-ERP`** database and maps both logins to users inside
   it with the right roles (`db_owner` for system; read+write for app).
3. Runs migrations **V001 → V036** in order (core schema, RLS policy, catalog,
   sales, inventory, procurement, finance, production, quality, shipments,
   admin, GL engine, bank reconciliation, AI snapshots, product images).
4. Runs the **`seed_dev_*.sql`** files in dependency order to populate every
   module with realistic demo data.
5. Verifies by selecting the demo products.

### Why not just run `setup.sql` in SSMS?

`setup.sql`, `create_users.sql` and `smoke_test_rls.sql` were written against a
database literally named **`preduit`** (lowercase), but the app and every seed
expect **`Preduit-ERP`**. That mismatch is why the repo also contains
`fix_users_in_correct_db.sql`. The script skips that whole trap by creating
`Preduit-ERP` directly with the users already in the right place — so you don't
need `setup.sql`, `create_users.sql`, or the `fix_*` scripts at all.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `sqlcmd not found` | `winget install Microsoft.Sqlcmd`, reopen PowerShell. |
| `A network-related... server was not found` | Wrong `-Server`. Try `localhost\SQLEXPRESS` or your instance name. |
| `Login failed for user 'erp_app'` (later, from the app) | `.env` password doesn't match what the script set. |
| `SSL Provider... certificate chain` | The script already passes `-C` (trust cert); for the app set `SQL_TRUST_SERVER_CERT=yes`. |
| Script blocked by execution policy | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, then rerun. |
| Want to start over | In SSMS: `DROP DATABASE [Preduit-ERP];` then rerun the script (it's safe to re-run). |
