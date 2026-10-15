"""Catalog detail edits cannot overwrite independent variant prices."""
import unittest
from decimal import Decimal
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select

import test_procurement_production as fixtures
from app.models.catalog import AttributeValue, Category, Product, ProductVariant
from app.models.inventory import Location
from app.modules.catalog import service as catalog
from app.modules.catalog.dto import ProductDetailChanges, ProductEditorSave, ProductUpdate, VariantPricesUpdate
from app.modules.inventory.dto import MatrixUpdate


class CatalogVariantPricingTests(unittest.TestCase):
    def setUp(self):
        fixtures.ProcurementProductionTests.setUp(self)
        audit = patch("app.modules.catalog.service.write_audit")
        audit.start(); self.addCleanup(audit.stop)
        category = Category(tenant_id=self.tid, name="Shirts", is_active=True)
        color = AttributeValue(tenant_id=self.tid, attr_type="Color", value="Blue", code="BLUE")
        small = AttributeValue(tenant_id=self.tid, attr_type="Size", value="S", code="S")
        large = AttributeValue(tenant_id=self.tid, attr_type="Size", value="XL", code="XL")
        self.product = Product(tenant_id=self.tid, title="Shirt", category_id=None,
                               season="Core", status="Active")
        self.db.add_all([category, color, small, large, self.product,
                         Location(tenant_id=self.tid, name="Warehouse")])
        self.db.flush()
        self.product.category_id = category.id
        self.small = ProductVariant(tenant_id=self.tid, product_id=self.product.id,
            sku="SKU-000101", color_id=color.id, size_id=small.id, price=20,
            retail_price=20, wholesale_price=15, online_price=21, supplier_price=8,
            currency_code="EUR", status="Active")
        self.large = ProductVariant(tenant_id=self.tid, product_id=self.product.id,
            sku="SKU-000102", color_id=color.id, size_id=large.id, price=22,
            retail_price=22, wholesale_price=17, online_price=23, supplier_price=9,
            currency_code="EUR", status="Active")
        self.db.add_all([self.small, self.large]); self.db.commit()

    tearDown = fixtures.ProcurementProductionTests.tearDown

    def editor(self, *, title="Renamed shirt", status=None, sizes=("S", "XL")):
        details = {"title": title}
        if status is not None:
            details["status"] = status
        return ProductEditorSave(details=ProductDetailChanges(**details),
            matrix=MatrixUpdate(colors=[{"color": "Blue", "originalColor": "Blue",
                "cells": [{"size": size, "originalSize": size} for size in sizes]}]))

    def test_detail_and_matrix_save_preserves_different_size_prices(self):
        catalog.save_product_editor(self.db, tenant_id=self.tid,
            public_id=self.product.public_id, payload=self.editor())
        self.db.commit()
        variants = self.db.scalars(select(ProductVariant).order_by(ProductVariant.id)).all()
        self.assertEqual([v.retail_price for v in variants], [Decimal("20"), Decimal("22")])
        self.assertEqual([v.wholesale_price for v in variants], [Decimal("15"), Decimal("17")])
        self.assertEqual(self.db.get(Product, self.product.id).title, "Renamed shirt")

    def test_unsized_seed_sku_does_not_set_the_order_price(self):
        self.db.add(ProductVariant(tenant_id=self.tid, product_id=self.product.id,
            sku="SKU-000100", price=1, retail_price=1, wholesale_price=1,
            online_price=1, supplier_price=1, currency_code="EUR", status="Active"))
        self.db.flush()
        detail = catalog.product_detail(self.db, public_id=self.product.public_id)
        self.assertEqual(detail["product"]["form"]["retailPrice"], 20)
        self.assertEqual(len(detail["product"]["variants"]), 2)
        self.assertEqual(catalog.search_products(self.db, q="Shirt")[0]["prices"]["retail"], 20)
        rows, _ = catalog.repo.list_products(self.db, limit=10, offset=0)
        self.assertEqual(rows[0]["retail"], Decimal("20"))

    def test_failed_combined_save_rolls_back_details_and_matrix(self):
        self.product.status = "Draft"
        self.small.retail_price = None
        self.db.commit()
        with self.assertRaises(HTTPException):
            catalog.save_product_editor(self.db, tenant_id=self.tid,
                public_id=self.product.public_id, payload=self.editor(status="Active", sizes=("S",)))
        self.db.rollback()
        self.db.expire_all()
        self.assertEqual(self.db.get(Product, self.product.id).title, "Shirt")
        self.assertEqual(self.db.get(Product, self.product.id).status, "Draft")
        self.assertEqual(self.db.get(ProductVariant, self.large.id).status, "Active")

    def test_prices_change_only_for_selected_variant(self):
        catalog.update_variant_prices(self.db, tenant_id=self.tid,
            public_id=self.product.public_id, payload=VariantPricesUpdate(variants=[{
                "variantId": self.large.public_id, "retailPrice": "25.00",
                "wholesalePrice": "19.00", "onlinePrice": "26.00",
                "supplierPrice": "10.00"}]))
        self.db.flush()
        self.assertEqual(self.small.retail_price, Decimal("20"))
        self.assertEqual(self.large.retail_price, Decimal("25.00"))
        self.assertEqual(self.large.price, Decimal("25.00"))
        with self.assertRaises(HTTPException):
            catalog.update_variant_prices(self.db, tenant_id=self.tid,
                public_id=self.product.public_id, payload=VariantPricesUpdate(variants=[{
                    "variantId": self.small.public_id, "retailPrice": None,
                    "wholesalePrice": 15, "onlinePrice": 21, "supplierPrice": 8}]))

    def test_legacy_product_update_rejects_implicit_bulk_price_change(self):
        with self.assertRaises(HTTPException) as error:
            catalog.update_product(self.db, tenant_id=self.tid,
                public_id=self.product.public_id,
                payload=ProductUpdate(title="Changed", status="Active",
                    category="Shirts", season="Core", retailPrice=20))
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.large.retail_price, Decimal("22"))


if __name__ == "__main__":
    unittest.main()
