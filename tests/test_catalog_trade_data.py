"""GTIN identity and safe colour/size maintenance."""
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select

import test_procurement_production as fixtures
from app.models.catalog import AttributeValue, Product, ProductVariant
from app.modules.catalog import service as catalog
from app.modules.catalog.dto import AttributeUpdate, VariantGtinsUpdate


class CatalogTradeDataTests(unittest.TestCase):
    def setUp(self):
        fixtures.ProcurementProductionTests.setUp(self)
        audit = patch("app.modules.catalog.service.write_audit")
        audit.start(); self.addCleanup(audit.stop)
        self.product = Product(tenant_id=self.tid, title="Shirt", status="Draft")
        self.color = AttributeValue(tenant_id=self.tid, attr_type="Color", value="Blue", code="BLU")
        self.size = AttributeValue(tenant_id=self.tid, attr_type="Size", value="S", code="S")
        self.other_color = AttributeValue(tenant_id=self.tid, attr_type="Color", value="Red", code="RED")
        self.db.add_all([self.product, self.color, self.size, self.other_color]); self.db.flush()
        self.variant = ProductVariant(tenant_id=self.tid, product_id=self.product.id,
            color_id=self.color.id, size_id=self.size.id, sku="SKU-000201", price=10,
            currency_code="EUR", status="Active")
        self.db.add(self.variant); self.db.flush()

    tearDown = fixtures.ProcurementProductionTests.tearDown

    def gtin(self, variant, value):
        return catalog.update_variant_gtins(self.db, tenant_id=self.tid,
            public_id=self.product.public_id,
            payload=VariantGtinsUpdate(variants=[{"variantId": variant.public_id, "gtin": value}]))

    def test_valid_optional_gtin_is_visible_and_can_be_cleared(self):
        self.gtin(self.variant, "4006381333931")
        self.assertEqual(self.variant.barcode, "4006381333931")
        detail = catalog.product_detail(self.db, public_id=self.product.public_id)
        self.assertEqual(detail["product"]["variants"][0]["gtin"], "4006381333931")
        self.gtin(self.variant, None)
        self.assertIsNone(self.variant.barcode)

    def test_bad_check_digit_and_duplicate_gtin_are_rejected(self):
        with self.assertRaises(HTTPException) as error:
            self.gtin(self.variant, "4006381333932")
        self.assertEqual(error.exception.status_code, 422)
        self.gtin(self.variant, "4006381333931")
        other = ProductVariant(tenant_id=self.tid, product_id=self.product.id,
            color_id=self.other_color.id, size_id=self.size.id, sku="SKU-000202",
            price=10, currency_code="EUR", status="Active")
        self.db.add(other); self.db.flush()
        with self.assertRaises(HTTPException) as error:
            self.gtin(other, "4006381333931")
        self.assertEqual(error.exception.status_code, 409)

    def test_two_variants_can_exchange_gtins(self):
        other = ProductVariant(tenant_id=self.tid, product_id=self.product.id,
            color_id=self.other_color.id, size_id=self.size.id, sku="SKU-000202",
            price=10, currency_code="EUR", status="Active")
        self.db.add(other); self.db.flush()
        self.gtin(self.variant, "4006381333931")
        self.gtin(other, "036000291452")
        catalog.update_variant_gtins(self.db, tenant_id=self.tid,
            public_id=self.product.public_id, payload=VariantGtinsUpdate(variants=[
                {"variantId": self.variant.public_id, "gtin": "036000291452"},
                {"variantId": other.public_id, "gtin": "4006381333931"},
            ]))
        self.assertEqual(self.variant.barcode, "036000291452")
        self.assertEqual(other.barcode, "4006381333931")

    def test_used_attribute_rename_preserves_variant_and_type_change_is_blocked(self):
        catalog.update_attribute(self.db, tenant_id=self.tid,
            public_id=self.color.public_id,
            payload=AttributeUpdate(value="Navy", type="Color", code="NAV"))
        self.assertEqual(self.variant.color_id, self.color.id)
        self.assertEqual(self.db.get(AttributeValue, self.color.id).value, "Navy")
        with self.assertRaises(HTTPException) as error:
            catalog.update_attribute(self.db, tenant_id=self.tid,
                public_id=self.color.public_id,
                payload=AttributeUpdate(value="Navy", type="Size", code="NAV"))
        self.assertEqual(error.exception.status_code, 409)
        with self.assertRaises(HTTPException):
            catalog.update_attribute(self.db, tenant_id=self.tid,
                public_id=self.color.public_id,
                payload=AttributeUpdate(value="red", type="Color", code="NAV"))


if __name__ == "__main__":
    unittest.main()
