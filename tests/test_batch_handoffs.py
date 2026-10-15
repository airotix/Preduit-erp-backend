"""Shared batch identity, TNA scheduling and inventory only on accepted inbound delivery."""
from datetime import date, timedelta
from fastapi import HTTPException
from sqlalchemy import select
from app.models.batches import BatchStage, InboundAllocation, BatchEvent
from app.models.procurement import GoodsReceipt, PurchaseOrderLine
from app.models.production import ProductionOrderLine
from app.models.quality import Inspection
from app.modules.batches import service as batches, workflow, dto
from app.modules.quality import service as quality
import test_batch_execution as fixtures
import unittest


class BatchHandoffTests(unittest.TestCase):
    setUp = fixtures.BatchExecutionTests.setUp
    tearDown = fixtures.BatchExecutionTests.tearDown
    order = fixtures.BatchExecutionTests.order
    count = fixtures.BatchExecutionTests.count
    cmd = fixtures.BatchExecutionTests.cmd
    plan = fixtures.BatchExecutionTests.plan
    produce = fixtures.BatchExecutionTests.produce
    qc = fixtures.BatchExecutionTests.qc
    receive = fixtures.BatchExecutionTests.receive
    ship = fixtures.BatchExecutionTests.ship
    lot = fixtures.BatchExecutionTests.lot

    def inbound(self, line, qty):
        result = workflow.create_inbound(self.db, self.tid, "shipping", self.work.public_id,
            self.cmd(dto.CreateInbound, carrier="Carrier", allocations=[dict(lineId=line.id, locationId=self.location.id, qty=qty)]))
        return batches.one(self.db, batches.Shipment, self.tid, result["publicId"])

    def deliver(self, ship, accepted=None):
        workflow.inbound_status(self.db, self.tid, "shipping", ship, self.cmd(dto.ShipmentStatus, status="In transit"))
        allocations = batches.rows(self.db, InboundAllocation, self.tid, InboundAllocation.shipment_id == ship.id)
        command = self.cmd(dto.ShipmentStatus, status="Delivered", accepted=[dict(allocationId=a.id, qty=a.qty if accepted is None else accepted) for a in allocations])
        workflow.inbound_status(self.db, self.tid, "warehouse", ship, command)
        return command

    def test_separate_batches_keep_one_po_and_independent_delivery(self):
        a, al = self.plan(400); b, bl = self.plan(600, "Batch B")
        self.produce(a, al); self.qc(a, al, 400)
        ship = self.inbound(al, 400)
        self.assertEqual((self.level.on_hand, al.received), (0, 0))
        command = self.deliver(ship)
        self.assertEqual((self.level.on_hand, self.level.reserved, al.received, bl.received), (400, 400, 400, 0))
        self.assertEqual(self.po.status, "Partially received")
        self.assertFalse(self.po.inventory_received)
        self.assertEqual(self.so.status, "Awaiting stock")
        workflow.inbound_status(self.db, self.tid, "warehouse", ship, command)
        self.assertEqual(self.level.on_hand, 400)
        for kind, order_id in [("production", self.work.public_id), ("sales", self.so.public_id), ("procurement", self.po.public_id)]:
            data = batches.view(self.db, self.tid, order_id, kind)
            self.assertEqual({batch["publicId"] for batch in data["batches"]}, {str(a.public_id), str(b.public_id)})
            source = next(s for s in data["shipments"] if s["publicId"] == str(ship.public_id))["sources"][0]
            self.assertEqual(source["batchId"], str(a.public_id))
        self.produce(b, bl); self.qc(b, bl, 600); self.deliver(self.inbound(bl, 600))
        self.assertEqual((self.level.on_hand, self.po.status, self.po.inventory_received), (1000, "Received", True))

    def test_partial_approval_split_inbound_and_cancel_release(self):
        batch, line = self.plan(500); self.produce(batch, line); self.qc(batch, line, 300, held=200)
        ship = self.inbound(line, 200)
        self.assertEqual(workflow.ready(self.db, self.tid, line), 100)
        with self.assertRaises(HTTPException): self.inbound(line, 101)
        workflow.inbound_status(self.db, self.tid, "shipping", ship, self.cmd(dto.ShipmentStatus, status="Cancelled"))
        self.assertEqual(workflow.ready(self.db, self.tid, line), 300)
        self.deliver(self.inbound(line, 150))
        self.assertEqual((line.received, line.held, workflow.ready(self.db, self.tid, line)), (150, 200, 150))

    def test_short_delivery_holds_discrepancy_and_never_overcredits(self):
        batch, line = self.plan(); self.produce(batch, line); self.qc(batch, line, 200)
        ship = self.inbound(line, 200)
        self.deliver(ship, 180)
        self.assertEqual((line.approved, line.held, line.received, self.level.on_hand), (180, 20, 180, 180))
        self.assertEqual(workflow.ready(self.db, self.tid, line), 0)
        self.assertEqual(self.count(GoodsReceipt), 1)
        with self.assertRaises(HTTPException): workflow.inbound_status(self.db, self.tid, "warehouse", ship, self.cmd(dto.ShipmentStatus, status="Delivered"))

    def test_no_direct_receipt_or_pre_qc_shipment(self):
        batch, line = self.plan()
        with self.assertRaises(HTTPException): self.inbound(line, 1)
        with self.assertRaises(HTTPException): self.qc(batch, line, 1)
        self.produce(batch, line)
        with self.assertRaises(HTTPException): self.inbound(line, 1)
        self.qc(batch, line, 200)
        with self.assertRaises(HTTPException): batches.line_action(self.db, self.tid, "warehouse", batch.public_id, line.id,
            self.cmd(dto.Quantity, qty=1, locationId=self.location.id), "Receive approved stock")
        self.assertEqual(self.level.on_hand, 0)

    def test_tna_extension_moves_dependencies_and_actual_dates(self):
        batch, line = self.plan()
        stages = batches.rows(self.db, BatchStage, self.tid, BatchStage.batch_id == batch.id)
        old_end = stages[0].end_on
        workflow.schedule(self.db, self.tid, "planner", batch.public_id, stages[0].id, self.cmd(dto.ExtendStage, days=3), extend=True)
        self.assertEqual(stages[0].end_on, old_end + timedelta(days=3))
        self.assertEqual(stages[1].start_on, stages[0].end_on + timedelta(days=1))
        with self.assertRaises(HTTPException): batches.stage(self.db, self.tid, "worker", batch.public_id, stages[1].id, self.cmd(dto.StageCommand, status="Completed"))
        self.produce(batch, line)
        self.assertTrue(all(s.actual_start_on == date.today() and s.actual_end_on == date.today() for s in stages))
        with self.assertRaises(HTTPException): workflow.schedule(self.db, self.tid, "planner", batch.public_id, stages[0].id, self.cmd(dto.ExtendStage, days=1), extend=True)

    def test_legacy_unscheduled_tna_can_be_planned_without_resetting_batch(self):
        batch, line = self.plan()
        stages = batches.rows(self.db, BatchStage, self.tid, BatchStage.batch_id == batch.id)
        for stage in stages: stage.start_on = stage.end_on = None
        with self.assertRaises(HTTPException): batches.stage(self.db, self.tid, "worker", batch.public_id, stages[0].id, self.cmd(dto.StageCommand, status="In progress"))
        workflow.schedule(self.db, self.tid, "planner", batch.public_id, stages[0].id,
            self.cmd(dto.StageSchedule, startOn=date.today()-timedelta(days=5), durationDays=2, worker="Team"))
        view = batches.view(self.db, self.tid, self.work.public_id)
        self.assertTrue(view["batches"][0]["stages"][0]["overdue"])
        self.assertEqual(view["batches"][0]["publicId"], str(batch.public_id))

    def test_each_inspection_has_its_own_id_under_same_batch(self):
        batch, line = self.plan(); self.produce(batch, line)
        first = self.qc(batch, line, 100, held=100)
        second = self.qc(batch, line, 100, kind="Reinspection")
        self.assertNotEqual(first["inspectionId"], second["inspectionId"])
        history = batches.view(self.db, self.tid, self.work.public_id)["batches"][0]["history"]
        self.assertEqual({h["inspectionId"] for h in history if h["kind"] == "QC"}, {first["inspectionId"], second["inspectionId"]})
        recorded = self.db.scalars(select(Inspection).where(Inspection.batch_line_id == line.id)).all()
        self.assertEqual({str(inspection.public_id) for inspection in recorded}, {first["inspectionId"], second["inspectionId"]})
        self.assertEqual(sum(inspection.approved_qty or 0 for inspection in recorded), line.approved)

    def test_each_style_in_one_batch_has_independent_production_timeline(self):
        other_style = ProductionOrderLine(tenant_id=self.tid, order_id=self.work.id,
            name="Jacket", qty=50, sku=self.source.sku)
        self.db.add(other_style); self.db.flush()
        other_source = PurchaseOrderLine(tenant_id=self.tid, po_id=self.po.id,
            production_line_id=other_style.id, name="Jacket", color=self.source.color,
            size=self.source.size, sku=self.source.sku, qty=50, price=self.source.price,
            line_total=self.source.price * 50)
        self.db.add(other_source); self.db.flush()
        self.work.qty += 50
        command = self.cmd(dto.CreateBatch, name="Mixed batch", stages=["Cutting", "Packed"],
            lines=[dict(sourceId=self.source.id, qty=50), dict(sourceId=other_source.id, qty=50)])
        result = batches.create_batch(self.db, self.tid, "planner", self.work.public_id, command)
        batch = batches.one(self.db, batches.ProductionBatch, self.tid, result["publicId"])
        stages = batches.rows(self.db, BatchStage, self.tid, BatchStage.batch_id == batch.id)
        self.assertEqual(len(stages), 4)
        first = [stage for stage in stages if stage.production_line_id == self.source.production_line_id]
        second = [stage for stage in stages if stage.production_line_id == other_style.id]
        for stage in first:
            batches.stage(self.db, self.tid, "worker", batch.public_id, stage.id,
                self.cmd(dto.StageCommand, status="Completed"))
        lines = batches.rows(self.db, batches.BatchLine, self.tid, batches.BatchLine.batch_id == batch.id)
        first_line = next(line for line in lines if line.po_line_id == self.source.id)
        second_line = next(line for line in lines if line.po_line_id == other_source.id)
        self.assertTrue(workflow.style_complete(self.db, self.tid, first_line))
        self.assertFalse(workflow.style_complete(self.db, self.tid, second_line))
        batches.line_action(self.db, self.tid, "worker", batch.public_id, first_line.id,
            self.cmd(dto.Quantity, qty=50), "Complete production")
        with self.assertRaises(HTTPException):
            batches.line_action(self.db, self.tid, "worker", batch.public_id, second_line.id,
                self.cmd(dto.Quantity, qty=1), "Complete production")

    def test_batch_style_uses_normal_start_and_stage_controls(self):
        batch, line = self.plan()
        style_id = self.source.production_line_id
        stages = batches.rows(self.db, BatchStage, self.tid, BatchStage.batch_id == batch.id)
        batches.start_style(self.db, self.tid, "planner", batch.public_id,
            self.cmd(dto.StartBatchStyle, productionLineId=style_id,
                stages=[{"name": "Cutting", "days": 3}, {"name": "Packing", "days": 2}]))
        self.assertEqual((stages[0].status, stages[1].status), ("In progress", "Pending"))
        batches.stage_action(self.db, self.tid, "planner", batch.public_id, stages[0].id,
            "assign", self.cmd(dto.BatchStageAction, worker="Cutting team"))
        self.assertEqual(stages[0].worker, "Cutting team")
        old_start = stages[1].start_on
        batches.stage_action(self.db, self.tid, "planner", batch.public_id, stages[0].id,
            "extend", self.cmd(dto.BatchStageAction, days=2))
        self.assertEqual(stages[1].start_on, old_start + timedelta(days=2))
        batches.stage_action(self.db, self.tid, "worker", batch.public_id, stages[0].id,
            "complete", self.cmd(dto.BatchStageAction))
        batches.stage_action(self.db, self.tid, "worker", batch.public_id, stages[1].id,
            "complete", self.cmd(dto.BatchStageAction))
        batches.line_action(self.db, self.tid, "worker", batch.public_id, line.id,
            self.cmd(dto.Quantity, qty=line.planned), "Complete production")
        self.assertEqual(self.work.progress, round(100 * line.planned / self.work.qty))

    def test_batch_qc_is_a_quality_record_without_shipping_action(self):
        batch, line = self.plan(); self.produce(batch, line)
        recorded = self.qc(batch, line, 150, held=50)
        detail = quality.inspection_detail(self.db, public_id=recorded["inspectionId"])
        self.assertTrue(detail["batchManaged"])
        self.assertEqual(detail["batchId"], str(batch.public_id))
        self.assertEqual(detail["meta"][2]["v"], "150")
        self.assertEqual(self.count(batches.Shipment), 0)
        with self.assertRaises(HTTPException):
            quality.pass_and_ship(self.db, tenant_id=self.tid,
                public_id=recorded["inspectionId"], carrier="Carrier", destination="Warehouse")

    def test_quality_lists_batches_separately_under_one_order(self):
        first, first_line = self.plan(100, "B1")
        second, second_line = self.plan(100, "B2")
        self.produce(first, first_line); self.produce(second, second_line)
        first_inspection = self.qc(first, first_line, 100)
        self.qc(second, second_line, 100)
        screen = quality.inspections_screen(self.db)
        self.assertEqual(screen["total"], 2)
        detail = quality.inspection_detail(self.db, public_id=first_inspection["inspectionId"])
        self.assertEqual(detail["batchId"], str(first.public_id))
        self.assertEqual(len(detail["inspection"]["items"]), 1)

    def test_api_module_permissions_and_delivery_retry(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.core.security import get_principal, Principal
        from app.core.deps import tenant_db
        from app.modules.batches.router import router
        batch, line = self.plan(); self.produce(batch, line); self.qc(batch, line, 200)
        app = FastAPI(); app.include_router(router)
        principal = Principal(user_id=None, email="shipping@example.com", tenant_id=str(self.tid), permissions=["shipments.read", "shipments.write"])
        app.dependency_overrides[get_principal] = lambda: principal
        app.dependency_overrides[tenant_db] = lambda: self.db
        with TestClient(app) as client:
            self.assertEqual(client.get("/batches/work/quality").status_code, 403)
            self.assertEqual(client.get("/batches/work/shipments").status_code, 200)
            self.assertEqual(client.get(f"/batches/orders/{self.so.public_id}?kind=sales").status_code, 200)
            self.assertEqual(client.post(f"/batches/{batch.public_id}/lines/{line.id}/qc", json=self.cmd(dto.QC, kind="Inline", assessed=1, sampleSize=1, approved=1, held=0, rejected=0).model_dump(mode="json")).status_code, 403)
            plan = self.cmd(dto.CreateInbound, carrier="Carrier", allocations=[dict(lineId=line.id, qty=200, locationId=self.location.id)])
            response = client.post(f"/batches/orders/{self.work.public_id}/inbound-shipments", json=plan.model_dump(mode="json"))
            self.assertEqual(response.status_code, 201)
            ship_id = response.json()["publicId"]
            self.assertEqual(client.get(f"/batches/shipments/{ship_id}").json()["sources"][0]["batchId"], str(batch.public_id))
            self.assertEqual(client.post(f"/batches/shipments/{ship_id}/status", json=self.cmd(dto.ShipmentStatus, status="In transit").model_dump(mode="json")).status_code, 200)
            a = self.db.scalar(select(InboundAllocation))
            delivery = self.cmd(dto.ShipmentStatus, status="Delivered", accepted=[dict(allocationId=a.id, qty=200)]).model_dump(mode="json")
            self.assertEqual(client.post(f"/batches/shipments/{ship_id}/status", json=delivery).status_code, 403)
            # A warehouse operator can receive without shipment editing permission.
            principal.permissions = ["inventory.read", "inventory.write"]
            self.assertEqual(client.get("/batches/work/inventory").status_code, 200)
            self.assertEqual(client.post(f"/batches/shipments/{ship_id}/status", json=self.cmd(dto.ShipmentStatus, status="Customs").model_dump(mode="json")).status_code, 403)
            for _ in range(2): self.assertEqual(client.post(f"/batches/shipments/{ship_id}/status", json=delivery).status_code, 200)
            self.assertEqual((self.level.on_hand, line.received, self.count(GoodsReceipt)), (200, 200, 1))

    def test_all_modules_retain_the_same_batch_after_completion(self):
        batch, line = self.plan(); self.produce(batch, line); self.qc(batch, line, 200)
        self.deliver(self.inbound(line, 200))
        for kind in ("production", "quality", "shipments", "inventory"):
            queue = batches.work_list(self.db, self.tid, kind)["batches"]
            self.assertEqual(queue[0]["publicId"], str(batch.public_id))
            self.assertEqual(queue[0]["productionStatus"], "Complete")
            self.assertEqual(queue[0]["qualityStatus"], "QC complete")
            self.assertEqual(queue[0]["shipmentStatus"], "Delivered to warehouse")
