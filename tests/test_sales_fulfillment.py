"""Real order, stock, procurement, QC and shipment services on isolated SQLite."""
import unittest
import uuid
from decimal import Decimal
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select, func

import test_procurement_production as fixtures
from app.models.catalog import AttributeValue, Product, ProductVariant
from app.models.inventory import Location, StockLevel, OrderStockReservation
from app.models.procurement import PurchaseOrder, PurchaseOrderLine, Supplier, GoodsReceipt, PoInvoice
from app.models.production import ProductionOrder, ProductionOrderLine, ProductionStage
from app.models.quality import Inspection, InspectionCheck
from app.models.sales import Invoice
from app.models.shipments import Shipment, ShipmentLine
from app.modules.sales import service as sales, fulfillment
from app.modules.sales.dto import OrderCreate
from app.modules.procurement import service as procurement
from app.modules.procurement.dto import GoodsReceiptCreate, PurchaseOrderCreate
from app.modules.quality import service as quality
from app.modules.shipments import service as shipping
from app.modules.catalog import service as catalog
from app.modules.inventory import service as inventory
from app.modules.inventory.dto import MatrixUpdate, StockAdjustmentCreate, StockReceiptCreate


class SalesFulfillmentTests(unittest.TestCase):
    def setUp(self):
        fixtures.ProcurementProductionTests.setUp(self)
        audit = patch("app.core.audit.write_audit")
        audit.start(); self.addCleanup(audit.stop)
        self.supplier = Supplier(tenant_id=self.tid, name="Factory", lead_time="14 days")
        self.location = Location(tenant_id=self.tid, name="Warehouse")
        self.product = Product(tenant_id=self.tid, title="Shirt", status="Active")
        self.color = AttributeValue(tenant_id=self.tid, attr_type="Color", value="Blue", code="BLUE")
        self.size = AttributeValue(tenant_id=self.tid, attr_type="Size", value="S", code="S")
        self.db.add_all([self.supplier, self.location, self.product, self.color, self.size])
        self.db.flush()
        self.variant = ProductVariant(tenant_id=self.tid, product_id=self.product.id, sku="SH-B-S",
            color_id=self.color.id, size_id=self.size.id, price=20, supplier_price=Decimal("8"), currency_code="EUR")
        self.db.add(self.variant)
        self.db.flush()
        self.level = StockLevel(tenant_id=self.tid, variant_id=self.variant.id, location_id=self.location.id, on_hand=60, reserved=0)
        self.db.add(self.level)
        self.db.flush()

    tearDown = fixtures.ProcurementProductionTests.tearDown

    def count(self, model):
        return self.db.scalar(select(func.count()).select_from(model))

    def restock_matrix(self, qty, source="catalog"):
        delta = qty - self.level.on_hand
        if delta > 0:
            return inventory.create_stock_receipt(self.db, tenant_id=self.tid,
                payload=StockReceiptCreate(location=self.location.name, reference="RC-TEST",
                    reason="Replenishment", lines=[dict(name="Shirt", color="Blue", size="S", qty=delta)]))
        if delta < 0:
            return inventory.adjust_stock_count(self.db, tenant_id=self.tid,
                payload=StockAdjustmentCreate(variantId=self.variant.id, location=self.location.name,
                    countedQty=qty, reference="CC-TEST", reason="Count correction"))

    def test_catalog_restock_ships_waiting_order_once_and_preserves_holds(self):
        order = self.order()
        self.restock_matrix(100)
        self.assertEqual(order.status, "Packed")
        shipment = self.db.scalar(select(Shipment))
        self.assertEqual(shipment.status, "Pending")
        self.assertEqual(shipment.sales_order_id, order.id)
        self.assertEqual((self.level.on_hand, self.level.reserved), (100, 100))
        self.restock_matrix(100)
        self.assertEqual(self.count(Shipment), 1)
        self.assertEqual(sum(h.qty for h in fulfillment.holds_for(self.db, order)), 100)

    def test_inventory_partial_restock_keeps_awaiting_order_and_reserves_extra(self):
        order = self.order()
        po = self.create_po(order)
        self.restock_matrix(80, source="inventory")
        self.assertEqual(order.status, "Awaiting stock")
        self.assertEqual(self.level.reserved, 80)
        self.assertEqual(fulfillment.stock_summary(self.db, order)[0]["missing"], 20)
        self.assertEqual(self.count(Shipment), 0)
        self.assertFalse(po.inventory_received)

    def test_inventory_receipt_fulfills_from_independent_stock_without_receiving_po(self):
        order = self.order()
        po = self.create_po(order)
        inventory.create_stock_receipt(self.db, tenant_id=self.tid, payload=StockReceiptCreate(
            location=self.location.name, reference="RC-TEST", reason="Goods received",
            lines=[dict(name="Shirt", color="Blue", size="S", qty=40)]))
        self.assertEqual(order.status, "Packed")
        self.assertEqual((self.level.on_hand, self.level.reserved), (100, 100))
        self.assertEqual(self.count(Shipment), 1)
        self.assertFalse(po.inventory_received)
        self.assertEqual(self.count(GoodsReceipt), 0)

    def test_restock_allocates_competing_orders_oldest_first_without_overselling(self):
        first, second = self.order(), self.order(50)
        self.restock_matrix(130)
        self.assertEqual(first.status, "Packed")
        self.assertEqual(second.status, "PO needed")
        self.assertEqual(sum(h.qty for h in fulfillment.holds_for(self.db, second)), 30)
        self.assertEqual(self.level.reserved, 130)
        self.restock_matrix(150, source="inventory")
        self.assertEqual(second.status, "Packed")
        self.assertEqual(self.count(Shipment), 2)
        self.assertEqual((self.level.on_hand, self.level.reserved), (150, 150))

    def test_restock_waits_for_every_size_before_shipment(self):
        order = self.order(extra_lines=[dict(name="Shirt", color="Blue", size="M", qty=5, price=20)])
        self.restock_matrix(100)
        self.assertEqual(order.status, "PO needed")
        self.assertEqual(self.count(Shipment), 0)
        catalog.save_product_matrix(self.db, tenant_id=self.tid, public_id=self.product.public_id,
            payload=MatrixUpdate(colors=[dict(color="Blue", cells=[
                dict(size="S", qty=100), dict(size="M", qty=0)])]))
        inventory.create_stock_receipt(self.db, tenant_id=self.tid, payload=StockReceiptCreate(
            location=self.location.name, reference="RC-TEST", reason="Goods received",
            lines=[dict(name="Shirt", color="Blue", size="M", qty=5)]))
        self.assertEqual(order.status, "Packed")
        self.assertEqual(self.count(ShipmentLine), 2)

    def test_restock_failure_rolls_back_stock_and_shipment_together(self):
        order = self.order()
        self.db.commit()
        original = fulfillment.refresh_order
        def fail_after_shipment(session, waiting_order):
            original(session, waiting_order)
            raise HTTPException(409, "Failed follow-up")
        with patch.object(fulfillment, "refresh_order", side_effect=fail_after_shipment):
            with self.assertRaises(HTTPException):
                self.restock_matrix(100)
        self.db.rollback()
        self.assertEqual(self.level.on_hand, 60)
        self.assertEqual(self.level.reserved, 60)
        self.assertEqual(order.status, "PO needed")
        self.assertEqual(self.count(Shipment), 0)

    def test_restock_skips_legacy_cancelled_and_other_tenant_orders(self):
        legacy, cancelled, foreign, waiting = [self.order() for _ in range(4)]
        legacy.fulfillment_tracked = False
        cancelled.status = "Cancelled"
        foreign.tenant_id = uuid.uuid4()
        self.db.flush()
        self.assertEqual([order.id for order in fulfillment.lock_waiting_orders(self.db, tenant_id=self.tid)], [waiting.id])
        self.restock_matrix(160)
        self.assertEqual(waiting.status, "Packed")
        self.assertEqual(legacy.status, "PO needed")
        self.assertEqual(cancelled.status, "Cancelled")
        self.assertEqual(foreign.status, "PO needed")
        self.assertEqual(self.count(Shipment), 1)
        self.assertEqual(self.level.reserved, 160)

    def order(self, qty=100, extra_lines=None, **line):
        with patch.object(sales, "build_sales_invoice_draft", return_value=None):
            return sales.create_order(self.db, tenant_id=self.tid, payload=OrderCreate(
                customer="Customer", channel="Retail", lines=[
                    dict(name="Shirt", color="Blue", size="S", sku=self.variant.sku, qty=qty, price=20, **line),
                    *(extra_lines or [])]))["order"]

    def create_po(self, order):
        return fulfillment.create_shortage_po(self.db, tenant_id=self.tid, public_id=order.public_id,
            supplier_id=self.supplier.public_id)

    def clear_qc(self, po, result="Pass"):
        work = self.db.scalar(select(ProductionOrder).where(ProductionOrder.purchase_order_id == po.id))
        lines = self.db.scalars(select(ProductionOrderLine).where(ProductionOrderLine.order_id == work.id)).all()
        inspections = []
        for line in lines:
            self.db.add(ProductionStage(tenant_id=self.tid, order_id=work.id, line_id=line.id,
                seq=1, name="Packed", status="Completed"))
            ins = Inspection(tenant_id=self.tid, order_ref=work.order_no, item=line.name, stage="Final", result=result)
            self.db.add(ins)
            inspections.append(ins)
        self.db.flush()
        return inspections[0]

    def receive(self, po):
        if not hasattr(self, "receipt_payloads"):
            self.receipt_payloads = {}
        if po.id not in self.receipt_payloads:
            lines = self.db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.po_id == po.id)).all()
            self.receipt_payloads[po.id] = GoodsReceiptCreate(po=po.po_no, supplier=self.supplier.name,
                locationId=self.location.public_id, reference="DEL-TEST", reason="Physical warehouse receipt",
                requestId=uuid.uuid4(), lines=[{"poLineId": line.public_id, "quantity": line.qty} for line in lines])
        return procurement.create_receipt(self.db, tenant_id=self.tid, payload=self.receipt_payloads[po.id])

    def test_standalone_po_receives_once_and_fulfills_waiting_order(self):
        order = self.order(100)
        po = procurement.create_po(self.db, tenant_id=self.tid, payload=PurchaseOrderCreate(
            supplier=self.supplier.name, expected="20 Oct 2026", lines=[dict(name="Shirt", color="Blue", size="S",
                sku=self.variant.sku, qty=40, price=8)]))
        ins = self.clear_qc(po, "In Progress")
        quality.pass_and_ship(self.db, tenant_id=self.tid, public_id=ins.public_id, carrier="", destination="")
        self.assertFalse(po.inventory_received)
        self.receive(po)
        self.assertTrue(po.inventory_received)
        self.assertEqual(self.level.on_hand, 100)
        self.assertEqual(self.level.reserved, 100)
        self.assertEqual(order.status, "Packed")
        self.assertEqual(self.count(Shipment), 1)
        self.assertEqual(self.db.scalar(select(Shipment)).sales_order_id, order.id)
        self.receive(po)
        self.assertEqual(self.level.on_hand, 100)
        self.assertEqual(self.count(GoodsReceipt), 1)

    def test_inspection_only_recovery_keeps_tna_and_receives_after_pass(self):
        order = self.order()
        po = self.create_po(order)
        parent = self.clear_qc(po, "Fail")
        old = self.db.scalar(select(ProductionStage))
        out = quality.reopen_failed(self.db, tenant_id=self.tid, public_id=parent.public_id, mode="inspection")
        child = out["inspection"]
        self.assertEqual(child.item, parent.item)
        self.assertEqual(child.parent_inspection_id, parent.id)
        self.assertFalse(old.is_deleted)
        self.assertEqual(old.status, "Completed")
        self.assertEqual(quality.reopen_failed(self.db, tenant_id=self.tid, public_id=parent.public_id,
            mode="inspection")["inspection"].id, child.id)
        quality.start_inspection(self.db, public_id=child.public_id, tenant_id=self.tid)
        quality.set_result(self.db, public_id=child.public_id, status="Pass", tenant_id=self.tid)
        self.assertFalse(po.inventory_received)
        self.receive(po)
        self.assertTrue(po.inventory_received)
        self.assertEqual(order.status, "Packed")
        self.assertEqual(parent.result, "Fail")
        detail = quality.inspection_detail(self.db, public_id=child.public_id)
        self.assertTrue(detail["inspection"]["shipment"]["allPassed"])
        self.assertEqual(detail["statusLabel"], "Passed")

    def test_standalone_receipt_combines_duplicate_variants_without_existing_stock(self):
        self.db.delete(self.level)
        self.db.flush()
        po = procurement.create_po(self.db, tenant_id=self.tid, payload=PurchaseOrderCreate(
            supplier=self.supplier.name, expected="20 Oct 2026", lines=[
                dict(name="Shirt", color="Blue", size="S", sku=self.variant.sku, qty=qty, price=8)
                for qty in [10, 20]]))
        self.clear_qc(po)
        self.receive(po)
        self.assertEqual(self.count(StockLevel), 1)
        self.assertEqual(self.db.scalar(select(StockLevel)).on_hand, 30)

    def test_production_recovery_archives_run_and_blocks_inspection_until_complete(self):
        from app.modules.production import service as production
        order = self.order()
        po = self.create_po(order)
        parent = self.clear_qc(po, "Fail")
        old = self.db.scalar(select(ProductionStage))
        out = quality.reopen_failed(self.db, tenant_id=self.tid, public_id=parent.public_id, mode="production")
        child = out["inspection"]
        self.assertTrue(old.is_deleted)
        self.assertEqual(old.status, "Completed")
        new = self.db.scalar(select(ProductionStage).where(ProductionStage.is_deleted.is_(False)))
        self.assertEqual(new.status, "Pending")
        again = quality.reopen_failed(self.db, tenant_id=self.tid, public_id=parent.public_id, mode="production")
        self.assertEqual(again["inspection"].id, child.id)
        self.assertEqual(self.count(ProductionStage), 2)
        self.assertEqual(out["production"].progress, 0)
        for action in [lambda: quality.start_inspection(self.db, public_id=child.public_id, tenant_id=self.tid),
                       lambda: quality.set_result(self.db, public_id=child.public_id, status="Pass", tenant_id=self.tid)]:
            with self.assertRaises(HTTPException) as err:
                action()
            self.assertEqual(err.exception.status_code, 409)
        with self.assertRaises(HTTPException):
            quality.reopen_failed(self.db, tenant_id=self.tid, public_id=parent.public_id, mode="inspection")
        production.stage_action(self.db, action="complete", public_id=new.public_id)
        self.assertFalse(po.inventory_received)
        quality.start_inspection(self.db, public_id=child.public_id, tenant_id=self.tid)
        quality.set_result(self.db, public_id=child.public_id, status="Pass", tenant_id=self.tid)
        self.assertFalse(po.inventory_received)
        self.receive(po)
        self.assertTrue(po.inventory_received)
        self.assertEqual(order.status, "Packed")

    def test_recovery_rejects_other_tenant_and_already_received_stock(self):
        order = self.order()
        po = self.create_po(order)
        parent = self.clear_qc(po, "Fail")
        with self.assertRaises(HTTPException) as err:
            quality.reopen_failed(self.db, tenant_id=uuid.uuid4(), public_id=parent.public_id, mode="production")
        self.assertEqual(err.exception.status_code, 404)
        po.inventory_received = True
        with self.assertRaises(HTTPException) as err:
            quality.reopen_failed(self.db, tenant_id=self.tid, public_id=parent.public_id, mode="production")
        self.assertEqual(err.exception.status_code, 409)

    def test_production_recovery_leaves_other_items_and_selected_mode_intact(self):
        from app.modules.quality.dto import DispositionIn
        order = self.order()
        po = self.create_po(order)
        parent = self.clear_qc(po, "Fail")
        work = self.db.scalar(select(ProductionOrder).where(ProductionOrder.purchase_order_id == po.id))
        other = ProductionOrderLine(tenant_id=self.tid, order_id=work.id, name="Jacket", qty=1)
        self.db.add(other)
        self.db.flush()
        sibling_stage = ProductionStage(tenant_id=self.tid, order_id=work.id, line_id=other.id,
            seq=1, name="Packed", status="Completed")
        self.db.add(sibling_stage)
        self.db.flush()
        quality.reopen_failed(self.db, tenant_id=self.tid, public_id=parent.public_id, mode="production")
        self.assertFalse(sibling_stage.is_deleted)
        self.assertEqual(sibling_stage.status, "Completed")
        with self.assertRaises(HTTPException):
            quality.set_disposition(self.db, public_id=parent.public_id, payload=DispositionIn(disposition="Hold"))

    def test_production_recovery_requires_production_permission(self):
        from app.core.security import Principal
        from app.modules.quality.router import reopen_failed
        from app.modules.quality.dto import ReopenIn
        order = self.order()
        po = self.create_po(order)
        parent = self.clear_qc(po, "Fail")
        principal = Principal(user_id=None, email=None, tenant_id=str(self.tid), permissions=["quality.write"])
        with self.assertRaises(HTTPException) as err:
            reopen_failed(str(parent.public_id), ReopenIn(mode="production"), principal, self.db)
        self.assertEqual(err.exception.status_code, 403)
        result = reopen_failed(str(parent.public_id), ReopenIn(mode="inspection"), principal, self.db)
        self.assertTrue(result["publicId"])

    def test_fully_stocked_order_creates_pending_shipment_and_invoice(self):
        order = self.order(40)
        shipment = self.db.scalar(select(Shipment))
        self.assertEqual(order.status, "Packed")
        self.assertEqual(shipment.status, "Pending")
        self.assertEqual(shipment.order_ref, order.order_no)
        self.assertEqual(self.level.reserved, 40)
        self.assertEqual(self.count(Invoice), 1)
        self.assertEqual(self.db.scalar(select(ShipmentLine)).qty, 40)
        fulfillment.refresh_order(self.db, order)
        self.assertEqual(self.count(Shipment), 1)

    def test_partial_stock_holds_available_and_po_contains_shortfall_at_purchase_price(self):
        order = self.order()
        self.assertEqual(order.status, "PO needed")
        self.assertEqual(self.level.reserved, 60)
        self.assertEqual(self.count(Shipment), 0)
        po = self.create_po(order)
        line = self.db.scalar(select(PurchaseOrderLine))
        self.assertEqual((line.qty, line.price), (40, Decimal("8")))
        self.assertEqual(po.sales_order_id, order.id)
        invoice = self.db.scalar(select(PoInvoice).where(PoInvoice.po_no == po.po_no))
        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.total, Decimal("320"))
        self.assertEqual(order.status, "Awaiting stock")
        self.assertIsNotNone(line.sales_order_line_id)
        self.assertEqual(self.count(ProductionOrder), 1)
        self.assertEqual(self.create_po(order).id, po.id)
        self.assertEqual(self.count(PurchaseOrder), 1)
        self.assertEqual(self.count(PoInvoice), 1)

    def test_another_order_cannot_take_held_stock(self):
        first = self.order(50)
        second = self.order(30)
        self.assertEqual(self.level.reserved, 60)
        self.assertEqual(fulfillment.stock_summary(self.db, first)[0]["reserved"], 50)
        self.assertEqual(fulfillment.stock_summary(self.db, second)[0]["reserved"], 10)
        self.assertEqual(fulfillment.stock_summary(self.db, second)[0]["missing"], 20)

    def test_stock_is_reserved_across_locations_without_overdraw(self):
        other = Location(tenant_id=self.tid, name="Shop")
        self.db.add(other); self.db.flush()
        level = StockLevel(tenant_id=self.tid, variant_id=self.variant.id, location_id=other.id, on_hand=40, reserved=0)
        self.db.add(level); self.db.flush()
        order = self.order()
        self.assertEqual(order.status, "Packed")
        self.assertEqual((self.level.reserved, level.reserved), (60, 40))
        self.assertEqual(self.count(Shipment), 1)

    def test_receipt_requires_final_qc_and_does_not_change_stock_on_failure(self):
        po = self.create_po(self.order())
        with self.assertRaises(HTTPException):
            self.receive(po)
        self.assertEqual(self.level.on_hand, 60)
        self.assertEqual(self.count(GoodsReceipt), 0)

    def test_final_qc_then_warehouse_receipt_then_dispatch_once(self):
        order = self.order()
        po = self.create_po(order)
        ins = self.clear_qc(po, result="In Progress")
        quality.pass_and_ship(self.db, tenant_id=self.tid, public_id=ins.public_id, carrier="", destination="")
        qc_detail = quality.inspection_detail(self.db, public_id=ins.public_id)
        self.assertEqual(qc_detail["inspection"]["shipment"]["replenishmentPO"]["publicId"], str(po.public_id))
        self.assertEqual(self.count(Shipment), 0)
        self.receive(po)
        self.assertEqual(self.count(Shipment), 1)
        receipt = self.db.scalar(select(GoodsReceipt))
        self.assertEqual(receipt.status, "Received")
        self.assertEqual(receipt.location, self.location.name)
        self.assertEqual((self.level.on_hand, self.level.reserved), (100, 100))
        self.assertEqual(order.status, "Packed")
        shipment = self.db.scalar(select(Shipment))
        self.assertEqual(self.db.scalar(select(ShipmentLine)).qty, 100)
        self.assertEqual(self.receive(po).id, receipt.id)
        self.assertEqual(self.level.on_hand, 100)
        self.assertEqual(self.count(Shipment), 1)
        shipping.set_status(self.db, public_id=shipment.public_id, status="Dispatched")
        self.assertEqual((self.level.on_hand, self.level.reserved), (0, 0))
        self.assertEqual(order.status, "Shipped")
        shipping.set_status(self.db, public_id=shipment.public_id, status="Delivered")
        shipping.set_status(self.db, public_id=shipment.public_id, status="Delivered")
        self.assertEqual(self.level.on_hand, 0)

    def test_cancelling_only_releases_this_orders_holds(self):
        first = self.order(40, extra_lines=[{"name": "Missing item", "qty": 1, "price": 10}])
        second = self.order(30)
        sales.set_order_status(self.db, public_id=first.public_id, status="Cancelled")
        self.assertEqual((self.level.on_hand, self.level.reserved), (60, 20))
        self.assertEqual(fulfillment.stock_summary(self.db, second)[0]["reserved"], 20)
        with self.assertRaises(HTTPException):
            self.create_po(first)

    def test_incomplete_order_cannot_be_manually_packed_or_shipped(self):
        order = self.order()
        for status in ["Packed", "Shipped"]:
            with self.assertRaises(HTTPException):
                sales.set_order_status(self.db, public_id=order.public_id, status=status)

    def test_missing_purchase_price_does_not_create_po(self):
        self.variant.supplier_price = None
        self.db.flush()
        with self.assertRaises(HTTPException):
            self.create_po(self.order())
        self.assertEqual(self.count(PurchaseOrder), 0)

    def test_cross_tenant_order_and_supplier_are_rejected(self):
        order = self.order()
        with self.assertRaises(HTTPException):
            fulfillment.create_shortage_po(self.db, tenant_id=uuid.uuid4(), public_id=order.public_id,
                supplier_id=self.supplier.public_id)
        other = Supplier(tenant_id=uuid.uuid4(), name="Other")
        self.db.add(other); self.db.flush()
        with self.assertRaises(HTTPException):
            fulfillment.create_shortage_po(self.db, tenant_id=self.tid, public_id=order.public_id,
                supplier_id=other.public_id)
        self.assertEqual(self.count(PurchaseOrder), 0)

    def test_missing_inventory_reserves_nothing_and_orders_full_quantity(self):
        self.level.on_hand = 0; self.db.flush()
        order = self.order(20)
        self.assertEqual(self.count(OrderStockReservation), 0)
        self.create_po(order)
        self.assertEqual(self.db.scalar(select(PurchaseOrderLine)).qty, 20)

    def test_new_stock_before_po_click_avoids_unnecessary_po(self):
        order = self.order()
        self.level.on_hand = 100; self.db.flush()
        self.assertIsNone(self.create_po(order))
        self.assertEqual(self.count(PurchaseOrder), 0)
        self.assertEqual(self.count(Shipment), 1)

    def test_reserved_stock_cannot_be_transferred_away(self):
        from app.modules.inventory import repository as inventory
        self.order(60)
        with self.assertRaises(HTTPException):
            inventory.adjust_stock(self.db, tenant_id=self.tid, variant_id=self.variant.id,
                location_id=self.location.id, on_hand_delta=-10)
        self.assertEqual((self.level.on_hand, self.level.reserved), (60, 60))

    def test_shipped_order_cannot_be_cancelled_after_dispatch(self):
        order = self.order(30)
        shipment = self.db.scalar(select(Shipment))
        shipping.set_status(self.db, public_id=shipment.public_id, status="Dispatched")
        with self.assertRaises(HTTPException):
            sales.set_order_status(self.db, public_id=order.public_id, status="Cancelled")
        self.assertEqual(shipment.status, "Dispatched")

    def test_latest_failed_reinspection_blocks_inventory_receipt(self):
        po = self.create_po(self.order())
        passed = self.clear_qc(po)
        self.db.add(Inspection(tenant_id=self.tid, order_ref=passed.order_ref, item=passed.item,
            stage="Final", result="Fail", parent_inspection_id=passed.id))
        self.db.flush()
        with self.assertRaises(HTTPException):
            self.receive(po)
        self.assertEqual(self.level.on_hand, 60)

    def test_qc_for_only_one_of_multiple_items_does_not_release_order(self):
        order = self.order()
        po = self.create_po(order)
        passed = self.clear_qc(po)
        work = self.db.scalar(select(ProductionOrder))
        item = ProductionOrderLine(tenant_id=self.tid, order_id=work.id, name="Other item", qty=10)
        self.db.add(item); self.db.flush()
        self.db.add(ProductionStage(tenant_id=self.tid, order_id=work.id, line_id=item.id,
            seq=1, name="Packed", status="Completed"))
        self.db.flush()
        with self.assertRaises(HTTPException):
            self.receive(po)
        self.assertEqual(self.count(Shipment), 0)

    def test_duplicate_variant_lines_share_available_stock_without_double_reserving(self):
        with patch.object(sales, "build_sales_invoice_draft", return_value=None):
            order = sales.create_order(self.db, tenant_id=self.tid, payload=OrderCreate(
                customer="Customer", channel="Retail", lines=[
                    {"name": "Shirt", "sku": self.variant.sku, "color": "Blue", "size": "S", "qty": 40, "price": 20},
                    {"name": "Shirt", "sku": self.variant.sku, "color": "Blue", "size": "S", "qty": 40, "price": 20},
                ]))["order"]
        summary = fulfillment.stock_summary(self.db, order)
        self.assertEqual([r["reserved"] for r in summary], [40, 20])
        self.create_po(order)
        self.assertEqual(self.db.scalar(select(PurchaseOrderLine)).qty, 20)

    def test_finance_failure_rolls_back_order_holds_and_auto_shipment(self):
        self.db.commit()
        with patch("app.modules.finance.service.post_unposted", side_effect=RuntimeError("Finance failed")):
            with self.assertRaises(RuntimeError):
                self.order(40)
        self.db.rollback()
        self.assertEqual(self.count(Shipment), 0)
        self.assertEqual(self.count(OrderStockReservation), 0)
        self.assertEqual(self.level.reserved, 0)

    def test_color_rename_does_not_break_held_order_or_po_receipt(self):
        order = self.order()
        po = self.create_po(order)
        self.color.value = "Royal Blue"
        self.db.flush()
        self.clear_qc(po)
        self.receive(po)
        self.assertEqual(self.level.reserved, 100)
        self.assertEqual(self.count(Shipment), 1)

    def test_complete_inspection_waits_for_explicit_warehouse_receipt(self):
        order = self.order()
        po = self.create_po(order)
        ins = self.clear_qc(po, result="In Progress")
        ins.max_defects = 0
        self.db.add(InspectionCheck(tenant_id=self.tid, inspection_id=ins.id, seq=1,
            criterion="Sizing", requirement="Correct size", result="Pass"))
        self.db.flush()
        out = quality.complete_inspection(self.db, public_id=ins.public_id)
        self.assertEqual(out["result"], "Pass")
        self.assertFalse(po.inventory_received)
        self.receive(po)
        self.assertTrue(po.inventory_received)
        self.assertEqual((self.level.on_hand, self.level.reserved), (100, 100))
        self.assertEqual(self.count(Shipment), 1)

    def test_failed_inspection_does_not_add_stock(self):
        po = self.create_po(self.order())
        ins = self.clear_qc(po, result="In Progress")
        ins.max_defects = 0
        self.db.add(InspectionCheck(tenant_id=self.tid, inspection_id=ins.id, seq=1,
            criterion="Sizing", requirement="Correct size", result="Fail"))
        self.db.flush()
        out = quality.complete_inspection(self.db, public_id=ins.public_id)
        self.assertEqual(out["result"], "Fail")
        self.assertEqual(self.level.on_hand, 60)
        self.assertEqual(self.count(GoodsReceipt), 0)

    def test_repeated_pass_status_cannot_add_stock_twice(self):
        po = self.create_po(self.order())
        ins = self.clear_qc(po, result="In Progress")
        for _ in range(2):
            quality.set_result(self.db, public_id=ins.public_id, status="Pass", tenant_id=self.tid)
        self.assertEqual(self.level.on_hand, 60)
        self.assertEqual(self.count(GoodsReceipt), 0)
        self.receive(po)
        self.receive(po)
        self.assertEqual(self.level.on_hand, 100)
        self.assertEqual(self.count(GoodsReceipt), 1)
        self.assertEqual(self.count(Shipment), 1)

    def test_qc_receipt_rechecks_other_awaiting_orders_that_are_fully_stocked(self):
        source = self.order()
        source_po = self.create_po(source)
        other = self.order(20)
        self.create_po(other)
        # Stock from another receipt is available but not allocated yet.
        self.level.on_hand += 20
        self.db.flush()
        ins = self.clear_qc(source_po, result="In Progress")
        quality.set_result(self.db, public_id=ins.public_id, status="Pass", tenant_id=self.tid)
        self.receive(source_po)
        self.assertEqual((self.level.on_hand, self.level.reserved), (120, 120))
        self.assertEqual(other.status, "Packed")
        self.assertEqual(self.count(Shipment), 2)
        self.assertEqual(fulfillment.stock_summary(self.db, other)[0]["missing"], 0)

    def test_source_order_gets_manufactured_stock_before_other_waiting_orders(self):
        source = self.order()
        po = self.create_po(source)
        other = self.order(20)
        self.create_po(other)
        ins = self.clear_qc(po, result="In Progress")
        quality.set_result(self.db, public_id=ins.public_id, status="Pass")
        self.receive(po)
        self.assertEqual(fulfillment.stock_summary(self.db, source)[0]["reserved"], 100)
        self.assertEqual(fulfillment.stock_summary(self.db, other)[0]["reserved"], 0)
        self.assertEqual(other.status, "Awaiting stock")
        self.assertEqual(self.count(Shipment), 1)

    def test_nonfinal_pass_does_not_receive_production(self):
        po = self.create_po(self.order())
        ins = self.clear_qc(po, result="In Progress")
        ins.stage = "In-line"
        self.db.flush()
        quality.set_result(self.db, public_id=ins.public_id, status="Pass")
        self.assertFalse(po.inventory_received)
        self.assertEqual(self.level.on_hand, 60)

    def test_warehouse_receipt_does_not_recheck_other_tenants(self):
        source = self.order()
        po = self.create_po(source)
        from app.models.sales import SalesOrder
        other = SalesOrder(tenant_id=uuid.uuid4(), customer_name="Other", status="Awaiting stock", fulfillment_tracked=True)
        self.db.add(other); self.db.flush()
        ins = self.clear_qc(po, result="In Progress")
        quality.set_result(self.db, public_id=ins.public_id, status="Pass")
        self.receive(po)
        self.assertEqual(other.status, "Awaiting stock")

    def test_passed_qc_waits_for_last_production_stage_to_complete(self):
        from app.modules.production import service as production
        po = self.create_po(self.order())
        ins = self.clear_qc(po, result="In Progress")
        stage = self.db.scalar(select(ProductionStage))
        stage.status = "In Progress"
        self.db.flush()
        quality.set_result(self.db, public_id=ins.public_id, status="Pass")
        self.assertEqual(self.count(GoodsReceipt), 0)
        production.stage_action(self.db, action="complete", public_id=stage.public_id)
        self.assertEqual(self.level.on_hand, 60)
        self.receive(po)
        self.assertEqual(self.level.on_hand, 100)
        self.assertEqual(self.count(GoodsReceipt), 1)
        self.assertEqual(self.count(Shipment), 1)

    def test_warehouse_receipt_rolls_back_on_shipment_failure_but_qc_remains_passed(self):
        po = self.create_po(self.order())
        ins = self.clear_qc(po, result="In Progress")
        quality.set_result(self.db, public_id=ins.public_id, status="Pass")
        self.db.commit()
        with patch.object(fulfillment, "refresh_order", side_effect=RuntimeError("Shipment failed")):
            with self.assertRaises(RuntimeError):
                self.receive(po)
        self.assertEqual(ins.result, "Pass")
        self.assertFalse(po.inventory_received)
        self.assertEqual((self.level.on_hand, self.level.reserved), (60, 60))
        self.assertEqual(self.count(GoodsReceipt), 0)

    def test_missing_location_blocks_receipt_but_does_not_block_qc(self):
        po = self.create_po(self.order())
        ins = self.clear_qc(po, result="In Progress")
        self.location.is_deleted = True
        self.db.flush()
        quality.set_result(self.db, public_id=ins.public_id, status="Pass")
        with self.assertRaises(HTTPException) as out:
            self.receive(po)
        self.assertIn("location", out.exception.detail)
        self.assertEqual(ins.result, "Pass")
        self.assertFalse(po.inventory_received)
        self.assertEqual(self.count(GoodsReceipt), 0)

    def test_dispatch_consumes_stock_once_and_delivery_only_confirms_receipt(self):
        order = self.order(40)
        shipment = self.db.scalar(select(Shipment))
        self.assertEqual(order.status, "Packed")
        with self.assertRaises(HTTPException):
            sales.set_order_status(self.db, public_id=order.public_id, status="Shipped")
        with self.assertRaises(HTTPException):
            shipping.set_status(self.db, public_id=shipment.public_id, status="Delivered")
        shipping.set_status(self.db, public_id=shipment.public_id, status="Label created")
        self.assertEqual((self.level.on_hand, self.level.reserved), (60, 40))
        for status in ["Dispatched", "Dispatched", "In transit", "Out for delivery", "Delivered", "Delivered"]:
            shipping.set_status(self.db, public_id=shipment.public_id, status=status)
            self.assertEqual((self.level.on_hand, self.level.reserved), (20, 0))
        self.assertEqual(order.status, "Shipped")
        self.assertEqual(self.variant.qty_on_hand, 20)
        with self.assertRaises(HTTPException):
            shipping.set_status(self.db, public_id=shipment.public_id, status="In transit")
