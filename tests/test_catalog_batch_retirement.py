"""Article retirement integrates with the batch and warehouse ledgers."""
import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

import test_batch_execution as batch_fixtures
from app.models.batches import BatchAllocation, BatchStage, InventoryAllocation
from app.models.catalog import Product
from app.models.inventory import StockLevel
from app.models.shipments import Shipment, ShipmentLine
from app.modules.catalog import retirement, commitments
from app.modules.catalog.dto import StockDisposition
from app.modules.sales import service as sales, fulfillment
from app.modules.procurement import service as procurement
from app.modules.batches import service as batches


class CatalogBatchRetirementTests(unittest.TestCase):
    setUp = batch_fixtures.BatchExecutionTests.setUp
    tearDown = batch_fixtures.BatchExecutionTests.tearDown
    order = batch_fixtures.BatchExecutionTests.order
    count = batch_fixtures.BatchExecutionTests.count
    cmd = batch_fixtures.BatchExecutionTests.cmd
    plan = batch_fixtures.BatchExecutionTests.plan
    produce = batch_fixtures.BatchExecutionTests.produce
    qc = batch_fixtures.BatchExecutionTests.qc
    receive = batch_fixtures.BatchExecutionTests.receive
    ship = batch_fixtures.BatchExecutionTests.ship
    lot = batch_fixtures.BatchExecutionTests.lot

    def preview(self):
        return retirement.preview(self.db, tenant_id=self.tid, public_id=self.product.public_id)

    def test_unreceived_production_can_be_withdrawn_and_cannot_restart(self):
        batch, line = self.plan()
        self.produce(batch, line)
        with patch.object(commitments, "write_audit"):
            procurement.set_po_status(self.db, public_id=self.po.public_id, status="Inactive", reason="Supplier commitment withdrawn")
        self.assertEqual(self.po.status, "Inactive")
        self.assertEqual(batch.status, "Cancelled")
        self.assertEqual(line.completed, 200)
        self.assertTrue(self.work.is_deleted)
        with self.assertRaises(HTTPException):
            batches.parent(self.db, self.tid, self.work.public_id, lock=True)
        self.assertEqual(self.level.on_hand, 0)
        self.assertEqual(self.so.status, "PO needed")

    def test_warehouse_receipt_prevents_po_withdrawal(self):
        batch, line = self.plan()
        self.produce(batch, line); self.qc(batch, line, 200); self.receive(batch, line, 200)
        with self.assertRaises(HTTPException):
            procurement.set_po_status(self.db, public_id=self.po.public_id, status="Inactive", reason="Supplier withdrawal")
        self.assertEqual(self.level.on_hand, 200)
        self.assertFalse(self.work.is_deleted)
        self.assertFalse(self.preview()["purchaseOrders"][0]["canMarkInactive"])

    def test_article_shipment_stock_disposition_and_retirement_keep_batch_history(self):
        batch, line = self.plan(1000)
        self.produce(batch, line); self.qc(batch, line, 1000); self.receive(batch, line, 1000)
        ship_id = UUID(self.ship([dict(lotId=self.lot(line).id, qty=1000)]))
        self.assertTrue(self.preview()["shipments"][0]["canCancel"])
        with patch.object(commitments, "write_audit"), patch.object(retirement, "write_audit"):
            retirement.cancel_article_shipment(self.db, tenant_id=self.tid, public_id=self.product.public_id,
                shipment_id=ship_id, reason="Article shipment withdrawn")
            sales.set_order_status(self.db, public_id=self.so.public_id, status="Inactive", reason="Customer commitment withdrawn")
            self.assertEqual(self.level.reserved, 0)
            retirement.dispose_stock(self.db, tenant_id=self.tid, public_id=self.product.public_id,
                payload=StockDisposition(stockLevelId=self.level.public_id, quantity=1000, kind="Write off", reason="Damaged stock"))
            self.assertEqual(self.lot(line).on_hand, 0)
            self.assertEqual(line.received, 1000)
            self.assertTrue(self.preview()["canDelete"])
            retirement.retire(self.db, tenant_id=self.tid, public_id=self.product.public_id)
        self.assertTrue(self.product.is_deleted)
        self.assertEqual(batch.status, "Cancelled")
        self.assertEqual(self.db.scalar(select(BatchAllocation)).status, "Cancelled")
        self.assertEqual(line.completed, 1000)
        self.assertEqual(line.approved, 1000)
        fulfillment.refresh_order(self.db, self.so)
        self.assertEqual(self.so.status, "Inactive")

    def test_mixed_article_shipment_cannot_be_cancelled_from_article_dialog(self):
        batch, line = self.plan()
        self.produce(batch, line); self.qc(batch, line, 200); self.receive(batch, line, 200)
        ship_id = UUID(self.ship([dict(lotId=self.lot(line).id, qty=100)]))
        ship = batches.one(self.db, Shipment, self.tid, ship_id)
        self.db.add(ShipmentLine(tenant_id=self.tid, shipment_id=ship.id, sku="OTHER-ARTICLE", description="Another article", qty=1))
        self.db.flush()
        self.assertFalse(self.preview()["shipments"][0]["canCancel"])
        with self.assertRaises(HTTPException):
            retirement.cancel_article_shipment(self.db, tenant_id=self.tid, public_id=self.product.public_id,
                shipment_id=ship_id, reason="Article withdrawal")
        self.assertEqual(ship.status, "Pending")
        self.assertEqual(self.lot(line).allocated, 100)

    def test_dispatched_order_cannot_be_withdrawn(self):
        batch, line = self.plan()
        self.produce(batch, line); self.qc(batch, line, 200); self.receive(batch, line, 200)
        ship_id = self.ship([dict(lotId=self.lot(line).id, qty=100)])
        batches.shipment_status(self.db, self.tid, ship_id, "In transit")
        with self.assertRaises(HTTPException):
            sales.set_order_status(self.db, public_id=self.so.public_id, status="Inactive", reason="Customer withdrawal")
        self.assertEqual(self.level.on_hand, 100)
        self.assertNotEqual(self.so.status, "Inactive")

    def test_standalone_batch_earmarked_inventory_can_be_disposed(self):
        self.po.sales_order_id = None
        self.source.sales_order_line_id = None
        self.so.status = "Inactive"
        self.db.flush()
        batch, line = self.plan(1000)
        self.produce(batch, line); self.qc(batch, line, 1000); self.receive(batch, line, 1000)
        self.assertEqual(self.level.reserved, 1000)
        self.assertEqual(self.preview()["stockRows"][0]["available"], 1000)
        with patch.object(retirement, "write_audit"):
            retirement.dispose_stock(self.db, tenant_id=self.tid, public_id=self.product.public_id,
                payload=StockDisposition(stockLevelId=self.level.public_id, quantity=1000, kind="Return to supplier",
                    reference="RTV-1", reason="Supplier accepted return"))
        self.assertEqual((self.level.on_hand, self.level.reserved, self.lot(line).on_hand), (0, 0, 0))
        self.assertTrue(self.preview()["canDelete"])
