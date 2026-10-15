"""Saved season suggestions stay scoped to the current catalog."""
import unittest
import uuid

from pydantic import ValidationError

import test_procurement_production as fixtures
from app.models.catalog import Product
from app.modules.catalog import service
from app.modules.catalog.dto import ProductCreate


class CatalogSeasonTests(unittest.TestCase):
    setUp = fixtures.ProcurementProductionTests.setUp
    tearDown = fixtures.ProcurementProductionTests.tearDown

    def test_saved_seasons_are_trimmed_deduplicated_and_tenant_scoped(self):
        for name in [" Summer 2027 ", "summer 2027", "Core", "", None]:
            self.db.add(Product(tenant_id=self.tid, title="Article", season=name))
        self.db.add(Product(tenant_id=self.tid, title="Deleted", season="Deleted season", is_deleted=True))
        self.db.add(Product(tenant_id=uuid.uuid4(), title="Other", season="Other tenant"))
        self.db.flush()
        self.assertEqual(service.list_seasons(self.db, tenant_id=self.tid), ["Core", "Summer 2027"])

    def test_new_season_accepts_custom_names_with_storage_length_validation(self):
        product = ProductCreate(title="Article", sku="SKU-000001", season=" Summer 2027 ")
        self.assertEqual(product.season, "Summer 2027")
        with self.assertRaises(ValidationError):
            ProductCreate(title="Article", sku="SKU-000001", season="S" * 41)
