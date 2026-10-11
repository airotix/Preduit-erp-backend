"""Business workflows on real services, isolated database and persisted ledgers."""
import unittest
import uuid
from fastapi import HTTPException
from sqlalchemy import select
import test_sales_fulfillment as fixtures
from app.models.batches import ProductionBatch, BatchLine, BatchStage, BatchLot, BatchAllocation, BatchEvent
from app.models.production import ProductionOrder
from app.models.procurement import PurchaseOrderLine
from app.models.inventory import Location, StockLevel
from app.modules.batches import service as batches, dto
from app.modules.sales import fulfillment


class BatchExecutionTests(unittest.TestCase):
    tearDown = fixtures.SalesFulfillmentTests.tearDown
    order = fixtures.SalesFulfillmentTests.order
    count = fixtures.SalesFulfillmentTests.count

    def setUp(self):
        fixtures.SalesFulfillmentTests.setUp(self)
        self.level.on_hand = 0; self.db.flush()
        self.so = self.order(1000)
        self.po = fulfillment.create_shortage_po(self.db, tenant_id=self.tid, public_id=self.so.public_id,
            supplier_id=self.supplier.public_id)
        self.work = self.db.scalar(select(ProductionOrder))
        self.source = self.db.scalar(select(PurchaseOrderLine))

    def cmd(self, cls, **values):
        return cls(requestId=uuid.uuid4(), reason="Recorded by warehouse team", **values)

    def plan(self, qty=200, name="Batch A"):
        command = self.cmd(dto.CreateBatch, name=name, stages=["Cutting", "Packing"], lines=[dict(sourceId=self.source.id, qty=qty)])
        result = batches.create_batch(self.db, self.tid, "actor", self.work.public_id, command)
        batch = batches.one(self.db, ProductionBatch, self.tid, result["publicId"])
        line = self.db.scalar(select(BatchLine).where(BatchLine.batch_id == batch.id))
        return batch, line

    def produce(self, batch, line, qty=None):
        for s in batches.rows(self.db, BatchStage, self.tid, BatchStage.batch_id == batch.id):
            batches.stage(self.db, self.tid, "actor", batch.public_id, s.id, self.cmd(dto.StageCommand, status="Completed"))
        batches.line_action(self.db, self.tid, "actor", batch.public_id, line.id,
            self.cmd(dto.Quantity, qty=qty or line.planned), "Complete production")

    def qc(self, batch, line, approved, held=0, rejected=0, kind="Final"):
        command = self.cmd(dto.QC, kind=kind, assessed=approved+held+rejected, sampleSize=1,
            approved=approved, held=held, rejected=rejected)
        return batches.line_action(self.db, self.tid, "inspector", batch.public_id, line.id, command, "QC")

    def receive(self, batch, line, qty):
        return batches.line_action(self.db, self.tid, "warehouse", batch.public_id, line.id,
            self.cmd(dto.Quantity, qty=qty, locationId=self.location.id), "Receive approved stock")

    def ship(self, allocations):
        result = batches.create_shipment(self.db, self.tid, "logistics", self.so.public_id, "sales",
            self.cmd(dto.CreateShipment, carrier="Carrier", destination="Customer", allocations=allocations))
        return result["publicId"]

    def lot(self, line):
        return self.db.scalar(select(BatchLot).where(BatchLot.line_id == line.id, BatchLot.location_id == self.location.id))

    def test_one_batch_split_and_one_shipment_combines_batches(self):
        a, al = self.plan(); self.produce(a, al); self.qc(a, al, 190, held=10); self.receive(a, al, 190)
        b, bl = self.plan(300, "Batch B"); self.produce(b, bl); self.qc(b, bl, 300); self.receive(b, bl, 300)
        first = self.ship([dict(lotId=self.lot(al).id, qty=100)])
        second = self.ship([dict(lotId=self.lot(al).id, qty=90), dict(lotId=self.lot(bl).id, qty=60)])
        self.assertEqual(self.so.status, "Awaiting stock")
        self.assertEqual(self.level.on_hand, 490)
        summary = batches.view(self.db, self.tid, self.so.public_id, "sales")["summary"]
        self.assertEqual((summary["allocated"], summary["dispatched"], summary["held"]), (250, 0, 10))
        batches.shipment_status(self.db, self.tid, first, "In transit")
        self.assertEqual(self.so.status, "Partially shipped")
        self.assertEqual(self.level.on_hand, 390)
        batches.shipment_status(self.db, self.tid, second, "In transit")
        self.assertEqual(self.level.on_hand, 240)
        summary = batches.view(self.db, self.tid, self.so.public_id, "sales")
        self.assertEqual((summary["summary"]["dispatched"], summary["summary"]["remaining"]), (250, 750))
        self.assertEqual([s["qty"] for s in summary["shipments"][1]["sources"]], [90, 60])
        self.assertEqual(summary["batches"][0]["summary"]["dispatched"], 190)
        self.assertEqual(fulfillment.stock_summary(self.db, self.so)[0]["missing"], 510)
        batches.shipment_status(self.db, self.tid, first, "Delivered")
        self.assertEqual(batches.view(self.db, self.tid, self.so.public_id, "sales")["summary"]["delivered"], 100)

    def test_hold_reinspection_and_scrap_replacement(self):
        batch, line = self.plan(1000); self.produce(batch, line)
        self.qc(batch, line, 900, held=80, rejected=20)
        with self.assertRaises(HTTPException): self.receive(batch, line, 901)
        self.qc(batch, line, 60, held=10, rejected=10, kind="Reinspection")
        self.assertEqual((line.approved, line.held, line.rejected), (960, 10, 30))
        replacement, repl = self.plan(30, "Replacement")
        self.assertEqual(repl.planned, 30)
        with self.assertRaises(HTTPException): self.plan(1)
        with self.assertRaises(HTTPException): self.qc(batch, line, 11, kind="Reinspection")

    def test_inline_qc_does_not_release_pieces(self):
        batch, line = self.plan()
        self.qc(batch, line, 190, held=10, kind="Inline")
        self.assertEqual((line.approved, line.held, line.completed), (0, 0, 0))
        with self.assertRaises(HTTPException): self.receive(batch, line, 1)

    def test_pending_cancel_releases_allocation_and_dispatch_is_idempotent(self):
        batch, line = self.plan(); self.produce(batch, line); self.qc(batch, line, 200); self.receive(batch, line, 200)
        lot = self.lot(line)
        ship = self.ship([dict(lotId=lot.id, qty=200)])
        with self.assertRaises(HTTPException): self.ship([dict(lotId=lot.id, qty=1)])
        batches.shipment_status(self.db, self.tid, ship, "Cancelled")
        self.assertEqual((lot.on_hand, lot.allocated, self.level.on_hand), (200, 0, 200))
        second = self.ship([dict(lotId=lot.id, qty=200)])
        with self.assertRaises(HTTPException): batches.shipment_status(self.db, self.tid, second, "Delivered")
        batches.shipment_status(self.db, self.tid, second, "In transit")
        batches.shipment_status(self.db, self.tid, second, "In transit")
        self.assertEqual(self.level.on_hand, 0)
        with self.assertRaises(HTTPException): batches.shipment_status(self.db, self.tid, second, "Cancelled")

    def test_batch_stock_transfer_retains_identity_and_order_reservation(self):
        batch, line = self.plan(); self.produce(batch, line); self.qc(batch, line, 200); self.receive(batch, line, 200)
        dest = Location(tenant_id=self.tid, name="Shop"); self.db.add(dest); self.db.flush()
        command = self.cmd(dto.Quantity, qty=80, locationId=dest.id)
        batches.transfer_lot(self.db, self.tid, "warehouse", batch.public_id, self.lot(line).id, command)
        dest_lot = self.db.scalar(select(BatchLot).where(BatchLot.location_id == dest.id))
        self.assertEqual(dest_lot.line_id, line.id)
        self.assertEqual((self.lot(line).on_hand, dest_lot.on_hand), (120, 80))
        level = self.db.scalar(select(StockLevel).where(StockLevel.location_id == dest.id))
        self.assertEqual((level.on_hand, level.reserved), (80, 80))
        self.assertEqual(sum(h.qty for h in fulfillment.holds_for(self.db, self.so)), 200)
        ship = self.ship([dict(lotId=dest_lot.id, qty=80)])
        with self.assertRaises(HTTPException):
            batches.transfer_lot(self.db, self.tid, "warehouse", batch.public_id, dest_lot.id,
                self.cmd(dto.Quantity, qty=1, locationId=self.location.id))
        batches.shipment_status(self.db, self.tid, ship, "In transit")
        self.assertEqual(level.on_hand, 0)

    def test_receipt_retry_does_not_duplicate_stock_or_audit(self):
        batch, line = self.plan(); self.produce(batch, line); self.qc(batch, line, 200)
        command = self.cmd(dto.Quantity, qty=100, locationId=self.location.id)
        for _ in range(2): batches.line_action(self.db, self.tid, "warehouse", batch.public_id, line.id, command, "Receive approved stock")
        self.assertEqual(self.level.on_hand, 100)
        self.assertEqual(line.received, 100)
        changed = command.model_copy(update={"qty": 101})
        with self.assertRaises(HTTPException): batches.line_action(self.db, self.tid, "warehouse", batch.public_id, line.id, changed, "Receive approved stock")

    def test_wrong_tenant_and_wrong_line_are_rejected(self):
        batch, line = self.plan()
        with self.assertRaises(HTTPException): batches.view(self.db, uuid.uuid4(), self.work.public_id)
        with self.assertRaises(HTTPException): batches.line_action(self.db, self.tid, "actor", batch.public_id, line.id+1,
            self.cmd(dto.Quantity, qty=1), "Complete production")

    def test_only_last_dispatch_completes_parent_order(self):
        batch, line = self.plan(1000); self.produce(batch, line); self.qc(batch, line, 1000); self.receive(batch, line, 1000)
        lot = self.lot(line)
        first = self.ship([dict(lotId=lot.id, qty=400)])
        second = self.ship([dict(lotId=lot.id, qty=600)])
        batches.shipment_status(self.db, self.tid, first, "In transit")
        self.assertEqual(self.so.status, "Partially shipped")
        batches.shipment_status(self.db, self.tid, second, "In transit")
        self.assertEqual(self.so.status, "Shipped")
        summary = batches.view(self.db, self.tid, self.so.public_id, "sales")["summary"]
        self.assertEqual((summary["remaining"], summary["delivered"]), (0, 0))

    def test_returned_dispatch_enters_qc_hold_before_reshipping(self):
        batch, line = self.plan(1000); self.produce(batch, line); self.qc(batch, line, 1000); self.receive(batch, line, 1000)
        ship = self.ship([dict(lotId=self.lot(line).id, qty=1000)])
        batches.shipment_status(self.db, self.tid, ship, "In transit")
        batches.shipment_status(self.db, self.tid, ship, "Delivered")
        batches.shipment_status(self.db, self.tid, ship, "Returned")
        self.assertEqual((line.approved, line.received, line.held), (0, 0, 1000))
        self.assertEqual((self.level.on_hand, self.lot(line).on_hand), (0, 0))
        self.assertEqual(self.so.status, "Awaiting stock")
        with self.assertRaises(HTTPException): self.receive(batch, line, 1)
        self.qc(batch, line, 1000, kind="Reinspection"); self.receive(batch, line, 1000)
        second = self.ship([dict(lotId=self.lot(line).id, qty=1000)])
        batches.shipment_status(self.db, self.tid, second, "In transit")
        self.assertEqual(self.so.status, "Shipped")
        self.assertEqual(batches.view(self.db, self.tid, self.so.public_id, "sales")["summary"]["dispatched"], 1000)

    def test_existing_reserved_stock_can_ship_with_new_batch_stock(self):
        # Fixture: 60 existing pieces and a PO for the remaining 940.
        self.level.on_hand = 60
        self.source.qty = 940; self.work.qty = 940; self.db.flush()
        fulfillment.reserve_available(self.db, self.so)
        batch, line = self.plan(940); self.produce(batch, line); self.qc(batch, line, 940); self.receive(batch, line, 940)
        sources = batches.inventory_sources(self.db, self.tid, self.so.id)
        self.assertEqual([s["available"] for s in sources], [60])
        command = self.cmd(dto.CreateShipment, carrier="Carrier", destination="Customer",
            allocations=[dict(lotId=self.lot(line).id, qty=940)],
            inventoryAllocations=[dict(reservationId=sources[0]["reservationId"], qty=60)])
        result = batches.create_shipment(self.db, self.tid, "logistics", self.so.public_id, "sales", command)
        view = batches.view(self.db, self.tid, self.so.public_id, "sales")
        self.assertEqual(view["summary"]["allocated"], 1000)
        self.assertEqual(view["shipments"][0]["qty"], 1000)
        self.assertEqual(len(view["shipments"][0]["sources"]), 2)
        self.assertIsNone(view["shipments"][0]["sources"][1]["batchId"])
        batches.shipment_status(self.db, self.tid, result["publicId"], "In transit")
        self.assertEqual((self.level.on_hand, self.level.reserved), (0, 0))
        self.assertEqual(self.so.status, "Shipped")
        self.assertEqual(batches.view(self.db, self.tid, self.so.public_id, "sales")["summary"]["remaining"], 0)

    def test_api_permissions_and_duplicate_dispatch_commands(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.core.security import get_principal, Principal
        from app.core.deps import tenant_db
        from app.modules.batches.router import router
        batch, line = self.plan(); self.produce(batch, line)
        app = FastAPI(); app.include_router(router)
        principal = Principal(user_id=None, email="qc@example.com", tenant_id=str(self.tid), permissions=["quality.read", "quality.write"])
        app.dependency_overrides[get_principal] = lambda: principal
        app.dependency_overrides[tenant_db] = lambda: self.db
        with TestClient(app) as client:
            self.assertEqual(client.get(f"/batches/{batch.public_id}/workspace").status_code, 200)
            self.assertEqual(client.get(f"/batches/orders/{self.work.public_id}").status_code, 200)
            command = self.cmd(dto.QC, kind="Final", assessed=200, sampleSize=10, approved=190, held=10, rejected=0)
            self.assertEqual(client.post(f"/batches/{batch.public_id}/lines/{line.id}/qc", json=command.model_dump(mode="json")).status_code, 200)
            self.assertEqual(client.post(f"/batches/{batch.public_id}/lines/{line.id}/receive", json=self.cmd(dto.Quantity, qty=190, locationId=self.location.id).model_dump(mode="json")).status_code, 403)
            principal.permissions = ["*"]
            self.receive(batch, line, 190)
            ship = self.ship([dict(lotId=self.lot(line).id, qty=100)])
            dispatch = self.cmd(dto.ShipmentStatus, status="In transit").model_dump(mode="json")
            for _ in range(2): self.assertEqual(client.post(f"/batches/shipments/{ship}/status", json=dispatch).status_code, 200)
            self.assertEqual(self.level.on_hand, 90)
            summary = client.get(f"/batches/shipments/{ship}").json()
            self.assertEqual(len(summary["history"]), 2)

    def test_cancelled_plan_restores_planning_quota_and_retains_history(self):
        batch, line = self.plan(1000)
        command = self.cmd(dto.Command)
        batches.cancel_batch(self.db, self.tid, "planner", batch.public_id, command)
        batches.cancel_batch(self.db, self.tid, "planner", batch.public_id, command)
        self.assertEqual(batch.status, "Cancelled")
        self.assertEqual(batches.view(self.db, self.tid, self.work.public_id)["sources"][0]["remainingToPlan"], 1000)
        replacement, repl = self.plan(1000, "Corrected plan")
        self.produce(replacement, repl)
        with self.assertRaises(HTTPException): batches.cancel_batch(self.db, self.tid, "planner", replacement.public_id, self.cmd(dto.Command))
        self.assertEqual(self.count(ProductionBatch), 2)

    def test_new_warehouse_stock_reserves_without_automatic_whole_order_shipment(self):
        self.plan(200)
        self.level.on_hand = 100
        self.db.flush()
        fulfillment.refresh_order(self.db, self.so)
        self.assertEqual(self.level.reserved, 100)
        self.assertEqual(batches.inventory_sources(self.db, self.tid, self.so.id)[0]["available"], 100)
        from app.models.shipments import Shipment
        self.assertEqual(self.count(Shipment), 0)
