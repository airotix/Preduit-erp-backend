"""Product release status controls new commitments without breaking old ones."""
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select

import test_sales_fulfillment as fixtures
from app.models.catalog import Category
from app.models.procurement import PurchaseOrder
from app.models.sales import SalesOrder
from app.modules.catalog import service as catalog
from app.modules.catalog.dto import ProductCreate, ProductUpdate
from app.modules.inventory import service as inventory
from app.modules.inventory.dto import StockReceiptCreate
from app.modules.procurement import service as procurement
from app.modules.procurement.dto import PurchaseOrderCreate
from app.modules.sales import fulfillment, service as sales
from app.modules.sales.dto import OrderCreate


class CatalogStatusGateTests(unittest.TestCase):
    def setUp(self):
        fixtures.SalesFulfillmentTests.setUp(self)
        audit = patch("app.modules.catalog.service.write_audit")
        audit.start(); self.addCleanup(audit.stop)
    tearDown = fixtures.SalesFulfillmentTests.tearDown
    order = fixtures.SalesFulfillmentTests.order

    def line(self):
        return dict(name="Shirt", color="Blue", size="S", sku=self.variant.sku,
                    qty=1, price=20)

    def update(self, status, *, category="Shirts", season="Core"):
        return catalog.update_product(self.db, tenant_id=self.tid,
            public_id=self.product.public_id,
            payload=ProductUpdate(title="Shirt", status=status, category=category,
                season=season))

    def test_new_article_must_begin_as_draft(self):
        with self.assertRaises(HTTPException) as error:
            catalog.create_product(self.db, tenant_id=self.tid,
                payload=ProductCreate(title="New", sku="SKU-000099", status="Active"))
        self.assertEqual(error.exception.status_code, 409)

    def test_activation_requires_release_data(self):
        self.product.status = "Draft"; self.db.flush()
        with self.assertRaises(HTTPException) as error:
            self.update("Active")
        self.assertIn("active category", error.exception.detail)
        self.assertEqual(self.product.status, "Draft")
        category = Category(tenant_id=self.tid, name="Shirts", is_active=False)
        self.db.add(category); self.db.flush()
        with self.assertRaises(HTTPException): self.update("Active")
        category.is_active = True; self.db.flush()
        with self.assertRaises(HTTPException) as error:
            self.update("Active", season="")
        self.assertIn("season", error.exception.detail)
        with self.assertRaises(HTTPException) as error:
            self.update("Active")
        self.assertIn("positive", error.exception.detail)
        self.variant.retail_price = 20
        self.variant.wholesale_price = 15
        self.variant.online_price = 22
        self.db.flush()
        self.update("Active")
        self.assertEqual(self.product.status, "Active")
        self.assertEqual(catalog.search_products(self.db, q="Shirt")[0]["name"], "Shirt")

    def test_colour_size_variant_is_required_for_activation(self):
        self.product.status = "Draft"
        self.variant.color_id = None
        self.db.flush()
        with self.assertRaises(HTTPException) as error:
            self.update("Active")
        self.assertIn("colour and size", error.exception.detail)

    def test_draft_and_discontinued_are_hidden_and_rejected_for_new_orders(self):
        for status in ("Draft", "Discontinued"):
            self.product.status = status; self.db.flush()
            self.assertEqual(catalog.search_products(self.db, q="Shirt"), [])
            with self.assertRaises(HTTPException) as error:
                sales.create_order(self.db, tenant_id=self.tid,
                    payload=OrderCreate(customer="Customer", channel="Retail",
                        lines=[{**self.line(), "sku": None, "name": "shirt"}]))
            self.assertIn(status, error.exception.detail)
            with self.assertRaises(HTTPException):
                procurement.create_po(self.db, tenant_id=self.tid,
                    payload=PurchaseOrderCreate(supplier="Factory", expected="20 Oct 2026",
                        lines=[{**self.line(), "sku": None}]))
            self.assertEqual(self.db.scalars(select(SalesOrder)).all(), [])
            self.assertEqual(self.db.scalars(select(PurchaseOrder)).all(), [])

    def test_custom_line_still_allowed_when_it_does_not_match_a_catalog_article(self):
        self.product.status = "Discontinued"; self.db.flush()
        with patch("app.modules.sales.service.build_sales_invoice_draft", return_value=None):
            result = sales.create_order(self.db, tenant_id=self.tid,
                payload=OrderCreate(customer="Customer", channel="Retail",
                    lines=[{**self.line(), "name": "Custom service", "sku": None, "price": 0}]))
        self.assertIsNotNone(result["order"].id)

    def test_discontinuing_article_keeps_existing_order_link(self):
        order = self.order(qty=100)
        self.product.status = "Discontinued"; self.db.flush()
        line = fulfillment.lines_for(self.db, order)[0]
        self.assertEqual(fulfillment.variant_for(self.db, order, line).id, self.variant.id)
        inventory.create_stock_receipt(self.db, tenant_id=self.tid,
            payload=StockReceiptCreate(location="Warehouse", reference="RC-STATUS",
                reason="Complete existing order", lines=[dict(name="Shirt", color="Blue", size="S", qty=40)]))
        self.assertEqual(order.status, "Packed")
        with self.assertRaises(HTTPException):
            sales.create_order(self.db, tenant_id=self.tid,
                payload=OrderCreate(customer="New customer", channel="Retail", lines=[self.line()]))


if __name__ == "__main__":
    unittest.main()
