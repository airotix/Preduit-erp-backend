"""Run batch workflow tests on local Docker PostgreSQL in a disposable schema.

Credentials stay in memory. Does not edit application configuration or user data.
Run: python scripts/check_batch_postgres.py
"""
import json
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import URL, create_engine, text
from sqlalchemy.orm import Session

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root)); sys.path.insert(0, str(root / "tests"))
import app.main  # register all FK targets
from app.models.base import Base
from app.models.core import Tenant
from app.models.catalog import Product, ProductVariant, AttributeValue
from app.models.inventory import Location, StockLevel
from app.models.procurement import Supplier
from test_batch_execution import BatchExecutionTests


def credentials():
    data = json.loads(subprocess.check_output(["docker", "inspect", "preduit-db"], text=True))[0]
    values = dict(entry.split("=", 1) for entry in data["Config"]["Env"] if "=" in entry)
    return values


config = credentials()
schema = "batch_check_" + uuid.uuid4().hex
engine = create_engine(URL.create("postgresql+psycopg2", username=config.get("POSTGRES_USER", "erp_admin"),
    password=config["POSTGRES_PASSWORD"], host="127.0.0.1", port=5432, database=config.get("POSTGRES_DB", "preduit")))
with engine.begin() as conn:
    conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    conn.execute(text(f'SET search_path TO "{schema}", public'))
    Base.metadata.create_all(conn.execution_options(schema_translate_map={None: schema}))


class PostgreSQLBatchTests(BatchExecutionTests):
    def setUp(self):
        self.engine = engine
        self.connection = engine.connect()
        self.connection.execute(text(f'SET search_path TO "{schema}", public'))
        self.db = Session(self.connection, autoflush=False)
        self.tid = uuid.uuid4()
        self.connection.execute(text("SELECT set_config('app.tenant_id', :tid, false)"), {"tid": str(self.tid)})
        self.db.add(Tenant(id=self.tid, name="Batch test", slug=str(self.tid), base_currency_code="EUR"))
        self.db.flush()
        self.post = patch("app.modules.finance.service.post_unposted", return_value=0); self.post.start()
        self.company = patch("app.modules.procurement.repository.company_info", return_value={}); self.company.start()
        self.supplier = Supplier(tenant_id=self.tid, name="Factory", lead_time="14 days")
        self.location = Location(tenant_id=self.tid, name="Warehouse")
        self.product = Product(tenant_id=self.tid, title="Shirt")
        self.color = AttributeValue(tenant_id=self.tid, attr_type="Color", value="Blue", code="BLUE")
        self.size = AttributeValue(tenant_id=self.tid, attr_type="Size", value="S", code="S")
        self.db.add_all([self.supplier, self.location, self.product, self.color, self.size]); self.db.flush()
        self.variant = ProductVariant(tenant_id=self.tid, product_id=self.product.id, sku="SH-B-S", color_id=self.color.id,
            size_id=self.size.id, price=20, supplier_price=8, currency_code="EUR")
        self.db.add(self.variant); self.db.flush()
        self.level = StockLevel(tenant_id=self.tid, variant_id=self.variant.id, location_id=self.location.id, on_hand=0, reserved=0)
        self.db.add(self.level); self.db.flush()
        self.so = self.order(1000)
        from app.modules.sales import fulfillment
        from app.models.production import ProductionOrder
        from app.models.procurement import PurchaseOrderLine
        from sqlalchemy import select
        self.po = fulfillment.create_shortage_po(self.db, tenant_id=self.tid, public_id=self.so.public_id, supplier_id=self.supplier.public_id)
        self.work = self.db.scalar(select(ProductionOrder)); self.source = self.db.scalar(select(PurchaseOrderLine))

    def tearDown(self):
        self.post.stop(); self.company.stop()
        self.db.close(); self.connection.rollback(); self.connection.close()

    def test_z_competing_shipments_cannot_allocate_the_same_pieces(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from fastapi import HTTPException
        from app.modules.batches import service, dto
        batch, line = self.plan(); self.produce(batch, line); self.qc(batch, line, 200); self.receive(batch, line, 200)
        lot = self.lot(line)
        tid, order_id, lot_id = self.tid, self.so.public_id, lot.id
        self.db.commit(); self.connection.commit()
        barrier = Barrier(2)
        def allocate():
            with Session(engine, autoflush=False) as db:
                db.execute(text(f'SET search_path TO "{schema}", public'))
                barrier.wait(timeout=10)
                try:
                    service.create_shipment(db, tid, "logistics", order_id, "sales", dto.CreateShipment(
                        requestId=uuid.uuid4(), reason="Concurrent allocation", carrier="Carrier", destination="Customer",
                        allocations=[dict(lotId=lot_id, qty=150)]))
                    db.commit(); return 201
                except HTTPException as exc:
                    db.rollback(); return exc.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: allocate(), range(2)))
        self.assertEqual(sorted(results), [201, 409])
        self.db.refresh(lot)
        self.assertEqual(lot.allocated, 150)

    def test_row_security_on_installed_batch_tables(self):
        from app.models.batches import ProductionBatch
        from app.models.production import ProductionOrder
        from app.models.procurement import PurchaseOrder
        from sqlalchemy import select
        own, foreign = uuid.uuid4(), uuid.uuid4()
        with Session(engine) as db:
            db.execute(text("SET LOCAL search_path TO public"))
            for tid in (own, foreign):
                db.add(Tenant(id=tid, name="RLS rollback check", slug=str(tid), base_currency_code="EUR"))
            db.flush()
            ids = []
            for tid in (own, foreign):
                po = PurchaseOrder(tenant_id=tid, supplier_name="RLS check")
                db.add(po); db.flush()
                work = ProductionOrder(tenant_id=tid, purchase_order_id=po.id, style="RLS check", qty=1)
                db.add(work); db.flush()
                batch = ProductionBatch(tenant_id=tid, order_id=work.id, name="RLS check")
                db.add(batch); db.flush(); ids.append(batch.id)
            db.execute(text("SET LOCAL ROLE erp_app"))
            db.execute(text("SET LOCAL app.rls_bypass = 'false'"))
            db.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(own)})
            visible = db.scalars(select(ProductionBatch.id).where(ProductionBatch.id.in_(ids))).all()
            self.assertEqual(visible, ids[:1])
            db.rollback()  # The real database retains no test tenants or orders.


try:
    result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(PostgreSQLBatchTests))
finally:
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()
sys.exit(0 if result.wasSuccessful() else 1)
