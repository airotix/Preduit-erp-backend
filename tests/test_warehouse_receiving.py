"""Physical receipt and dispatch boundaries, using real workflow services."""
import unittest
import uuid
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select

import test_sales_fulfillment as sales_fixtures
from app.models.inventory import Location, StockEntry, StockLevel
from app.models.procurement import GoodsReceipt, GoodsReceiptLine, PurchaseOrderLine
from app.models.shipments import Shipment
from app.modules.inventory import receiving
from app.modules.inventory.dto import WarehouseReceiptCreate
from app.modules.procurement import service as procurement
from app.modules.quality import service as quality
from app.modules.sales import fulfillment, service as sales
from app.modules.shipments import service as shipping
from app.modules.shipments.dto import ShipmentUpdate


class WarehouseReceivingTests(unittest.TestCase):
    setUp = sales_fixtures.SalesFulfillmentTests.setUp
    tearDown = sales_fixtures.SalesFulfillmentTests.tearDown
    count = sales_fixtures.SalesFulfillmentTests.count
    order = sales_fixtures.SalesFulfillmentTests.order
    create_po = sales_fixtures.SalesFulfillmentTests.create_po
    clear_qc = sales_fixtures.SalesFulfillmentTests.clear_qc

    def work(self, passed=True):
        order = self.order()
        po = self.create_po(order)
        self.clear_qc(po, "Pass" if passed else "In Progress")
        line = self.db.scalar(select(PurchaseOrderLine).where(PurchaseOrderLine.po_id == po.id))
        return order, po, line

    def payload(self, line, qty=40, location=None, request=None):
        return WarehouseReceiptCreate(locationId=(location or self.location).public_id,
            lines=[{"poLineId": line.public_id, "quantity": qty}], reference="DN-1001",
            reason="Physical delivery counted", requestId=request or uuid.uuid4())

    def test_qc_pass_changes_no_stock_and_queue_shows_readiness(self):
        order, po, line = self.work(passed=False)
        self.assertFalse(receiving.queue(self.db, tenant_id=self.tid)["orders"][0]["ready"])
        inspection = self.db.scalar(select(quality.Inspection))
        quality.set_result(self.db, public_id=inspection.public_id, status="Pass")
        queue = receiving.queue(self.db, tenant_id=self.tid)
        self.assertTrue(queue["orders"][0]["ready"])
        self.assertEqual(queue["orders"][0]["lines"][0]["remaining"], 40)
        self.assertEqual((self.level.on_hand, self.level.reserved), (60, 60))
        self.assertFalse(po.inventory_received)
        self.assertEqual(self.count(GoodsReceipt), 0)
        self.assertEqual(self.count(Shipment), 0)

    def test_partial_arrivals_go_to_chosen_locations_and_only_full_order_gets_shipment(self):
        order, po, line = self.work()
        second = Location(tenant_id=self.tid, name="Second warehouse")
        self.db.add(second); self.db.flush()
        first = receiving.post(self.db, tenant_id=self.tid, public_id=po.public_id,
            payload=self.payload(line, 15, second), actor="receiver@example.com")
        self.assertEqual(first.location_id, second.id)
        self.assertEqual(first.actor, "receiver@example.com")
        self.assertEqual(po.status, "Partially received")
        self.assertFalse(po.inventory_received)
        self.assertEqual(self.level.on_hand, 60)
        self.assertEqual(self.count(Shipment), 0)
        detail = receiving.queue(self.db, tenant_id=self.tid)["orders"][0]["lines"][0]
        self.assertEqual((detail["received"], detail["remaining"]), (15, 25))
        self.assertEqual(fulfillment.procurement_summary(self.db, order)[0]["toPurchase"], 0)
        receiving.post(self.db, tenant_id=self.tid, public_id=po.public_id, payload=self.payload(line, 25))
        self.assertTrue(po.inventory_received)
        self.assertEqual(po.status, "Received")
        self.assertEqual(order.status, "Packed")
        self.assertEqual(self.count(Shipment), 1)
        self.assertEqual(self.count(GoodsReceipt), 2)
        self.assertEqual(self.count(GoodsReceiptLine), 2)
        self.assertEqual(self.count(StockEntry), 2)
        self.assertEqual(receiving.queue(self.db, tenant_id=self.tid)["total"], 0)
        destination = self.db.scalar(select(StockLevel).where(StockLevel.location_id == second.id))
        self.assertEqual((self.level.on_hand, destination.on_hand), (85, 15))

    def test_receipt_replay_is_idempotent_and_changed_request_is_rejected(self):
        _, po, line = self.work()
        payload = self.payload(line, 15)
        first = receiving.post(self.db, tenant_id=self.tid, public_id=po.public_id, payload=payload)
        repeat = receiving.post(self.db, tenant_id=self.tid, public_id=po.public_id, payload=payload)
        self.assertEqual(first.id, repeat.id)
        with self.assertRaises(HTTPException) as error:
            receiving.post(self.db, tenant_id=self.tid, public_id=po.public_id,
                payload=self.payload(line, 16, request=payload.requestId))
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.level.on_hand, 75)
        self.assertEqual(self.count(GoodsReceipt), 1)

    def test_overreceipt_foreign_line_location_and_tenant_leave_no_stock(self):
        _, po, line = self.work()
        foreign = Location(tenant_id=uuid.uuid4(), name="Other tenant warehouse")
        self.db.add(foreign); self.db.flush()
        for payload, tenant in [(self.payload(line, 41), self.tid),
                (self.payload(line, location=foreign), self.tid),
                (self.payload(line), uuid.uuid4())]:
            with self.subTest(tenant=tenant, location=payload.locationId):
                with self.assertRaises(HTTPException):
                    receiving.post(self.db, tenant_id=tenant, public_id=po.public_id, payload=payload)
        wrong_line = self.payload(line)
        wrong_line.lines[0].poLineId = uuid.uuid4()
        with self.assertRaises(HTTPException):
            receiving.post(self.db, tenant_id=self.tid, public_id=po.public_id, payload=wrong_line)
        self.assertEqual(self.level.on_hand, 60)
        self.assertEqual(self.count(GoodsReceipt), 0)

    def test_duplicate_lines_and_receipt_before_final_qc_are_rejected(self):
        _, po, line = self.work(passed=False)
        with self.assertRaises(HTTPException):
            receiving.post(self.db, tenant_id=self.tid, public_id=po.public_id, payload=self.payload(line))
        inspection = self.db.scalar(select(quality.Inspection))
        quality.set_result(self.db, public_id=inspection.public_id, status="Pass")
        payload = self.payload(line, 10)
        payload.lines.append(payload.lines[0])
        with self.assertRaises(HTTPException):
            receiving.post(self.db, tenant_id=self.tid, public_id=po.public_id, payload=payload)
        self.assertEqual(self.level.on_hand, 60)

    def test_no_new_receipt_after_full_delivery_and_posted_documents_cannot_be_status_edited(self):
        _, po, line = self.work()
        grn = receiving.post(self.db, tenant_id=self.tid, public_id=po.public_id, payload=self.payload(line))
        with self.assertRaises(HTTPException):
            receiving.post(self.db, tenant_id=self.tid, public_id=po.public_id, payload=self.payload(line))
        with self.assertRaises(HTTPException):
            procurement.set_receipt_status(self.db, public_id=grn.public_id, status="Expected")
        self.assertEqual(self.level.on_hand, 100)

    def test_dispatch_from_multiple_warehouses_removes_stock_once_and_frees_reservations(self):
        order, po, line = self.work()
        second = Location(tenant_id=self.tid, name="Shop")
        self.db.add(second); self.db.flush()
        receiving.post(self.db, tenant_id=self.tid, public_id=po.public_id, payload=self.payload(line, location=second))
        shipment = self.db.scalar(select(Shipment))
        shipping.set_status(self.db, public_id=shipment.public_id, status="Dispatched")
        self.assertTrue(shipment.inventory_dispatched)
        self.assertIsNotNone(shipment.dispatched_at)
        self.assertEqual(order.status, "Shipped")
        self.assertEqual(sum(level.on_hand for level in self.db.scalars(select(StockLevel))), 0)
        self.assertEqual(sum(level.reserved for level in self.db.scalars(select(StockLevel))), 0)
        self.assertEqual(fulfillment.holds_for(self.db, order), [])
        for status in ("Dispatched", "In transit", "Delivered", "Delivered"):
            shipping.set_status(self.db, public_id=shipment.public_id, status=status)
        shipping.set_status(self.db, public_id=shipment.public_id, status="Dispatched")
        self.assertEqual(shipment.status, "Delivered")
        self.assertIsNotNone(shipment.delivered_at)
        self.assertEqual(sum(level.on_hand for level in self.db.scalars(select(StockLevel))), 0)

    def test_edit_cannot_skip_dispatch_or_roll_back_dispatched_stock(self):
        self.order(40)
        shipment = self.db.scalar(select(Shipment))
        edit = ShipmentUpdate(carrier="Carrier", destination="Customer", status="Delivered")
        with self.assertRaises(HTTPException):
            shipping.update_shipment(self.db, public_id=shipment.public_id, payload=edit)
        self.assertEqual(shipment.status, "Pending")
        self.assertEqual(self.level.on_hand, 60)
        shipping.update_shipment(self.db, public_id=shipment.public_id, payload=edit.model_copy(update={"status": "Dispatched"}))
        for status in ("Pending", "Label created", "Cancelled", "Bogus"):
            with self.assertRaises(HTTPException):
                shipping.set_status(self.db, public_id=shipment.public_id, status=status)
        self.assertEqual(self.level.on_hand, 20)

    def test_pending_order_can_be_cancelled_but_cannot_be_dispatched_afterwards(self):
        order = self.order(40)
        shipment = self.db.scalar(select(Shipment))
        sales.set_order_status(self.db, public_id=order.public_id, status="Cancelled")
        self.assertEqual(self.level.reserved, 0)
        self.assertEqual(self.level.on_hand, 60)
        with self.assertRaises(HTTPException):
            shipping.set_status(self.db, public_id=shipment.public_id, status="Dispatched")

    def test_failed_dispatch_is_atomic(self):
        order = self.order(40)
        shipment = self.db.scalar(select(Shipment))
        with patch("app.core.audit.write_audit", side_effect=RuntimeError("Audit unavailable")):
            with self.assertRaises(RuntimeError):
                shipping.set_status(self.db, public_id=shipment.public_id, status="Dispatched")
        self.assertEqual((self.level.on_hand, self.level.reserved), (60, 40))
        self.assertEqual(order.status, "Packed")
        self.assertFalse(shipment.inventory_dispatched)
        self.assertEqual(shipment.status, "Pending")
