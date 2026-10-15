"""Smoke-check V070/V071 against a disposable PostgreSQL schema."""
import json
import subprocess
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text, URL


root = Path(__file__).resolve().parents[1]
container = json.loads(subprocess.check_output(["docker", "inspect", "preduit-db"], text=True))[0]
settings = dict(item.split("=", 1) for item in container["Config"]["Env"] if "=" in item)
engine = create_engine(URL.create("postgresql+psycopg2",
    username=settings.get("POSTGRES_USER", "erp_admin"), password=settings["POSTGRES_PASSWORD"],
    host="127.0.0.1", port=5432, database=settings.get("POSTGRES_DB", "preduit")))
schema = "batch_migration_check_" + uuid.uuid4().hex
tenant = uuid.uuid4()

try:
    with engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.commit()
        connection.execute(text(f'SET search_path TO "{schema}"'))
        connection.exec_driver_sql("""
            CREATE TABLE production_orders (id BIGINT PRIMARY KEY, order_no VARCHAR(32));
            CREATE TABLE production_order_lines (id BIGINT PRIMARY KEY);
            CREATE TABLE purchase_order_lines (id BIGINT PRIMARY KEY,
                production_line_id BIGINT REFERENCES production_order_lines(id),
                name VARCHAR(200), color VARCHAR(60), size VARCHAR(60), sku VARCHAR(64));
            CREATE TABLE production_batches (id BIGINT PRIMARY KEY, order_id BIGINT REFERENCES production_orders(id),
                name VARCHAR(120));
            CREATE TABLE production_batch_lines (id BIGINT PRIMARY KEY,
                batch_id BIGINT REFERENCES production_batches(id), po_line_id BIGINT REFERENCES purchase_order_lines(id));
            CREATE TABLE production_batch_stages (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, public_id UUID UNIQUE NOT NULL,
                tenant_id UUID NOT NULL, batch_id BIGINT REFERENCES production_batches(id),
                seq INT NOT NULL, name VARCHAR(80), status VARCHAR(24), worker VARCHAR(120), notes VARCHAR(400),
                start_on DATE, end_on DATE, duration_days INT, actual_start_on DATE, actual_end_on DATE,
                CONSTRAINT production_batch_stages_batch_id_seq_key UNIQUE(batch_id, seq));
            CREATE TABLE production_batch_events (id BIGINT PRIMARY KEY, tenant_id UUID NOT NULL,
                kind VARCHAR(40), payload TEXT, result TEXT, actor VARCHAR(120), created_at TIMESTAMPTZ);
            CREATE TABLE inspections (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                public_id UUID UNIQUE NOT NULL, tenant_id UUID NOT NULL, inspection_no VARCHAR(32),
                order_ref VARCHAR(40), item VARCHAR(200), stage VARCHAR(20), aql VARCHAR(10),
                defect_count INT, result VARCHAR(20), inspector VARCHAR(120), sku VARCHAR(64),
                product VARCHAR(200), batch_lot VARCHAR(60), inspection_type VARCHAR(40), prod_qty INT,
                sample_size INT, inspection_date DATE, finalized_at TIMESTAMPTZ,
                disposition_notes VARCHAR(400), is_deleted BOOLEAN);
        """)
        connection.exec_driver_sql("""
            INSERT INTO production_orders VALUES (1, 'MO-TEST');
            INSERT INTO production_order_lines VALUES (10), (11);
            INSERT INTO purchase_order_lines VALUES (20, 10, 'Shirt', 'Blue', 'M', 'S-M'),
                                                    (21, 11, 'Jacket', 'Black', 'L', 'J-L');
            INSERT INTO production_batches VALUES (30, 1, 'B1');
            INSERT INTO production_batch_lines VALUES (40, 30, 20), (41, 30, 21);
        """)
        connection.execute(text("""
            INSERT INTO production_batch_stages
                (public_id, tenant_id, batch_id, seq, name, status, duration_days)
            VALUES (:first, :tenant, 30, 1, 'Cutting', 'Completed', 3),
                   (:second, :tenant, 30, 2, 'Packed', 'Pending', 1)
        """), {"first": uuid.uuid4(), "second": uuid.uuid4(), "tenant": tenant})
        connection.execute(text("""
            INSERT INTO production_batch_events
                (id, tenant_id, kind, payload, result, actor, created_at)
            VALUES (50, :tenant, 'QC', :payload, '{}', 'Inspector', now())
        """), {"tenant": tenant, "payload": json.dumps({"scope": "40", "kind": "Final",
            "assessed": 10, "sampleSize": 2, "approved": 8, "held": 2, "rejected": 0})})
        connection.commit()

        for name in ("V070_pg__batch_style_timelines.sql", "V071_pg__batch_inspections.sql"):
            connection.exec_driver_sql((root / "db" / name).read_text(encoding="utf-8"))
            connection.commit()
        stages = connection.execute(text("""
            SELECT production_line_id, seq, status FROM production_batch_stages ORDER BY production_line_id, seq
        """)).all()
        assert stages == [(10, 1, "Completed"), (10, 2, "Pending"),
                          (11, 1, "Completed"), (11, 2, "Pending")], stages
        inspection = connection.execute(text("""
            SELECT batch_line_id, approved_qty, held_qty, result FROM inspections
        """)).one()
        assert inspection == (40, 8, 2, "Fail"), inspection
        linked = connection.execute(text("""
            SELECT result::jsonb ? 'inspectionId' FROM production_batch_events WHERE id = 50
        """)).scalar_one()
        assert linked
        print("V070/V071 migration smoke check passed")
finally:
    with engine.begin() as cleanup:
        cleanup.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()
