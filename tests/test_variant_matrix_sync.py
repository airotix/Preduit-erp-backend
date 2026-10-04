"""Both public matrix paths share structure, identity and stock quantities."""
import unittest
import uuid
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select

import test_procurement_production as fixtures
from app.models.catalog import AttributeValue, Product, ProductVariant
from app.models.inventory import Location, StockLevel
from app.modules.catalog import service as catalog
from app.modules.inventory import service as inventory
from app.modules.inventory import repository as inv_repo
from app.modules.inventory.dto import MatrixUpdate


class VariantMatrixSyncTests(unittest.TestCase):
    STANDARD_SIZES = ["XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL", "5XL", "6XL", "7XL"]
    def setUp(self):
        fixtures.ProcurementProductionTests.setUp(self)
        self.product = Product(tenant_id=self.tid, title="Article", status="Active")
        self.location = Location(tenant_id=self.tid, name="Warehouse", code="WH", kind="Warehouse")
        self.db.add_all([self.product, self.location])
        self.db.flush()

    tearDown = fixtures.ProcurementProductionTests.tearDown

    def save(self, colors, source="catalog"):
        payload = MatrixUpdate(colors=colors, location=self.location.name if not self.location.is_deleted else None)
        fn = catalog.save_product_matrix if source == "catalog" else inventory.save_article_matrix
        return fn(self.db, tenant_id=self.tid, public_id=self.product.public_id, payload=payload)

    def row(self, name="Blue", sizes=("S", "M"), qty=0, **fields):
        return {"color": name, "hex": "#0000ff", "cells": [
            {"size": size, "qty": qty} for size in sizes], **fields}

    def assert_synced(self):
        cat = catalog.product_detail(self.db, public_id=self.product.public_id)["product"]
        inv = inventory.stock_article_detail(self.db, public_id=self.product.public_id)["stock"]
        self.assertEqual(cat["sizes"], inv["sizes"])
        self.assertEqual([(r["name"], r["hex"], [c["q"] for c in r["cells"]]) for r in cat["matrix"]],
                         [(r["name"], r["hex"], r["cells"]) for r in inv["colors"]])
        return inv

    def cells(self, sizes):
        inv = self.assert_synced()
        return [inv["colors"][0]["cells"][inv["sizes"].index(s)] for s in sizes]

    def test_empty_article_shows_standard_size_columns_in_both_modules(self):
        inv = self.assert_synced()
        self.assertEqual(inv["sizes"], self.STANDARD_SIZES)
        self.assertEqual(inv["colors"], [])

    def test_unassigned_article_requires_location_even_when_warehouse_exists(self):
        with self.assertRaises(HTTPException) as error:
            catalog.save_product_matrix(self.db, tenant_id=self.tid, public_id=self.product.public_id,
                                        payload=MatrixUpdate(colors=[self.row(qty=5)]))
        self.assertEqual(error.exception.detail, "Create an inventory location before adding stock.")
        self.assertEqual(len(self.db.scalars(select(ProductVariant)).all()), 0)

    def test_selecting_location_then_retrying_saves_all_quantities(self):
        self.save([self.row(qty=5)])
        levels = self.db.scalars(select(StockLevel)).all()
        self.assertTrue(all(level.location_id == self.location.id for level in levels))
        self.assertEqual(self.cells(("S", "M")), [5, 5])
        # Once assigned, subsequent quantity saves need no additional location.
        catalog.save_product_matrix(self.db, tenant_id=self.tid, public_id=self.product.public_id,
                                    payload=MatrixUpdate(colors=[self.row(qty=6)]))
        self.assertEqual(self.cells(("S", "M")), [6, 6])

    def test_catalog_add_and_inventory_updates_share_axes_and_stock(self):
        self.save([self.row(qty=7)])
        self.assertEqual(self.cells(("S", "M")), [7, 7])
        self.save([self.row(sizes=("S", "M", "L"), qty=10), self.row("Red", sizes=("S", "M", "L"))], "inventory")
        self.assertEqual(self.assert_synced()["sizes"], self.STANDARD_SIZES)

    def test_colour_and_size_rename_keep_variant_ids_skus_and_stock(self):
        self.save([self.row(sizes=("S",), qty=5)])
        variant = self.db.scalar(select(ProductVariant))
        original_id, sku = variant.id, variant.sku
        self.save([{"color": "Navy", "originalColor": "Blue", "hex": "#123456",
                    "cells": [{"size": "Small", "originalSize": "S", "qty": 5}]}], "inventory")
        self.assertEqual(variant.id, original_id)
        self.assertEqual(variant.sku, sku)
        self.assertEqual(self.assert_synced()["sizes"], self.STANDARD_SIZES + ["Small"])
        self.assertEqual(self.assert_synced()["colors"][0]["name"], "Navy")
        self.assertEqual(self.db.scalar(select(StockLevel)).variant_id, original_id)

    def test_delete_colour_and_size_hides_both_and_retains_history(self):
        self.save([self.row(), self.row("Red")])
        count = len(self.db.scalars(select(ProductVariant)).all())
        self.save([self.row(sizes=("S",))], "inventory")
        inv = self.assert_synced()
        self.assertEqual(inv["sizes"], self.STANDARD_SIZES)
        self.assertEqual([c["name"] for c in inv["colors"]], ["Blue"])
        self.assertEqual(len(self.db.scalars(select(ProductVariant)).all()), count)
        self.assertEqual(len(self.db.scalars(select(ProductVariant).where(ProductVariant.status == "Archived")).all()), 3)
        self.assertEqual(inv_repo.search_stock_products(self.db, q="Article")[0]["colors"],
                         [{"name": "Blue", "hex": "#0000ff"}])
        self.save([])
        self.assertEqual(self.assert_synced()["sizes"], self.STANDARD_SIZES)
        self.assertEqual(self.assert_synced()["colors"], [])

    def test_readding_empty_archived_variant_reuses_its_sku(self):
        self.save([self.row(sizes=("S",))])
        variant = self.db.scalar(select(ProductVariant))
        sku = variant.sku
        self.save([])
        self.save([self.row(sizes=("S",))], "inventory")
        self.assertEqual(variant.status, "Active")
        self.assertEqual(variant.sku, sku)
        self.assertEqual(len(self.db.scalars(select(ProductVariant)).all()), 1)
        self.assert_synced()

    def test_delete_with_stock_or_reservations_is_rejected(self):
        self.save([self.row(sizes=("S",), qty=5)])
        with self.assertRaises(HTTPException) as error:
            self.save([])
        self.assertEqual(error.exception.status_code, 409)
        self.db.scalar(select(StockLevel)).on_hand = 0
        self.db.scalar(select(StockLevel)).reserved = 2
        self.db.flush()
        with self.assertRaises(HTTPException):
            self.save([], "inventory")

    def test_multi_location_total_is_not_written_twice(self):
        self.save([self.row(sizes=("S",), qty=5)])
        variant = self.db.scalar(select(ProductVariant))
        second = Location(tenant_id=self.tid, name="Shop", code="SH", kind="Retail")
        self.db.add(second)
        self.db.flush()
        self.db.add(StockLevel(tenant_id=self.tid, variant_id=variant.id, location_id=second.id,
                               on_hand=4, reserved=2))
        self.db.flush()
        self.save([self.row(sizes=("S",), qty=9)])
        self.assertEqual(self.cells(("S",)), [9])
        self.save([self.row(sizes=("S",), qty=3)], "inventory")
        levels = self.db.scalars(select(StockLevel)).all()
        self.assertEqual(sum(l.on_hand for l in levels), 3)
        self.assertEqual(sum(l.reserved for l in levels), 2)
        self.assertTrue(all(l.on_hand >= l.reserved for l in levels))
        self.assert_synced()

    def test_stock_changes_are_visible_in_catalog_without_matrix_save(self):
        self.save([self.row(sizes=("S",))])
        variant = self.db.scalar(select(ProductVariant))
        inv_repo.adjust_stock(self.db, tenant_id=self.tid, variant_id=variant.id,
                              location_id=self.location.id, on_hand_delta=8)
        self.assertEqual(self.cells(("S",)), [8])

    def test_receiving_stock_restores_removed_variant_in_both_views(self):
        self.save([self.row(sizes=("S",))])
        variant = self.db.scalar(select(ProductVariant))
        self.save([])
        inv_repo.add_stock(self.db, tenant_id=self.tid, variant_id=variant.id,
                           location_id=self.location.id, qty=4)
        self.assertEqual(variant.status, "Active")
        self.assertEqual(self.cells(("S",)), [4])

    def test_zero_quantity_structure_syncs_without_a_location(self):
        self.location.is_deleted = True
        self.db.flush()
        self.save([self.row()])
        self.assertEqual(self.assert_synced()["sizes"], self.STANDARD_SIZES)
        self.assertEqual(self.cells(("S", "M")), [0, 0])
        rows, count = inv_repo.list_stock(self.db, limit=50, offset=0)
        self.assertEqual(count, 1)
        self.assertEqual(rows[0]["title"], "Article")

    def test_quantity_below_reservations_is_rejected(self):
        self.save([self.row(sizes=("S",), qty=5)])
        level = self.db.scalar(select(StockLevel))
        level.reserved = 4
        self.db.flush()
        with self.assertRaises(HTTPException) as error:
            self.save([self.row(sizes=("S",), qty=3)], "inventory")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(level.on_hand, 5)

    def test_renaming_one_article_does_not_rename_other_articles(self):
        self.save([self.row(sizes=("S",))])
        other = Product(tenant_id=self.tid, title="Other", status="Active")
        self.db.add(other)
        self.db.flush()
        catalog.save_product_matrix(self.db, tenant_id=self.tid, public_id=other.public_id,
                                    payload=MatrixUpdate(colors=[self.row(sizes=("S",))]))
        self.save([{"color": "Navy", "originalColor": "Blue", "cells": [{"size": "S", "qty": 0}]}])
        detail = catalog.product_detail(self.db, public_id=other.public_id)
        self.assertEqual(detail["product"]["matrix"][0]["name"], "Blue")

    def test_new_variants_preserve_channel_prices(self):
        self.save([self.row(sizes=("S",))])
        variant = self.db.scalar(select(ProductVariant))
        variant.retail_price, variant.wholesale_price = Decimal("20"), Decimal("12")
        variant.online_price, variant.supplier_price = Decimal("18"), Decimal("8")
        self.db.flush()
        self.save([self.row(sizes=("S", "L"))])
        other = self.db.scalars(select(ProductVariant).order_by(ProductVariant.id.desc())).first()
        self.assertEqual((other.retail_price, other.wholesale_price, other.online_price, other.supplier_price),
                         (Decimal("20"), Decimal("12"), Decimal("18"), Decimal("8")))

    def test_duplicate_names_and_tenant_mismatch_are_rejected(self):
        with self.assertRaises(HTTPException):
            self.save([self.row("Blue"), self.row(" blue ")])
        with self.assertRaises(HTTPException):
            self.save([self.row(sizes=("S", " s "))])
        result = catalog.save_product_matrix(self.db, tenant_id=uuid.uuid4(), public_id=self.product.public_id,
                                             payload=MatrixUpdate(colors=[self.row()]))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
