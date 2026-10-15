"""Focused checks for catalog retirement safeguards and retained history."""
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.catalog import AttributeValue, Category, Product, ProductVariant
from app.models.inventory import Location, OrderStockReservation, ReorderAlert, StockLevel, StockTransfer, StockTransferLine
from app.models.sales import Invoice, SalesInvoice, SalesOrder, SalesOrderLine
from app.models.procurement import GoodsReceipt, PoInvoice, PurchaseOrder, PurchaseOrderLine
from app.models.finance import SupplierBill
from app.models.production import ProductionOrder, ProductionOrderLine, ProductionStage
from app.models.quality import Inspection
import test_procurement_production as fixtures
from app.modules.catalog import retirement
from app.modules.catalog.dto import StockDisposition
from app.modules.procurement import service as procurement_service
from app.modules.sales import service as sales_service


class CatalogRetirementTests(unittest.TestCase):
    def setUp(self):
        fixtures.ProcurementProductionTests.setUp(self)
        self.tid = uuid4()
        self.pid = uuid4()
        self.product = Product(id=1, public_id=self.pid, tenant_id=self.tid,
                               title="Jacket", status="Active", is_deleted=False)
        self.variant = ProductVariant(id=1, public_id=uuid4(), tenant_id=self.tid,
                                      product_id=1, sku="JKT-1", qty_on_hand=0,
                                      price=10, currency_code="EUR", status="Active")
        self.stock = StockLevel(id=1, public_id=uuid4(), tenant_id=self.tid,
                                variant_id=1, location_id=1, on_hand=0, reserved=0)
        self.location = Location(id=1, public_id=uuid4(), tenant_id=self.tid,
                                 name="Main warehouse", is_deleted=False)
        self.db.add_all([self.product, self.variant, self.location, self.stock])
        self.db.flush()

    tearDown = fixtures.ProcurementProductionTests.tearDown

    def _retire(self):
        with patch.object(retirement, "write_audit"):
            return retirement.retire(self.db, tenant_id=self.tid, public_id=str(self.pid))

    def test_stock_blocks_retirement(self):
        self.stock.on_hand = 2
        self.db.flush()
        self.assertFalse(retirement.preview(self.db, tenant_id=self.tid, public_id=str(self.pid))["canDelete"])
        with self.assertRaises(HTTPException) as raised:
            self._retire()
        self.assertEqual(raised.exception.status_code, 409)
        self.assertFalse(self.product.is_deleted)

    def test_inline_disposition_records_and_reconciles_quantity(self):
        self.stock.on_hand = 5
        self.variant.qty_on_hand = 5
        self.db.flush()
        payload = StockDisposition(stockLevelId=self.stock.public_id, quantity=3,
                                   kind="Write off", reason="Damaged units")
        with patch.object(retirement, "write_audit") as audit:
            retirement.dispose_stock(self.db, tenant_id=self.tid, public_id=str(self.pid), payload=payload)
        self.assertEqual(self.stock.on_hand, 2)
        self.assertEqual(self.variant.qty_on_hand, 2)
        self.assertEqual(audit.call_count, 2)
        with patch.object(retirement, "write_audit"):
            retirement.dispose_stock(self.db, tenant_id=self.tid, public_id=str(self.pid),
                                     payload=StockDisposition(stockLevelId=self.stock.public_id, quantity=2,
                                                              kind="Return to supplier", reason="Rejected goods",
                                                              reference="RTV-1"))
        self.assertTrue(retirement.preview(self.db, tenant_id=self.tid, public_id=str(self.pid))["canDelete"])

    def test_reserved_stock_cannot_be_disposed(self):
        self.stock.on_hand = 5
        self.stock.reserved = 2
        self.db.flush()
        with self.assertRaises(HTTPException) as raised:
            retirement.dispose_stock(self.db, tenant_id=self.tid, public_id=str(self.pid),
                                     payload=StockDisposition(stockLevelId=self.stock.public_id, quantity=3,
                                                              kind="Write off", reason="Damaged units"))
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.stock.on_hand, 5)

    def test_legacy_balance_can_be_reconciled_only_after_physical_stock_clears(self):
        self.variant.qty_on_hand = 7
        self.db.flush()
        result = retirement.preview(self.db, tenant_id=self.tid, public_id=str(self.pid))
        self.assertTrue(result["legacyMismatch"])
        self.assertFalse(result["canDelete"])
        with patch.object(retirement, "write_audit"):
            retirement.reconcile_stock_cache(self.db, tenant_id=self.tid,
                                             public_id=str(self.pid), reason="Verified empty warehouse")
        self.assertTrue(retirement.preview(self.db, tenant_id=self.tid, public_id=str(self.pid))["canDelete"])

    def test_active_order_and_po_block_retirement_even_without_stock(self):
        order = SalesOrder(id=1, public_id=uuid4(), tenant_id=self.tid,
                           customer_name="Buyer", status="New", is_deleted=False)
        line = SalesOrderLine(id=1, tenant_id=self.tid, order_id=1, variant_id=1,
                              name="Jacket", sku="JKT-1")
        po = PurchaseOrder(id=1, public_id=uuid4(), tenant_id=self.tid,
                           supplier_name="Maker", status="Approved", is_deleted=False)
        po_line = PurchaseOrderLine(id=1, public_id=uuid4(), tenant_id=self.tid,
                                    po_id=1, sales_order_line_id=1, name="Jacket", sku=None)
        self.db.add_all([order, line, po, po_line])
        self.db.flush()
        result = retirement.preview(self.db, tenant_id=self.tid, public_id=str(self.pid))
        self.assertFalse(result["canDelete"])
        self.assertEqual(len(result["orders"]), 1)
        self.assertEqual(len(result["purchaseOrders"]), 1)
        order.status = "Inactive"
        po.status = "Inactive"
        self.db.flush()
        self.assertTrue(retirement.preview(self.db, tenant_id=self.tid, public_id=str(self.pid))["canDelete"])

    def test_order_inactivation_waits_for_linked_po(self):
        order = SalesOrder(id=1, public_id=uuid4(), tenant_id=self.tid,
                           customer_name="Buyer", status="New", is_deleted=False)
        po = PurchaseOrder(id=1, public_id=uuid4(), tenant_id=self.tid,
                           sales_order_id=1, supplier_name="Maker", status="Approved",
                           is_deleted=False)
        self.db.add_all([order, po])
        self.db.flush()
        with self.assertRaises(HTTPException) as raised:
            sales_service.set_order_status(self.db, public_id=str(order.public_id),
                                           status="Inactive", reason="Whole order withdrawn")
        self.assertEqual(raised.exception.status_code, 409)

    def test_preview_explains_paid_order_and_received_po(self):
        order = SalesOrder(id=1, public_id=uuid4(), tenant_id=self.tid,
                           order_no="SO-1", customer_name="Buyer", status="New", is_deleted=False)
        line = SalesOrderLine(id=1, tenant_id=self.tid, order_id=1, variant_id=1,
                              name="Jacket", sku="JKT-1")
        paid = Invoice(id=1, public_id=uuid4(), tenant_id=self.tid,
                       order_no="SO-1", customer_name="Buyer", status="Paid", is_deleted=False)
        po = PurchaseOrder(id=1, public_id=uuid4(), tenant_id=self.tid,
                           po_no="PO-1", supplier_name="Maker", status="Approved",
                           inventory_received=False, is_deleted=False)
        po_line = PurchaseOrderLine(id=1, public_id=uuid4(), tenant_id=self.tid,
                                    po_id=1, name="Jacket", sku="JKT-1")
        receipt = GoodsReceipt(id=1, public_id=uuid4(), tenant_id=self.tid,
                               po_ref="PO-1", supplier_name="Maker", received_count=1,
                               is_deleted=False)
        self.db.add_all([order, line, paid, po, po_line, receipt])
        self.db.flush()
        result = retirement.preview(self.db, tenant_id=self.tid, public_id=str(self.pid))
        self.assertFalse(result["orders"][0]["canMarkInactive"])
        self.assertIn("Paid invoice", result["orders"][0]["blockReason"])
        self.assertFalse(result["purchaseOrders"][0]["canMarkInactive"])
        self.assertIn("Received goods", result["purchaseOrders"][0]["blockReason"])

    def test_retirement_keeps_history_and_stops_open_work(self):
        work = ProductionOrder(id=1, public_id=uuid4(), tenant_id=self.tid,
                               style="Jacket", stage="Cutting", is_deleted=False)
        line = ProductionOrderLine(id=1, public_id=uuid4(), tenant_id=self.tid,
                                   order_id=1, name="Jacket", sku="JKT-1", is_deleted=False)
        stage = ProductionStage(id=1, public_id=uuid4(), tenant_id=self.tid,
                                order_id=1, line_id=1, seq=1, name="Cutting",
                                status="In Progress", is_deleted=False)
        other_line = ProductionOrderLine(id=2, public_id=uuid4(), tenant_id=self.tid,
                                         order_id=1, name="Other style", sku="OTHER-1",
                                         is_deleted=False)
        other_stage = ProductionStage(id=2, public_id=uuid4(), tenant_id=self.tid,
                                      order_id=1, line_id=2, seq=1, name="Cutting",
                                      status="In Progress", is_deleted=False)
        inspection = Inspection(id=1, public_id=uuid4(), tenant_id=self.tid,
                                sku="JKT-1", stage="Final", result="Pending", is_deleted=False)
        alert = ReorderAlert(id=1, public_id=uuid4(), tenant_id=self.tid,
                             variant_id=1, sku="JKT-1", is_deleted=False)
        self.db.add_all([work, line, stage, other_line, other_stage, inspection, alert])
        self.db.flush()
        preview = retirement.preview(self.db, tenant_id=self.tid, public_id=str(self.pid))
        self.assertEqual((preview["openStages"], preview["openInspections"]), (1, 1))
        self.assertTrue(preview["canDelete"])
        self._retire()
        self.assertTrue(self.product.is_deleted)
        self.assertEqual(self.variant.status, "Archived")
        self.assertIsNone(self.db.get(StockLevel, 1))
        self.assertTrue(alert.is_deleted)
        self.assertEqual(stage.status, "Cancelled")
        self.assertEqual(other_stage.status, "In Progress")
        self.assertEqual(inspection.result, "Cancelled")
        self.assertFalse(line.is_deleted)
        self.assertFalse(inspection.is_deleted)

    def test_inactive_po_stops_its_production_and_inspection(self):
        source_order = SalesOrder(id=1, public_id=uuid4(), tenant_id=self.tid,
                                  customer_name="Buyer", status="Awaiting stock",
                                  fulfillment_tracked=True, is_deleted=False)
        po = PurchaseOrder(id=1, public_id=uuid4(), tenant_id=self.tid,
                           po_no="PO-1", sales_order_id=1, supplier_name="Maker", status="Approved",
                           inventory_received=False, is_deleted=False)
        work = ProductionOrder(id=1, public_id=uuid4(), tenant_id=self.tid,
                               purchase_order_id=1, order_no="PROD-1", style="Jacket",
                               stage="Cutting", is_deleted=False)
        stage = ProductionStage(id=1, public_id=uuid4(), tenant_id=self.tid,
                                order_id=1, seq=1, name="Cutting", status="In Progress",
                                is_deleted=False)
        inspection = Inspection(id=1, public_id=uuid4(), tenant_id=self.tid,
                                order_ref="PROD-1", stage="Final", result="Pending",
                                is_deleted=False)
        self.db.add_all([source_order, po, work, stage, inspection])
        self.db.flush()

        def set_status(session, *, public_id, status):
            po.status = status
            session.flush()
            return po

        with patch.object(procurement_service.repo, "get_po_detail", return_value={"po": po}), \
             patch.object(procurement_service.repo, "set_po_status", side_effect=set_status), \
             patch("app.modules.catalog.commitments.write_audit"):
            procurement_service.set_po_status(self.db, public_id=str(po.public_id),
                                              status="Inactive", reason="Supplier cancelled")
        self.assertEqual(po.status, "Inactive")
        self.assertEqual(source_order.status, "PO needed")
        self.assertEqual(stage.status, "Cancelled")
        self.assertEqual(inspection.result, "Cancelled")

    def test_foreign_article_is_not_retired(self):
        with self.assertRaises(HTTPException) as raised:
            retirement.retire(self.db, tenant_id=uuid4(), public_id=self.pid)
        self.assertEqual(raised.exception.status_code, 404)
        self.assertFalse(self.product.is_deleted)

    def test_historical_inventory_allocation_keeps_its_zero_stock_origin(self):
        from app.models.shipments import Shipment, ShipmentLine
        from app.models.batches import InventoryAllocation
        order = SalesOrder(id=1, public_id=uuid4(), tenant_id=self.tid, customer_name="Buyer", status="Inactive")
        line = SalesOrderLine(id=1, tenant_id=self.tid, order_id=1, variant_id=1, sku="JKT-1", name="Jacket")
        ship = Shipment(id=1, public_id=uuid4(), tenant_id=self.tid, sales_order_id=1, status="Cancelled", batch_managed=True)
        ship_line = ShipmentLine(id=1, tenant_id=self.tid, shipment_id=1, sku="JKT-1", description="Jacket", qty=1)
        allocation = InventoryAllocation(id=1, tenant_id=self.tid, shipment_id=1, shipment_line_id=1,
            order_line_id=1, stock_level_id=1, qty=1, status="Cancelled")
        self.db.add_all([order, line, ship, ship_line, allocation]); self.db.flush()
        self._retire()
        self.assertTrue(self.product.is_deleted)
        self.assertIsNotNone(self.db.get(StockLevel, allocation.stock_level_id))
        self.assertEqual(self.stock.on_hand, 0)

    def test_article_api_requires_write_permissions_for_each_operation(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.core.security import get_principal, Principal
        from app.core.deps import tenant_db
        from app.modules.catalog.router import router
        app = FastAPI(); app.include_router(router)
        principal = Principal(user_id=None, email="catalog@example.com", tenant_id=str(self.tid), permissions=["catalog.read"])
        app.dependency_overrides[get_principal] = lambda: principal
        app.dependency_overrides[tenant_db] = lambda: self.db
        with TestClient(app) as client:
            base = f"/catalog/products/{self.pid}"
            self.assertEqual(client.get(base + "/delete-preview").status_code, 200)
            self.assertEqual(client.delete(base).status_code, 403)
            principal.permissions = ["catalog.read", "catalog.write"]
            self.assertEqual(client.post(base + "/stock-disposition", json={"stockLevelId": str(self.stock.public_id),
                "quantity": 1, "kind": "Write off", "reason": "Damaged stock"}).status_code, 403)
            self.assertEqual(client.post(base + f"/shipments/{uuid4()}/cancel", json={"reason": "Cancelled shipment"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
