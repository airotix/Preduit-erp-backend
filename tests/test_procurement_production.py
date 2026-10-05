"""Workflow regression tests using an isolated SQLite database.

Exercise real ORM/services; substitute PostgreSQL UUID/identity defaults for
SQLite and mock unrelated document rendering and GL posting. PostgreSQL row
locking/RLS and migration execution require a PostgreSQL integration check.
"""
import unittest
import uuid
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import BigInteger, DefaultClause, Integer, MetaData, create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.procurement import PurchaseOrder, PurchaseOrderLine, PoInvoice
from app.models.production import ProductionOrder, ProductionOrderLine, ProductionStage
from app.models.sales import SalesOrder
from app.models.finance import SupplierBill
from app.models.quality import Inspection
from app.modules.procurement import service as procurement
from app.modules.procurement.dto import PurchaseOrderCreate
from app.modules.production import service as production
from app.modules.production import repository as production_repo
from app.modules.production.router import create_porder
from app.modules.production.dto import ProductionOrderCreate
from app.modules.sales import service as sales
from app.modules.sales.dto import OrderCreate


class ProcurementProductionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        schema = MetaData()
        for table in Base.metadata.sorted_tables:
            copy = table.to_metadata(schema)
            for column in copy.columns:
                if isinstance(column.type, BigInteger):
                    column.type = Integer()
                if column.server_default is not None:
                    default = str(column.server_default.arg)
                    if "gen_random_uuid" in default:
                        column.server_default = DefaultClause(text("(lower(hex(randomblob(16))))"))
        schema.create_all(self.engine)
        self.db = Session(self.engine, autoflush=False)
        self.tid = uuid.uuid4()
        self.post = patch("app.modules.finance.service.post_unposted", return_value=0)
        self.post.start()
        company = patch("app.modules.procurement.repository.company_info", return_value={})
        company.start()
        self.addCleanup(company.stop)

    def tearDown(self):
        self.post.stop()
        self.db.close()
        self.engine.dispose()

    def count(self, model):
        return self.db.scalar(select(func.count()).select_from(model))

    def create_po(self):
        return procurement.create_po(self.db, tenant_id=self.tid, payload=PurchaseOrderCreate(
            supplier="Test supplier", expected="20 Oct 2026", lines=[
                {"name": "Shirt", "color": "Blue", "size": "S", "sku": "SH-B-S", "qty": 3, "price": 10},
                {"name": "Shirt", "color": "Blue", "size": "M", "sku": "SH-B-M", "qty": 5, "price": 10},
                {"name": "Jacket", "color": "Black", "size": "L", "sku": "JK-L", "qty": 2, "price": 25},
            ]))

    def test_po_creates_unstarted_work_with_variant_links_and_payable(self):
        po = self.create_po()
        work = self.db.scalar(select(ProductionOrder))
        self.assertEqual(work.purchase_order_id, po.id)
        self.assertIsNone(work.sales_order_id)
        self.assertEqual(work.qty, 10)
        self.assertEqual(work.progress, 0)
        self.assertEqual(self.count(ProductionStage), 0)
        self.assertEqual(po.status, "Pending approval")
        items = self.db.scalars(select(ProductionOrderLine).order_by(ProductionOrderLine.id)).all()
        self.assertEqual([(i.name, i.qty) for i in items], [("Shirt", 8), ("Jacket", 2)])
        variants = self.db.scalars(select(PurchaseOrderLine).order_by(PurchaseOrderLine.id)).all()
        self.assertEqual([v.size for v in variants], ["S", "M", "L"])
        self.assertEqual([v.sku for v in variants], ["SH-B-S", "SH-B-M", "JK-L"])
        self.assertEqual([v.production_line_id for v in variants], [items[0].id, items[0].id, items[1].id])
        self.assertEqual(self.count(SupplierBill), 1)
        self.assertEqual(self.count(PoInvoice), 1)

    def test_repeated_trigger_and_status_updates_do_not_duplicate(self):
        po = self.create_po()
        first = production.create_for_purchase_order(self.db, tenant_id=self.tid, purchase_order=po)
        second = production.create_for_purchase_order(self.db, tenant_id=self.tid, purchase_order=po)
        self.assertEqual(first.id, second.id)
        procurement.set_po_status(self.db, public_id=po.public_id, status="Approved")
        procurement.set_po_status(self.db, public_id=po.public_id, status="Received")
        self.assertEqual(self.count(ProductionOrder), 1)
        self.assertEqual(self.count(ProductionOrderLine), 2)
        self.assertEqual(self.count(PoInvoice), 1)

    def test_database_rejects_duplicate_source_po(self):
        po = self.create_po()
        self.db.add(ProductionOrder(tenant_id=self.tid, purchase_order_id=po.id,
                                    style="Duplicate", qty=1))
        with self.assertRaises(IntegrityError):
            self.db.flush()

    def test_cross_tenant_source_is_rejected(self):
        po = self.create_po()
        with self.assertRaises(ValueError):
            production.create_for_purchase_order(self.db, tenant_id=uuid.uuid4(), purchase_order=po)
        self.assertEqual(self.count(ProductionOrder), 1)

    def test_production_failure_rolls_back_po_and_lines(self):
        with patch("app.modules.production.repository.create_porder_line", side_effect=RuntimeError("Failure")):
            with self.assertRaises(RuntimeError):
                self.create_po()
        self.db.rollback()
        self.assertEqual(self.count(PurchaseOrder), 0)
        self.assertEqual(self.count(PurchaseOrderLine), 0)
        self.assertEqual(self.count(ProductionOrder), 0)

    def test_finance_failure_rolls_back_production_too(self):
        with patch("app.modules.finance.repository.create_bill", side_effect=RuntimeError("Failure")):
            with self.assertRaises(RuntimeError):
                self.create_po()
        self.db.rollback()
        self.assertEqual(self.count(PurchaseOrder), 0)
        self.assertEqual(self.count(ProductionOrder), 0)
        self.assertEqual(self.count(ProductionOrderLine), 0)

    def test_sales_order_no_longer_creates_production(self):
        with patch.object(sales, "build_sales_invoice_draft", return_value=None):
            sales.create_order(self.db, tenant_id=self.tid, payload=OrderCreate(
                customer="Test customer", channel="Retail", lines=[
                    {"name": "Shirt", "color": "Blue", "size": "S", "qty": 3, "price": 10}]))
        self.assertEqual(self.count(SalesOrder), 1)
        self.assertEqual(self.count(ProductionOrder), 0)

    def test_standalone_production_creation_is_blocked(self):
        with self.assertRaises(HTTPException) as caught:
            create_porder(ProductionOrderCreate(style="Shirt", factory="Test", qty=3),
                          principal=None, db=self.db)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.count(ProductionOrder), 0)

    def test_existing_sales_link_and_tna_progress_are_preserved(self):
        with patch.object(sales, "build_sales_invoice_draft", return_value=None):
            result = sales.create_order(self.db, tenant_id=self.tid, payload=OrderCreate(
                customer="Legacy customer", channel="Retail", lines=[
                    {"name": "Old style", "qty": 4, "price": 10}]))
        work = production_repo.create_porder(self.db, tenant_id=self.tid, style="Old style",
                                            factory="Factory", qty=4, sales_order_id=result["order"].id)
        item = production_repo.create_porder_line(self.db, tenant_id=self.tid,
                                                  order_id=work.id, name="Old style", qty=4)
        production.start_production(self.db, tenant_id=self.tid, public_id=work.public_id,
                                    stages=[{"name": "Sewing", "days": 2}], line_id=str(item.public_id))
        stage = self.db.scalar(select(ProductionStage).where(ProductionStage.name == "Sewing"))
        self.create_po()
        detail = production.porder_detail(self.db, public_id=work.public_id)
        self.assertIsNone(work.purchase_order_id)
        self.assertIsNone(detail["sourcePurchaseOrder"])
        self.assertEqual(detail["orderLines"][0]["item"], "Old style")
        self.assertEqual(detail["orderTotal"], "€40.00")
        self.assertTrue(detail["started"])
        self.assertEqual(stage.status, "In Progress")
        self.assertEqual(self.count(ProductionStage), 2)

    def test_source_summaries_and_links(self):
        po = self.create_po()
        work = self.db.scalar(select(ProductionOrder))
        detail = production.porder_detail(self.db, public_id=work.public_id)
        self.assertEqual(detail["sourcePurchaseOrder"]["publicId"], str(po.public_id))
        self.assertEqual([line["size"] for line in detail["orderLines"]], ["S", "M", "L"])
        self.assertEqual(detail["orderTotal"], "EUR 130.00")
        po_detail = procurement.po_detail(self.db, public_id=po.public_id)
        self.assertEqual(po_detail["productionOrder"]["publicId"], str(work.public_id))
        screen = production.porders_screen(self.db)
        self.assertEqual(len(screen["columns"]), len(screen["rows"][0]))
        self.assertNotIn("Factory", [column["label"] for column in screen["columns"]])
        self.assertIsNone(screen["action"])

    def test_explicit_tna_start_and_completion_still_open_inspection(self):
        self.create_po()
        work = self.db.scalar(select(ProductionOrder))
        item = self.db.scalar(select(ProductionOrderLine).order_by(ProductionOrderLine.id))
        production.start_production(self.db, tenant_id=self.tid, public_id=work.public_id,
                                    stages=[{"name": "Packed", "days": 1}], line_id=str(item.public_id))
        stage = self.db.scalar(select(ProductionStage).where(ProductionStage.name == "Packed"))
        self.assertEqual(stage.status, "In Progress")
        with patch("app.modules.documents.service.write_audit"):
            production.techpacks.upload_line(self.db, tenant_id=self.tid, public_id=item.public_id,
                filename="tech-pack.pdf", content_type="application/pdf", data=b"%PDF-1.4 test")
        production.stage_action(self.db, action="complete", public_id=stage.public_id)
        inspection = self.db.scalar(select(Inspection))
        self.assertEqual(inspection.order_ref, work.order_no)
        self.assertEqual(inspection.item, "Shirt")
        self.assertEqual(inspection.prod_qty, 8)


    def test_backfill_generates_three_missing_invoices_once(self):
        pos = [self.create_po() for _ in range(3)]
        for invoice in self.db.scalars(select(PoInvoice)).all():
            self.db.delete(invoice)
        self.db.flush()
        self.assertEqual(procurement.generate_missing_invoices(self.db, tenant_id=self.tid), {"generated": 3})
        self.assertEqual(self.count(PoInvoice), 3)
        self.assertEqual({inv.po_no for inv in self.db.scalars(select(PoInvoice)).all()}, {po.po_no for po in pos})
        self.assertEqual(procurement.generate_missing_invoices(self.db, tenant_id=self.tid), {"generated": 0})
        self.assertEqual(self.count(PoInvoice), 3)

    def test_invoice_generation_is_scoped_to_po_tenant_and_keeps_existing_invoice(self):
        po = self.create_po()
        invoice = self.db.scalar(select(PoInvoice))
        invoice.status = "Paid"
        self.db.flush()
        self.assertFalse(procurement.ensure_po_invoice(self.db, tenant_id=self.tid, po_id=po.id))
        self.assertEqual(invoice.status, "Paid")
        self.assertEqual(procurement.generate_missing_invoices(self.db, tenant_id=uuid.uuid4()), {"generated": 0})
        with self.assertRaises(HTTPException) as err:
            procurement.ensure_po_invoice(self.db, tenant_id=uuid.uuid4(), po_id=po.id)
        self.assertEqual(err.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
