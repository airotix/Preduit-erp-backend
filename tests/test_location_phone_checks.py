"""Country dependencies, phone semantics, and bounded public search contracts."""
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.core import locations
from app.core.validation import clean_phone, validate_invoice_phones
from app.modules.meta.locations_router import router
from app.modules.auth.dto import CompanySetupRequest, CreateWorkspaceRequest
from app.modules.sales.dto import CustomerCreate, CustomerUpdate
from app.modules.procurement.dto import SupplierCreate, SupplierUpdate


class LocationPhoneChecks(unittest.TestCase):
    def test_normalizes_national_format_using_country_and_keeps_foreign_numbers(self):
        self.assertEqual(clean_phone("0300 1234567", "Pakistan"), "+923001234567")
        self.assertEqual(clean_phone("0300 1234567", "PK"), "+923001234567")
        self.assertEqual(clean_phone("+1 (202) 555-0123", "PK"), "+12025550123")
        with self.assertRaises(ValueError):
            clean_phone("0300 1234567")

    def test_wrong_country_length_pattern_and_maximum_are_rejected(self):
        self.assertEqual(clean_phone("+49301234567890"), "+49301234567890")
        for value, country in [("03001234567", "US"), ("+923001234", None),
                               ("+920001234567", None), ("+493012345678901", None),
                               ("+923001234567 ext 12", None)]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                clean_phone(value, country)

    def test_mobile_fields_accept_mobile_but_general_phone_accepts_landline(self):
        self.assertEqual(clean_phone("+442079460018"), "+442079460018")
        with self.assertRaises(ValueError):
            clean_phone("+442079460018", mobile_only=True)
        self.assertEqual(clean_phone("+923001234567", mobile_only=True), "+923001234567")

    def test_location_membership_and_legacy_names(self):
        self.assertEqual(locations.country_code("Pakistan"), "PK")
        self.assertEqual(locations.country_code("UK"), "GB")
        self.assertEqual(locations.clean_location("PK", "Punjab", "Lahore"),
                         {"country": "Pakistan", "state": "Punjab", "city": "Lahore"})
        for args in [(None, None, "Lahore"), ("PK", "California", None),
                     ("PK", None, "London"), ("PK", "Sindh", "Lahore"), ("Unknown", None, None)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                locations.clean_location(*args)

    def test_contact_dtos_enforce_location_on_create_update_and_normalize_phone(self):
        for dto in [CustomerCreate, CustomerUpdate, SupplierCreate, SupplierUpdate]:
            with self.subTest(dto=dto):
                contact = dto(name="Test", country="PK", state="Punjab", city="Lahore", phone="0300 1234567")
                self.assertEqual(contact.country, "Pakistan")
                self.assertEqual(contact.phone, "+923001234567")
                with self.assertRaises(ValidationError):
                    dto(name="Test", country="PK", city="London")
                # Sales regions remain free text and never select a phone country implicitly.
                self.assertEqual(dto(name="Test", region="North accounts", phone="+12025550123").region, "North accounts")

    def test_setup_and_admin_workspace_validate_country_city_pair(self):
        base = dict(companyName="Company", currency="PKR", country="PK", city="Lahore")
        for dto, extra in [(CompanySetupRequest, {}), (CreateWorkspaceRequest,
                dict(ownerName="Owner", email="test@example.com", password="test-password"))]:
            self.assertEqual(dto(**base, **extra).country, "Pakistan")
            with self.assertRaises(ValidationError):
                dto(**{**base, "city": "London"}, **extra)

    def test_invoice_normalization_and_country_validation(self):
        payload = {"exporter": {"country": "PK", "tel": "0300 1234567"},
                   "buyer": {"country": "PK"}, "contact": "Accounts office", "contactPhone": "0300 1234567", "origin": "PK"}
        validate_invoice_phones(payload)
        self.assertEqual(payload["exporter"]["tel"], "+923001234567")
        self.assertEqual(payload["exporter"]["country"], "Pakistan")
        self.assertEqual(payload["contact"], "Accounts office")
        self.assertEqual(payload["contactPhone"], "+923001234567")
        self.assertEqual(payload["origin"], "Pakistan")

    def test_public_search_is_bounded_scoped_cached_and_safe(self):
        app = FastAPI(); app.include_router(router, prefix="/api/v1")
        with TestClient(app) as client:
            response = client.get("/api/v1/locations/cities", params={"country": "PK", "q": "Lah"})
            self.assertEqual(response.status_code, 200)
            self.assertIn("max-age", response.headers["cache-control"])
            self.assertIn("Lahore", [item["name"] for item in response.json()])
            for country in ["US", "IN", "PK"]:
                rows = client.get("/api/v1/locations/cities", params={"country": country}).json()
                self.assertLessEqual(len(rows), 50)
            self.assertEqual(client.get("/api/v1/locations/cities", params={"country": "PK", "state": "California"}).status_code, 422)
            self.assertEqual(client.get("/api/v1/locations/cities", params={"country": "Unknown"}).status_code, 422)
            self.assertEqual(client.get("/api/v1/locations/cities", params={"country": "PK", "q": "x" * 121}).status_code, 422)
            self.assertEqual(client.get("/api/v1/locations/cities", params={"country": "PK", "q": "' OR 1=1--"}).json(), [])


if __name__ == "__main__":
    unittest.main()
