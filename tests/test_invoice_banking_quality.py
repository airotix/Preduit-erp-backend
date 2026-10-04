"""Banking on existing invoices and QC defect/size workflows."""
import json
import unittest
import uuid
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select

import test_procurement_production as fixtures
from app.models.procurement import Supplier, PoInvoice
from app.models.sales import SalesInvoice
from app.models.production import ProductionOrder, ProductionOrderLine
from app.models.quality import Inspection, InspectionDefect, DefectType
# Register stock tables before the shared fixture copies SQLAlchemy metadata,
# including when this test module is run independently of the full suite.
from app.models.inventory import Location, StockLevel, OrderStockReservation
from app.modules.procurement import service as procurement
from app.modules.sales import service as sales
from app.modules.sales.dto import OrderCreate
from app.modules.quality import service as quality
from app.modules.quality.dto import DefectIn


class InvoiceBankingQualityTests(unittest.TestCase):
    setUp = fixtures.ProcurementProductionTests.setUp
    tearDown = fixtures.ProcurementProductionTests.tearDown

    def test_saved_sales_invoice_fills_missing_bank_fields_and_preserves_override(self):
        company = dict(name="Brand", bank_name="Bank", bank_account="123", bank_iban="IBAN", bank_swift="SWIFT")
        result = sales.create_sales_invoice_doc(self.db, tenant_id=self.tid, data={
            "buyer": {"name": "Customer"}, "remit": {"bank": " Custom bank ", "account": " "}})
        inv = self.db.scalar(select(SalesInvoice))
        original = inv.data
        with patch.object(sales.repo, "company_info", return_value=company):
            loaded = sales.get_sales_invoice_doc(self.db, public_id=inv.public_id)["data"]
        self.assertEqual(loaded["remit"], dict(title="Brand", bank=" Custom bank ", account="123", iban="IBAN", swift="SWIFT"))
        self.assertEqual(inv.data, original)  # viewing doesn't rewrite the saved document
        sales.update_sales_invoice_doc(self.db, public_id=inv.public_id, data=loaded)
        self.assertEqual(json.loads(inv.data)["remit"]["account"], "123")

    def test_basic_sales_invoice_detail_contains_company_bank_fields(self):
        with patch.object(sales, "build_sales_invoice_draft", return_value=None):
            sales.create_order(self.db, tenant_id=self.tid, payload=OrderCreate(customer="Buyer", channel="Retail",
                lines=[dict(name="Article", qty=1, price=10)]))
        from app.models.sales import Invoice
        inv = self.db.scalar(select(Invoice))
        with patch.object(sales.repo, "company_info", return_value=dict(name="Brand", bank_name="Bank", bank_account="456")):
            detail = sales.invoice_detail(self.db, public_id=inv.public_id)
        self.assertEqual(dict((row["k"], row["v"]) for row in detail["doc"]["bankDetails"])["Account"], "456")

    def test_saved_procurement_invoice_uses_linked_supplier_and_preserves_override(self):
        po = fixtures.ProcurementProductionTests.create_po(self)
        supplier = self.db.get(Supplier, po.supplier_id)
        supplier.bank_name, supplier.bank_account_number = "Supplier bank", "987"
        supplier.bank_account_title, supplier.bank_iban, supplier.bank_swift = "Factory", "SUP-IBAN", "SUP-SWIFT"
        self.db.flush()
        inv = self.db.scalar(select(PoInvoice))
        data = json.loads(inv.data)
        data["bank"] = {"bank": "", "account": "Manual account"}
        inv.data = json.dumps(data)
        self.db.flush()
        loaded = procurement.get_invoice(self.db, public_id=inv.public_id)["data"]["bank"]
        self.assertEqual(loaded["bank"], "Supplier bank")
        self.assertEqual(loaded["account"], "Manual account")
        self.assertEqual(loaded["iban"], "SUP-IBAN")
        self.assertEqual(loaded["swift"], "SUP-SWIFT")
        self.assertEqual(loaded["beneficiary"], "Factory")

    def inspection(self, result="In Progress"):
        ins = Inspection(tenant_id=self.tid, order_ref="TEST", result=result)
        self.db.add(ins)
        self.db.flush()
        return ins

    def test_free_text_defect_saves_and_updates_count(self):
        ins = self.inspection()
        defect = quality.add_defect(self.db, tenant_id=self.tid, public_id=str(ins.public_id),
            payload=DefectIn(defectName=" Loose seam ", qtyAffected=3, description="Sleeve"))
        self.assertEqual(defect.defect_name, "Loose seam")
        self.assertEqual(ins.defect_count, 3)
        self.assertIsNotNone(defect.public_id)
        quality.delete_defect(self.db, defect_public_id=defect.public_id)
        self.assertEqual(ins.defect_count, 0)

    def test_saved_defect_type_accepts_public_uuid_string(self):
        ins = self.inspection()
        kind = DefectType(tenant_id=self.tid, name="Hole", category="Fabric", severity="Major")
        self.db.add(kind)
        self.db.flush()
        defect = quality.add_defect(self.db, tenant_id=self.tid, public_id=str(ins.public_id),
            payload=DefectIn(defectTypeId=str(kind.public_id), qtyAffected=2))
        self.assertEqual(defect.defect_type_id, kind.id)
        self.assertEqual(defect.severity, "Major")
        self.assertEqual(ins.defect_count, 2)

    def test_new_defect_is_created_and_logged_with_details(self):
        ins = self.inspection()
        defect = quality.add_defect(self.db, tenant_id=self.tid, public_id=str(ins.public_id),
            payload=DefectIn(newDefect={"name": " Loose seam ", "category": " Stitching ", "severity": "Major"},
                qtyAffected=3, description="Sleeve seam"))
        kind = self.db.get(DefectType, defect.defect_type_id)
        self.assertEqual((kind.name, kind.category, kind.severity), ("Loose seam", "Stitching", "Major"))
        self.assertEqual((defect.defect_name, defect.category, defect.severity), (kind.name, kind.category, kind.severity))
        self.assertEqual(defect.description, "Sleeve seam")
        self.assertEqual(ins.defect_count, 3)
        self.assertTrue(any(option["name"] == "Loose seam" for option in quality.defect_options(self.db)))

    def test_new_defect_name_reuses_existing_type_in_same_tenant(self):
        ins = self.inspection()
        kind = DefectType(tenant_id=self.tid, name="Hole", category="Fabric", severity="Critical")
        self.db.add(kind)
        self.db.flush()
        defect = quality.add_defect(self.db, tenant_id=self.tid, public_id=str(ins.public_id),
            payload=DefectIn(newDefect={"name": " hole ", "category": "Other", "severity": "Minor"}))
        self.assertEqual(defect.defect_type_id, kind.id)
        self.assertEqual(defect.severity, "Critical")
        self.assertEqual(len(self.db.scalars(select(DefectType)).all()), 1)

    def test_new_defect_rejects_pending_inspection_and_blank_category(self):
        ins = self.inspection()
        ins.result = "Pending"
        with self.assertRaises(HTTPException):
            quality.add_defect(self.db, tenant_id=self.tid, public_id=str(ins.public_id),
                payload=DefectIn(newDefect={"name": "Hole", "category": "Fabric", "severity": "Major"}))
        ins.result = "In Progress"
        with self.assertRaises(HTTPException):
            quality.add_defect(self.db, tenant_id=self.tid, public_id=str(ins.public_id),
                payload=DefectIn(newDefect={"name": "Hole", "category": " ", "severity": "Major"}))
        self.assertEqual(len(self.db.scalars(select(DefectType)).all()), 0)

    def test_inspection_tabs_show_latest_run_and_history_preserves_old_details(self):
        parent = self.inspection("Fail")
        parent.item = "Shirt"
        self.db.flush()
        child = quality.reopen_failed(self.db, tenant_id=self.tid, public_id=parent.public_id,
            mode="inspection")["inspection"]
        current = quality.inspection_detail(self.db, public_id=child.public_id)
        self.assertEqual([it["publicId"] for it in current["inspection"]["items"]], [str(child.public_id)])
        self.assertEqual(current["tabs"], ["Shirt"])
        self.assertEqual(len(current["inspection"]["items"][0]["history"]), 2)
        historical = quality.inspection_detail(self.db, public_id=parent.public_id)
        self.assertTrue(historical["inspection"]["items"][0]["historical"])
        self.assertEqual(historical["inspection"]["items"][0]["header"]["result"], "Fail")
        self.assertEqual(historical["inspection"]["items"][1]["publicId"], str(child.public_id))

    def test_recovery_saves_corrective_details_once_and_validates_date_before_creation(self):
        import datetime
        parent = self.inspection("Fail")
        with self.assertRaises(HTTPException):
            quality.reopen_failed(self.db, tenant_id=self.tid, public_id=parent.public_id,
                mode="inspection", due_date="invalid")
        self.assertEqual(len(self.db.scalars(select(Inspection)).all()), 1)
        result = quality.reopen_failed(self.db, tenant_id=self.tid, public_id=parent.public_id,
            mode="inspection", notes=" Check stitching ", assigned_to=" QC team ", due_date="2026-10-10")
        self.assertEqual(parent.disposition_notes, "Check stitching")
        self.assertEqual(parent.assigned_to, "QC team")
        self.assertEqual(parent.due_date, datetime.date(2026, 10, 10))
        repeated = quality.reopen_failed(self.db, tenant_id=self.tid, public_id=parent.public_id,
            mode="inspection", notes="Changed")
        self.assertEqual(result["inspection"].id, repeated["inspection"].id)
        self.assertEqual(parent.disposition_notes, "Check stitching")

    def test_defect_rejects_empty_completed_and_foreign_type(self):
        ins = self.inspection()
        with self.assertRaises(HTTPException) as err:
            quality.add_defect(self.db, tenant_id=self.tid, public_id=str(ins.public_id), payload=DefectIn())
        self.assertEqual(err.exception.status_code, 422)
        kind = DefectType(tenant_id=uuid.uuid4(), name="Other", category="Fabric", severity="Major")
        self.db.add(kind)
        self.db.flush()
        with self.assertRaises(HTTPException) as err:
            quality.add_defect(self.db, tenant_id=self.tid, public_id=str(ins.public_id), payload=DefectIn(defectTypeId=str(kind.public_id)))
        self.assertEqual(err.exception.status_code, 404)
        ins.result = "Pass"
        self.db.flush()
        with self.assertRaises(HTTPException) as err:
            quality.add_defect(self.db, tenant_id=self.tid, public_id=str(ins.public_id), payload=DefectIn(defectName="Hole"))
        self.assertEqual(err.exception.status_code, 409)
        self.assertEqual(len(self.db.scalars(select(InspectionDefect)).all()), 0)

    def test_qc_size_ratio_uses_only_inspected_article_po_quantities(self):
        po = fixtures.ProcurementProductionTests.create_po(self)
        work = self.db.scalar(select(ProductionOrder).where(ProductionOrder.purchase_order_id == po.id))
        ins = Inspection(tenant_id=self.tid, order_ref=work.order_no, item="Shirt", result="Pending")
        self.db.add(ins)
        self.db.flush()
        breakdown = quality.size_breakdown(self.db, ins)
        self.assertEqual(breakdown["sizes"], ["S", "M"])
        self.assertEqual(breakdown["totals"], [3, 5])
        self.assertEqual(breakdown["total"], 8)
        self.assertEqual(breakdown["ratio"], [3, 5])
        self.assertEqual(breakdown["rows"][0]["color"], "Blue")
        self.assertEqual(quality.inspection_detail(self.db, public_id=ins.public_id)["inspection"]["items"][0]["sizeBreakdown"], breakdown)

    def test_qc_size_ratio_aggregates_colors_and_reduces_ratio(self):
        work = ProductionOrder(tenant_id=self.tid, order_no="SIZE", style="Shirt", qty=120)
        self.db.add(work)
        self.db.flush()
        for color, size, qty in [("Blue", "S", 20), ("Blue", "M", 40), ("Red", "S", 20), ("Red", "M", 40)]:
            self.db.add(ProductionOrderLine(tenant_id=self.tid, order_id=work.id, name="Shirt", color=color, size=size, qty=qty))
        self.db.flush()
        ins = Inspection(tenant_id=self.tid, order_ref="SIZE", item="Shirt", result="Pending")
        self.db.add(ins)
        self.db.flush()
        breakdown = quality.size_breakdown(self.db, ins)
        self.assertEqual(breakdown["totals"], [40, 80])
        self.assertEqual(breakdown["ratio"], [1, 2])
        self.assertEqual(len(breakdown["rows"]), 2)
        ins.tenant_id = uuid.uuid4()
        self.assertEqual(quality.size_breakdown(self.db, ins)["total"], 0)
