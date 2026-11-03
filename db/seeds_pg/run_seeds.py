"""Apply the PostgreSQL dev seeds to the local Docker database (DEV ONLY).

    python db/seeds_pg/run_seeds.py                 # seed the only tenant in the database
    python db/seeds_pg/run_seeds.py --tenant <uuid> # seed a specific tenant
    python db/seeds_pg/run_seeds.py --no-post       # skip the GL posting step

Each file runs in its own transaction as the container's superuser and is re-runnable
(inserts are guarded). Afterwards the finance documents are posted into the GL through
the running backend (POST /api/v1/finance/gl/sync) so the journals are built by the app
itself. The login for that step is DEV_EMAIL / the seeded dev owner; pass --email and
--password (or set SEED_EMAIL / SEED_PASSWORD) if yours differ.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTAINER = "preduit-db"
ORDER = [
    "seed_dev_data.sql", "seed_dev_sizes.sql", "seed_dev_catalog_detail.sql", "seed_dev_admin.sql",
    "seed_dev_inventory.sql", "seed_dev_sales.sql", "seed_dev_procurement.sql", "seed_dev_production.sql",
    "seed_dev_quality.sql", "seed_dev_shipments.sql", "seed_dev_finance.sql", "seed_dev_finance_controls.sql",
]


def psql(*args, sql=None):
    cmd = ["docker", "exec", "-i", CONTAINER, "psql", "-U", "erp_admin", "-d", "preduit", "-X", *args]
    done = subprocess.run(cmd, input=sql, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if done.returncode:
        raise RuntimeError((done.stderr or done.stdout).strip())
    return done.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant")
    ap.add_argument("--no-post", action="store_true")
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--email", default=os.getenv("SEED_EMAIL", "dev@preduit.local"))
    ap.add_argument("--password", default=os.getenv("SEED_PASSWORD"))
    args = ap.parse_args()

    tenant = args.tenant
    if not tenant:
        rows = psql("-At", "-c", "SELECT id FROM tenants WHERE NOT is_deleted ORDER BY created_at").split()
        if len(rows) != 1:
            sys.exit(f"Found {len(rows)} tenants; pass --tenant <uuid>.")
        tenant = rows[0]
    print(f"Seeding tenant {tenant}")

    for name in ORDER:
        sql = (HERE / name).read_text(encoding="utf-8")
        psql("-q", "-1", "-v", "ON_ERROR_STOP=1", "-v", f"tenant={tenant}", sql=sql)
        print(f"  ok  {name}")

    if args.no_post:
        return
    if not args.password:
        print("Skipping GL posting: no password given (--password or SEED_PASSWORD).")
        return
    import httpx
    with httpx.Client(base_url=args.api, timeout=60) as client:
        login = client.post("/api/v1/auth/login", json={"email": args.email, "password": args.password})
        login.raise_for_status()
        body = login.json()
        client.headers["Authorization"] = "Bearer " + (body.get("accessToken") or body.get("access_token"))
        posted = client.post("/api/v1/finance/gl/sync")
        posted.raise_for_status()
        print("  ok  GL posting:", json.dumps(posted.json()))


if __name__ == "__main__":
    main()
