# backend/db

PostgreSQL only. The local database is the `preduit-db` Docker container (`docker compose up -d db`).

- `V001_pg__consolidated_schema.sql` — full schema (converted from the old SQL Server V001–V062); runs on first container start.
- `V063_pg__*.sql` … `V082_pg__*.sql` — incremental migrations; applied in order by `entrypoint.sh` (and recorded in `schema_migrations`).
- `local/` — local-only role passwords used by docker-compose.
- `seeds_pg/` — DEV-ONLY demo data. `python db/seeds_pg/run_seeds.py` (see its docstring). Re-runnable.
- `legacy-sqlserver/` — the original T-SQL migrations, seeds, setup and fix scripts, kept for reference only. They do not run on PostgreSQL.
