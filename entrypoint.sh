#!/bin/bash
set -e

# Auto-apply the consolidated schema on first startup.
# Uses the system DB URL (erp_admin) to create tables, roles, and RLS policies.
# Skips if the 'tenants' table already exists (schema already applied).
if [ -n "$DB_HOST" ] && [ -f "db/V001_pg__consolidated_schema.sql" ]; then
    echo "Checking if database schema needs to be applied..."

    # Build connection string from environment
    # Get the master password from AWS Secrets Manager (managed by RDS)
    DB_URL="postgresql://${DB_USER:-erp_admin}:${DB_PASS}@${DB_HOST}:${DB_PORT:-5432}/${DB_NAME:-preduit}"

    # Check if schema is already applied by looking for the tenants table
    TABLE_EXISTS=$(python3 -c "
import sqlalchemy, os, sys
try:
    engine = sqlalchemy.create_engine('$DB_URL', connect_args={'sslmode': os.getenv('DB_SSLMODE', 'require')})
    with engine.connect() as conn:
        result = conn.execute(sqlalchemy.text(
            \"SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name='tenants')\"
        ))
        print(result.scalar())
except Exception as e:
    print(f'Error: {e}', file=sys.stderr)
    print('False')
" 2>&1)

    if [ "$TABLE_EXISTS" = "True" ]; then
        echo "Schema already applied — skipping migration."
    else
        echo "Applying consolidated schema..."
        python3 -c "
import sqlalchemy, os
engine = sqlalchemy.create_engine('$DB_URL', connect_args={'sslmode': os.getenv('DB_SSLMODE', 'require')})
with open('db/V001_pg__consolidated_schema.sql') as f:
    sql = f.read()
with engine.connect() as conn:
    conn.execute(sqlalchemy.text(sql))
    conn.commit()
print('Schema applied successfully.')
"
        echo "Database schema migration complete."
    fi
fi

exec "$@"
