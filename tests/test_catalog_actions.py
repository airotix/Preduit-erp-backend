"""Catalog deletion protects linked data and SKU validation covers both writes."""
import unittest
import uuid
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

import test_procurement_production as fixtures
from app.models.catalog import Category, Product
from app.modules.catalog import service
from app.modules.catalog.dto import ProductCreate, ProductUpdate


class CatalogActionTests(unittest.TestCase):
    setUp = fixtures.ProcurementProductionTests.setUp
    tearDown = fixtures.ProcurementProductionTests.tearDown

    def category(self, **values):
        category = Category(tenant_id=values.pop("tenant_id", self.tid),
                            name=values.pop("name", "Knitwear"), **values)
        self.db.add(category)
        self.db.flush()
        return category

    def test_empty_category_deleted_and_audited(self):
        category = self.category()
        public_id = category.public_id
        with patch("app.modules.catalog.service.write_audit") as audit:
            service.delete_category(self.db, tenant_id=self.tid, public_id=public_id)
        self.assertIsNone(self.db.scalar(select(Category).where(Category.public_id == public_id)))
        self.assertEqual(audit.call_args.kwargs["action"], "DELETE")
        self.assertEqual(audit.call_args.kwargs["entity_id"], str(public_id))
        self.assertNotIn("Knitwear", service.list_category_names(self.db))

    def test_articles_including_archived_block_category_deletion(self):
        for archived in [False, True]:
            with self.subTest(archived=archived):
                category = self.category(name=f"Category {archived}")
                article = Product(tenant_id=self.tid, title="Shirt", category_id=category.id,
                                  is_deleted=archived)
                self.db.add(article)
                self.db.flush()
                with self.assertRaises(HTTPException) as error:
                    service.delete_category(self.db, tenant_id=self.tid, public_id=category.public_id)
                self.assertEqual(error.exception.status_code, 409)
                self.assertEqual(article.category_id, category.id)
                self.assertIsNotNone(self.db.get(Category, category.id))

    def test_child_categories_block_deletion(self):
        parent = self.category()
        child = self.category(name="Child", parent_id=parent.id)
        with self.assertRaises(HTTPException) as error:
            service.delete_category(self.db, tenant_id=self.tid, public_id=parent.public_id)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(child.parent_id, parent.id)

    def test_missing_and_foreign_categories_return_not_found(self):
        foreign = self.category(tenant_id=uuid.uuid4())
        for public_id in [foreign.public_id, uuid.uuid4()]:
            with self.assertRaises(HTTPException) as error:
                service.delete_category(self.db, tenant_id=self.tid, public_id=public_id)
            self.assertEqual(error.exception.status_code, 404)
        self.assertIsNotNone(self.db.get(Category, foreign.id))


class CatalogSkuTests(unittest.TestCase):
    def test_six_ascii_digits_preserve_leading_zeros(self):
        for contract in [ProductCreate, ProductUpdate]:
            for sku in ["SKU-000001", "SKU-123456", "SKU-999999"]:
                self.assertEqual(contract(title="Article", sku=sku).sku, sku)

    def test_short_long_letters_and_unprefixed_values_rejected(self):
        for contract in [ProductCreate, ProductUpdate]:
            for sku in ["", "SKU-12345", "SKU-1234567", "SKU-12A456", "123456", "SKU-１２３４５６", "SKU-123456\n"]:
                with self.subTest(contract=contract.__name__, sku=sku):
                    with self.assertRaises(ValidationError):
                        contract(title="Article", sku=sku)

    def test_detail_editor_can_omit_sku(self):
        self.assertIsNone(ProductUpdate(title="Article").sku)
