"""Verify warehouse receipt/dispatch on Docker PostgreSQL in a disposable schema.

Uses the real V074 migration and services. Credentials stay in memory.
Run: .venv/Scripts/python.exe scripts/check_warehouse_postgres.py
"""
import json
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import URL, create_engine, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root)); sys.path.insert(0, str(root / "tests"))
import app.main  # Register mapped tables and their FK targets.
from app.models.base import Base
from app.models.core import Tenant
from app.models.catalog import Product, ProductVariant, AttributeValue
from app.models.inventory import Location, StockLevel
from app.models.procurement import Supplier, GoodsReceipt
from app.modules.inventory import receiving
from test_warehouse_receiving import WarehouseReceivingTests

container = json.loads(subprocess.check_output(["docker", "inspect", "preduit-db"], text=True))[0]
config = dict(entry.split("=", 1) for entry in container["Config"]["Env"] if "=" in entry)
engine = create_engine(URL.create("postgresql+psycopg2", username=config.get("POSTGRES_USER", "erp_admin"),
    password=config["POSTGRES_PASSWORD"], host="127.0.0.1", port=5432, database=config.get("POSTGRES_DB", "preduit")))
schema = "warehouse_check_" + uuid.uuid4().hex


class PostgreSQLWarehouseTests(WarehouseReceivingTests):
    def setUp(self):
        self.engine = engine
        self.connection = engine.connect()
        self.connection.execute(text(f'SET search_path TO "{schema}", public'))
        self.db = Session(self.connection, autoflush=False)
        self.tid = uuid.uuid4()
        self.db.add(Tenant(id=self.tid, name="Warehouse check", slug=str(self.tid), base_currency_code="EUR"))
        self.db.flush()
        for target, result in [("app.modules.finance.service.post_unposted", 0),
                ("app.modules.procurement.repository.company_info", {}), ("app.core.audit.write_audit", None)]:
            mock = patch(target, return_value=result); mock.start(); self.addCleanup(mock.stop)
        self.supplier = Supplier(tenant_id=self.tid, name="Factory", lead_time="14 days")
        self.location = Location(tenant_id=self.tid, name="Warehouse")
        self.product = Product(tenant_id=self.tid, title="Shirt", status="Active")
        self.color = AttributeValue(tenant_id=self.tid, attr_type="Color", value="Blue", code="BLUE")
        self.size = AttributeValue(tenant_id=self.tid, attr_type="Size", value="S", code="S")
        self.db.add_all([self.supplier, self.location, self.product, self.color, self.size]); self.db.flush()
        self.variant = ProductVariant(tenant_id=self.tid, product_id=self.product.id, sku="SH-B-S",
            color_id=self.color.id, size_id=self.size.id, price=20, supplier_price=8, currency_code="EUR")
        self.db.add(self.variant); self.db.flush()
        self.level = StockLevel(tenant_id=self.tid, variant_id=self.variant.id, location_id=self.location.id, on_hand=60, reserved=0)
        self.db.add(self.level); self.db.flush()

    def tearDown(self):
        self.db.close(); self.connection.rollback(); self.connection.close()
        # The concurrency case commits its fixture so other connections can
        # see it. Clear only this disposable schema before the next case.
        tables = ", ".join(f'"{schema}"."{table.name}"' for table in Base.metadata.sorted_tables)
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))

    def test_concurrent_receipt_retry_posts_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        _, po, line = self.work()
        payload = self.payload(line, 15)
        tenant, po_id, level_id = self.tid, po.public_id, self.level.id
        self.db.commit(); self.connection.commit()
        barrier = Barrier(2)

        def receive():
            with Session(engine, autoflush=False) as db:
                db.execute(text(f'SET search_path TO "{schema}", public'))
                barrier.wait(timeout=10)
                grn = receiving.post(db, tenant_id=tenant, public_id=po_id, payload=payload)
                result = grn.id
                db.commit()
                return result

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: receive(), range(2)))
        self.assertEqual(results[0], results[1])
        with Session(engine) as db:
            db.execute(text(f'SET search_path TO "{schema}", public'))
            self.assertEqual(db.get(StockLevel, level_id).on_hand, 75)
            self.assertEqual(db.scalar(select(func.count()).select_from(GoodsReceipt).where(GoodsReceipt.tenant_id == tenant)), 1)

    def apply_migration(self):
        sql = (root / "db/V074_pg__physical_warehouse_receipts_and_dispatch.sql").read_text(encoding="utf-8")
        self.db.execute(text(sql.replace("BEGIN;", "", 1).rsplit("COMMIT;", 1)[0]))
        # Match the ON COMMIT DROP cleanup while retaining fixture rollback.
        self.db.execute(text("DROP TABLE pg_temp.migration_dispatch_orders"))
        self.db.expire_all()

    def test_migration_relieves_legacy_in_transit_stock_once(self):
        from app.models.shipments import Shipment
        from app.modules.shipments import service as shipping
        order = self.order(40)
        shipment = self.db.scalar(select(Shipment))
        order.status, shipment.status = "Shipped", "In transit"
        self.db.flush()
        self.apply_migration()
        self.assertEqual((self.level.on_hand, self.level.reserved), (20, 0))
        self.assertTrue(shipment.inventory_dispatched)
        self.apply_migration()
        self.assertEqual((self.level.on_hand, self.level.reserved), (20, 0))
        shipping.set_status(self.db, public_id=shipment.public_id, status="Delivered")
        self.assertEqual(self.level.on_hand, 20)

    def test_migration_preserves_stock_already_consumed_by_legacy_delivery(self):
        from app.models.shipments import Shipment
        from app.modules.sales import fulfillment
        order = self.order(40)
        shipment = self.db.scalar(select(Shipment))
        fulfillment.complete_shipment(self.db, order)
        shipment.status = "Delivered"
        self.db.flush()
        self.apply_migration()
        self.assertEqual((self.level.on_hand, self.level.reserved), (20, 0))
        self.assertTrue(shipment.inventory_dispatched)

    def test_migration_refuses_unreconciled_legacy_dispatch(self):
        from app.models.shipments import Shipment
        from app.modules.sales import fulfillment
        order = self.order(40)
        shipment = self.db.scalar(select(Shipment))
        order.status, shipment.status = "Shipped", "In transit"
        fulfillment.holds_for(self.db, order)[0].qty = 35
        self.db.flush()
        with self.assertRaises(DBAPIError):
            with self.db.begin_nested():
                self.apply_migration()
        self.assertEqual((self.level.on_hand, self.level.reserved), (60, 40))
        self.assertFalse(shipment.inventory_dispatched)


try:
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        conn.execute(text(f'SET search_path TO "{schema}", public'))
        Base.metadata.create_all(conn.execution_options(schema_translate_map={None: schema}))
        migration = (root / "db/V074_pg__physical_warehouse_receipts_and_dispatch.sql").read_text(encoding="utf-8")
        conn.execute(text(migration.replace("BEGIN;", "", 1).rsplit("COMMIT;", 1)[0]))
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PostgreSQLWarehouseTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
finally:
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()
sys.exit(not result.wasSuccessful())
