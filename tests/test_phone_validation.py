"""Phone limits across contact DTOs and editable invoice documents."""
import unittest
from unittest.mock import MagicMock

from fastapi import HTTPException
from pydantic import ValidationError

from app.core.validation import clean_phone, validate_invoice_phones
from app.modules.auth.dto import WorkspaceRequestForm
from app.modules.auth.service import save_company_profile
from app.modules.procurement.dto import SupplierCreate, SupplierUpdate
from app.modules.sales.dto import CustomerCreate, CustomerUpdate
from app.modules.procurement import service as procurement
from app.modules.sales import service as sales


class PhoneValidationTests(unittest.TestCase):
    def test_digit_boundaries_and_formatting(self):
        for value in ("+923001234567", "+92 (300) 123-4567", "+1 202 555 0123"):
            with self.subTest(value=value):
                self.assertEqual(clean_phone(value), "+12025550123" if value.startswith("+1") else "+923001234567")
        for value in ("123456", "123456789012345", "+12 (345) 678-9012345", "1234567abc"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                clean_phone(value)
        for value in (None, "", "   "):
            self.assertIsNone(clean_phone(value))

    def test_customer_and_supplier_create_and_update(self):
        for dto in (CustomerCreate, CustomerUpdate, SupplierCreate, SupplierUpdate):
            with self.subTest(dto=dto):
                self.assertEqual(dto(name="Contact", phone="+92 300 1234567").phone, "+923001234567")
                with self.assertRaises(ValidationError):
                    dto(name="Contact", phone="123456789012345")

    def test_workspace_contact_is_required_and_limited(self):
        fields = dict(name="Owner", email="owner@example.com", businessName="Business")
        self.assertEqual(WorkspaceRequestForm(**fields, contactNumber="+92 300 1234567").contactNumber,
                         "+923001234567")
        for value in ("   ", "123456789012345", "letters"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                WorkspaceRequestForm(**fields, contactNumber=value)

    def test_company_phone_and_support_line_reject_before_storage(self):
        for field in ("phone", "supportLine"):
            with self.subTest(field=field), self.assertRaises(HTTPException) as caught:
                save_company_profile("unused", {field: "123456789012345"})
            self.assertEqual(caught.exception.status_code, 422)

    def test_invoice_party_phone_validation(self):
        validate_invoice_phones({"exporter": {"tel": "+92 (300) 123-4567"}, "buyer": {"phone": ""}})
        for party, field in (("exporter", "tel"), ("buyer", "phone")):
            with self.subTest(party=party), self.assertRaises(HTTPException) as caught:
                validate_invoice_phones({party: {field: "123456789012345"}})
            self.assertEqual(caught.exception.status_code, 422)

    def test_invoice_create_and_update_reject_before_database_changes(self):
        for create, update in ((sales.create_sales_invoice_doc, sales.update_sales_invoice_doc),
                               (procurement.create_invoice, procurement.update_invoice)):
            for function, identity in ((create, {"tenant_id": "unused"}), (update, {"public_id": "unused"})):
                session = MagicMock()
                with self.subTest(function=function.__name__), self.assertRaises(HTTPException) as caught:
                    function(session, **identity, data={"exporter": {"tel": "123456789012345"}})
                self.assertEqual(caught.exception.status_code, 422)
                self.assertFalse(session.mock_calls)


if __name__ == "__main__":
    unittest.main()
