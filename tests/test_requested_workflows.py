"""Regression coverage for item POs, safe stock movements and profile snapshots."""
import unittest
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
import test_sales_fulfillment as fixtures
from app.models.catalog import Product, ProductVariant
from app.models.core import Tenant, User
from app.models.inventory import Location, StockLevel, StockTransfer
from app.models.procurement import PurchaseOrder, PurchaseOrderLine, Supplier
from app.modules.sales import fulfillment
from app.modules.inventory import service as inventory
from app.modules.inventory.dto import StockReceiptCreate, TransferCreate
from app.modules.procurement import service as procurement
from app.modules.invoice_banking import profile_defaults
from app.modules.auth import service as auth


class RequestedWorkflowTests(unittest.TestCase):
    setUp = fixtures.SalesFulfillmentTests.setUp
    tearDown = fixtures.SalesFulfillmentTests.tearDown
    order = fixtures.SalesFulfillmentTests.order
    count = fixtures.SalesFulfillmentTests.count

    def test_each_article_has_its_selected_supplier_and_retry_does_not_duplicate(self):
        product = Product(tenant_id=self.tid, title="Jacket", status="Active")
        supplier = Supplier(tenant_id=self.tid, name="Second factory")
        self.db.add_all([product, supplier]); self.db.flush()
        variant = ProductVariant(tenant_id=self.tid, product_id=product.id, sku="J-B-S",
            color_id=self.color.id, size_id=self.size.id, price=30, supplier_price=12, currency_code="EUR")
        self.db.add(variant); self.db.flush()
        order = self.order(extra_lines=[dict(name="Jacket", color="Blue", size="S", sku=variant.sku, qty=25, price=30)])
        lines = fulfillment.lines_for(self.db, order)
        assignments = [SimpleNamespace(lineId=l.id, supplierId=s.public_id)
            for l, s in zip(lines, [self.supplier, supplier])]
        pos = fulfillment.create_shortage_pos(self.db, tenant_id=self.tid,
            public_id=order.public_id, assignments=assignments)
        self.assertEqual([p.supplier_id for p in pos], [self.supplier.id, supplier.id])
        self.assertEqual([p.sales_order_id for p in pos], [order.id, order.id])
        self.assertEqual([self.db.scalar(select(PurchaseOrderLine.qty).where(PurchaseOrderLine.po_id == p.id)) for p in pos], [40, 25])
        fulfillment.create_shortage_pos(self.db, tenant_id=self.tid, public_id=order.public_id, assignments=assignments)
        self.assertEqual(self.count(PurchaseOrder), 2)
        procurement.set_po_status(self.db, public_id=pos[0].public_id, status="Rejected", tenant_id=self.tid)
        replacement = fulfillment.create_shortage_pos(self.db, tenant_id=self.tid,
            public_id=order.public_id, assignments=assignments[:1])
        self.assertEqual(len(replacement), 1)
        self.assertNotEqual(replacement[0].id, pos[0].id)

    def test_foreign_supplier_does_not_create_a_po(self):
        order = self.order()
        foreign = Supplier(tenant_id=uuid.uuid4(), name="Foreign")
        self.db.add(foreign); self.db.flush()
        with self.assertRaises(HTTPException):
            fulfillment.create_shortage_pos(self.db, tenant_id=self.tid, public_id=order.public_id,
                assignments=[SimpleNamespace(lineId=fulfillment.lines_for(self.db, order)[0].id, supplierId=foreign.public_id)])
        self.assertEqual(self.count(PurchaseOrder), 0)

    def test_multiple_variants_of_one_article_share_one_po(self):
        from app.models.catalog import AttributeValue
        size = AttributeValue(tenant_id=self.tid, attr_type="Size", value="M", code="M")
        self.db.add(size); self.db.flush()
        variant = ProductVariant(tenant_id=self.tid, product_id=self.product.id, sku="SH-B-M",
            color_id=self.color.id, size_id=size.id, price=20, supplier_price=8, currency_code="EUR")
        self.db.add(variant); self.db.flush()
        order = self.order(extra_lines=[dict(name="Shirt", color="Blue", size="M", sku=variant.sku, qty=5, price=20)])
        pos = fulfillment.create_shortage_pos(self.db, tenant_id=self.tid, public_id=order.public_id,
            assignments=[SimpleNamespace(lineId=l.id, supplierId=self.supplier.public_id)
                for l in fulfillment.lines_for(self.db, order)])
        self.assertEqual(len(pos), 1)
        self.assertEqual(self.count(PurchaseOrderLine), 2)
        self.assertEqual([r["toPurchase"] for r in fulfillment.procurement_summary(self.db, order)], [0, 0])

    def receipt(self, lines, location="Warehouse"):
        return inventory.create_stock_receipt(self.db, tenant_id=self.tid,
            payload=StockReceiptCreate(location=location, lines=lines,
                reference="RC-TEST", reason="Goods received"))

    def test_invalid_receipt_line_does_not_partially_receive(self):
        with self.assertRaises(HTTPException):
            self.receipt([dict(name="Shirt", color="Blue", size="S", qty=10),
                dict(name="Unknown", color="Blue", size="S", qty=10)])
        self.assertEqual(self.level.on_hand, 60)
        with self.assertRaises(HTTPException):
            self.receipt([dict(name="Shirt", color="Blue", size="S", qty=10)], "Unknown")
        self.assertEqual(self.level.on_hand, 60)

    def test_transfer_respects_reservations_and_duplicate_lines(self):
        destination = Location(tenant_id=self.tid, name="Shop")
        self.db.add(destination); self.db.flush()
        self.order(50)
        def transfer(qtys):
            return inventory.create_transfer(self.db, tenant_id=self.tid, payload=TransferCreate(
                **{"from": "Warehouse", "to": "Shop", "lines": [
                    dict(name="Shirt", color="Blue", size="S", sku=self.variant.sku, qty=q) for q in qtys]}))
        with self.assertRaises(HTTPException):
            transfer([6, 5])
        self.assertEqual(self.count(StockTransfer), 0)
        self.assertEqual(self.level.on_hand, 60)
        result = transfer([6, 4])
        self.assertEqual(result.status, "Received")
        self.assertEqual((self.level.on_hand, self.level.reserved), (50, 50))
        dest = self.db.scalar(select(StockLevel).where(StockLevel.location_id == destination.id))
        self.assertEqual(dest.on_hand, 10)
        with self.assertRaises(HTTPException):
            inventory.set_transfer_status(self.db, public_id=result.public_id, status="Cancelled")
        self.assertEqual(self.level.on_hand + dest.on_hand, 60)

    def test_profile_is_committed_and_invoice_defaults_are_snapshots(self):
        actor = uuid.uuid4()
        self.db.add_all([Tenant(id=self.tid, name="Company", slug=str(self.tid), base_currency_code="USD"),
            User(tenant_id=self.tid, public_id=actor, external_id=str(actor), email="user@example.com", display_name="Preparer")])
        self.db.commit()
        @contextmanager
        def session():
            with Session(self.engine) as db:
                yield db
                db.commit()
        with patch.object(auth, "system_session", session), patch.object(auth, "_set_tenant"), patch.object(auth, "_get_enabled_modules", return_value=[]):
            auth.save_company_profile(str(self.tid), dict(companyName="Saved company", legalName="Saved legal",
                businessEmail="company@example.com", bankName="Saved bank", bankAccount="1234", bankIban="IBAN", bankSwift="SWIFT"))
            restored = auth.get_company_profile(str(self.tid))
        self.assertEqual(restored["bankAccount"], "1234")
        self.assertEqual(restored["businessEmail"], "company@example.com")
        with Session(self.engine) as db:
            data = profile_defaults(db, self.tid, {}, actor, sales=True)
            self.assertEqual(data["exporter"]["name"], "Saved legal")
            self.assertEqual(data["remit"]["bank"], "Saved bank")
            self.assertEqual(data["preparedByEmail"], "user@example.com")
            self.assertEqual(data["preparedBy"], "Preparer")
            db.get(Tenant, self.tid).bank_name = "Changed bank"
            self.assertEqual(profile_defaults(db, self.tid, data, actor, sales=True)["remit"]["bank"], "Saved bank")
            foreign = profile_defaults(db, self.tid, {}, uuid.uuid4(), sales=True)
            self.assertNotIn("preparedBy", foreign)
