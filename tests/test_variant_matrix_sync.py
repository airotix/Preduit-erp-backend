"""Catalog defines variants; Inventory posts physical quantities."""
import unittest
import uuid
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select

import test_procurement_production as fixtures
from app.models.catalog import Product, ProductVariant
from app.models.inventory import Location, StockEntry, StockLevel
from app.modules.catalog import service as catalog
from app.modules.inventory import service as inventory
from app.modules.inventory.dto import MatrixUpdate, StockAdjustmentCreate, StockReceiptCreate


class VariantMatrixSyncTests(unittest.TestCase):
    def setUp(self):
        fixtures.ProcurementProductionTests.setUp(self)
        audit = patch("app.core.audit.write_audit")
        audit.start(); self.addCleanup(audit.stop)
        self.product = Product(tenant_id=self.tid, title="Article", status="Active")
        self.location = Location(tenant_id=self.tid, name="Warehouse", code="WH", kind="Warehouse")
        self.db.add_all([self.product, self.location]); self.db.flush()

    tearDown = fixtures.ProcurementProductionTests.tearDown

    def save(self, qty=0, *, source="catalog", color="Blue", size="S"):
        fn = catalog.save_product_matrix if source == "catalog" else inventory.save_article_matrix
        return fn(self.db, tenant_id=self.tid, public_id=self.product.public_id,
            payload=MatrixUpdate(colors=[{"color": color, "cells": [{"size": size, "qty": qty}]}]))

    def receive(self, qty, *, request_id=None):
        return inventory.create_stock_receipt(self.db, tenant_id=self.tid,
            payload=StockReceiptCreate(location="Warehouse", reference="RC-001", reason="Goods received",
                requestId=request_id or uuid.uuid4(),
                lines=[{"name": "Article", "color": "Blue", "size": "S", "qty": qty}]),
            actor="tester@example.com")

    def test_structure_saves_without_a_location_or_quantity(self):
        self.location.is_deleted = True; self.db.flush()
        self.save()
        self.assertEqual(len(self.db.scalars(select(ProductVariant)).all()), 1)
        self.assertEqual(self.db.scalars(select(StockLevel)).all(), [])

    def test_matrix_quantity_edits_are_rejected_in_both_modules(self):
        for source in ("catalog", "inventory"):
            with self.assertRaises(HTTPException) as error:
                self.save(5, source=source)
            self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.db.scalars(select(StockLevel)).all(), [])

    def test_receipt_requires_saved_variant(self):
        with self.assertRaises(HTTPException) as error:
            self.receive(4)
        self.assertEqual(error.exception.status_code, 409)
        self.save(); self.receive(4)
        self.assertEqual(self.db.scalar(select(StockLevel)).on_hand, 4)

    def test_receipt_is_traceable_idempotent_and_visible_in_catalog(self):
        self.save()
        request_id = uuid.uuid4()
        result = self.receive(7, request_id=request_id)
        self.assertEqual(self.receive(7, request_id=request_id), result)
        self.assertEqual(self.db.scalar(select(StockLevel)).on_hand, 7)
        entry = self.db.scalar(select(StockEntry))
        self.assertEqual((entry.kind, entry.qty_delta, entry.before_qty, entry.after_qty),
            ("Receipt", 7, 0, 7))
        self.assertEqual(entry.reference, "RC-001")
        product = catalog.product_detail(self.db, public_id=self.product.public_id)["product"]
        self.assertEqual(product["matrix"][0]["cells"][product["sizes"].index("S")]["q"], 7)
        history = inventory.stock_entries(self.db, tenant_id=self.tid, public_id=self.product.public_id)
        self.assertEqual(history[0]["actor"], "tester@example.com")

    def test_count_correction_respects_reservations_and_records_delta(self):
        self.save(); self.receive(10)
        variant = self.db.scalar(select(ProductVariant)); level = self.db.scalar(select(StockLevel))
        level.reserved = 4; self.db.flush()
        with self.assertRaises(HTTPException) as error:
            inventory.adjust_stock_count(self.db, tenant_id=self.tid,
                payload=StockAdjustmentCreate(variantId=variant.id, location="Warehouse",
                    countedQty=3, reference="CC-001", reason="Physical count"))
        self.assertEqual(error.exception.status_code, 409)
        result = inventory.adjust_stock_count(self.db, tenant_id=self.tid,
            payload=StockAdjustmentCreate(variantId=variant.id, location="Warehouse",
                countedQty=6, reference="CC-002", reason="Physical count"))
        self.assertEqual((result["delta"], level.on_hand), (-4, 6))
        entry = self.db.scalars(select(StockEntry).where(StockEntry.kind == "Adjustment")).one()
        self.assertEqual((entry.before_qty, entry.after_qty, entry.qty_delta), (10, 6, -4))

    def test_variant_with_stock_cannot_be_removed(self):
        self.save(); self.receive(5)
        with self.assertRaises(HTTPException) as error:
            catalog.save_product_matrix(self.db, tenant_id=self.tid, public_id=self.product.public_id,
                payload=MatrixUpdate(colors=[]))
        self.assertEqual(error.exception.status_code, 409)

    def test_multi_location_total_is_not_redistributed(self):
        self.save(); self.receive(5)
        variant = self.db.scalar(select(ProductVariant))
        shop = Location(tenant_id=self.tid, name="Shop", code="SH", kind="Retail")
        self.db.add(shop); self.db.flush()
        self.db.add(StockLevel(tenant_id=self.tid, variant_id=variant.id,
            location_id=shop.id, on_hand=4, reserved=2)); self.db.flush()
        self.save(9, source="inventory")
        with self.assertRaises(HTTPException): self.save(3)
        self.assertEqual(sum(x.on_hand for x in self.db.scalars(select(StockLevel)).all()), 9)


if __name__ == "__main__":
    unittest.main()
