import asyncio
import uuid
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select

import test_procurement_production as workflow
from app.models.catalog import Product, ProductVariant
from app.models.document import Document, DocumentContent
from app.models.production import ProductionStage
from app.models.quality import Inspection
from app.modules import techpacks
from app.modules.documents.router import download
from app.modules.production import repository, service


class TechPackTests(unittest.TestCase):
    tearDown = workflow.ProcurementProductionTests.tearDown
    count = workflow.ProcurementProductionTests.count
    # Reuse the isolated ORM fixture without repeating the parent's test cases.
    def setUp(self):
        workflow.ProcurementProductionTests.setUp(self)
        audit = patch("app.modules.documents.service.write_audit")
        audit.start()
        self.addCleanup(audit.stop)
        settings = patch("app.modules.techpacks.get_settings", return_value=SimpleNamespace(s3_bucket=""))
        settings.start()
        self.addCleanup(settings.stop)
        self.article = Product(tenant_id=self.tid, title="Article")
        self.db.add(self.article)
        self.db.flush()
        self.db.add(ProductVariant(tenant_id=self.tid, product_id=self.article.id, sku="ARTICLE-1",
                                  price=10, currency_code="EUR"))
        self.order = repository.create_porder(self.db, tenant_id=self.tid, style="Article", factory="Factory", qty=2)
        self.line = repository.create_porder_line(self.db, tenant_id=self.tid, order_id=self.order.id,
                                                 name="Article", sku="ARTICLE-1", qty=2)
        self.db.flush()

    def upload(self, **kwargs):
        return techpacks.upload_product(self.db, tenant_id=self.tid, public_id=self.article.public_id,
                    filename="pack.pdf", content_type="application/pdf", data=b"%PDF-1.4 test", **kwargs)

    def start(self):
        service.start_production(self.db, tenant_id=self.tid, public_id=self.order.public_id,
            stages=[{"name": "Packed", "days": 1}], line_id=str(self.line.public_id))
        return repository.list_line_stages(self.db, self.line.id)

    def test_catalog_upload_persists_bytes_and_auto_receives_first_stage(self):
        result = self.upload()
        stages = self.start()
        self.assertEqual([(s.name, s.status) for s in stages], [("Tech Pack", "Received"), ("Packed", "In Progress")])
        self.assertEqual(techpacks.line_pack(self.db, self.line)["document"], result["document"])
        response = download(result["document"]["public_id"], db=self.db)
        async def body():
            return b"".join([chunk async for chunk in response.body_iterator])
        self.assertEqual(asyncio.run(body()), b"%PDF-1.4 test")
        self.assertEqual(self.count(DocumentContent), 1)

    def test_production_upload_is_shared_with_catalog_and_opens_final_qc(self):
        stages = self.start()
        self.assertEqual(stages[0].status, "Pending")
        with self.assertRaises(HTTPException):
            service.stage_action(self.db, action="complete", public_id=stages[0].public_id)
        service.stage_action(self.db, action="complete", public_id=stages[1].public_id)
        self.assertEqual(self.count(Inspection), 0)
        result = techpacks.upload_line(self.db, tenant_id=self.tid, public_id=self.line.public_id,
            filename="production.pdf", content_type="application/pdf", data=b"%PDF-1.4 production")
        self.assertEqual(stages[0].status, "Received")
        self.assertEqual(techpacks.product_pack(self.db, self.article)["document"], result["document"])
        self.assertEqual(self.count(Inspection), 1)
        self.upload()
        self.assertEqual(self.count(Inspection), 1)
        self.assertEqual(techpacks.product_pack(self.db, self.article)["document"]["filename"], "pack.pdf")
        self.assertEqual(self.count(DocumentContent), 2)

    def test_line_status_only_uses_direct_product_link_and_real_line_upload(self):
        self.line.name = "Article"
        self.line.sku = None
        self.db.flush()
        self.upload()
        self.assertEqual(techpacks.line_pack(self.db, self.line)["status"], "Pending")

    def test_unlinked_upload_stays_on_line_and_tenant_isolation(self):
        self.line.name = "Unknown"
        self.line.sku = "UNKNOWN"
        self.db.flush()
        stages = self.start()
        techpacks.upload_line(self.db, tenant_id=self.tid, public_id=self.line.public_id,
            filename="only-line.pdf", content_type="application/pdf", data=b"%PDF-1.4 line")
        self.assertEqual(stages[0].status, "Received")
        self.assertEqual(techpacks.product_pack(self.db, self.article)["status"], "Pending")
        with self.assertRaises(HTTPException) as caught:
            techpacks.product_by_public(self.db, self.article.public_id, uuid.uuid4())
        self.assertEqual(caught.exception.status_code, 404)

    def test_invalid_upload_does_not_receive_stage(self):
        stages = self.start()
        for filename, data in [("pack.exe", b"bad"), ("pack.pdf", b"")]:
            with self.assertRaises(HTTPException):
                techpacks.upload_line(self.db, tenant_id=self.tid, public_id=self.line.public_id,
                    filename=filename, content_type="application/pdf", data=data)
        self.assertEqual(stages[0].status, "Pending")
        self.assertEqual(self.count(Document), 0)

    def test_non_pdf_and_disguised_uploads_are_rejected_without_storage(self):
        for filename, mime, data in [("pack.docx", "application/octet-stream", b"%PDF-1.4"),
                                     ("pack.pdf", "image/png", b"%PDF-1.4"),
                                     ("pack.pdf", "application/pdf", b"not a pdf")]:
            with self.subTest(filename=filename, mime=mime), self.assertRaises(HTTPException) as caught:
                techpacks.upload_line(self.db, tenant_id=self.tid, public_id=self.line.public_id,
                    filename=filename, content_type=mime, data=data)
            self.assertEqual(caught.exception.status_code, 415)
        self.assertEqual(self.count(Document), 0)
        self.assertEqual(self.count(DocumentContent), 0)

    def test_s3_mode_reuses_existing_storage_without_database_bytes(self):
        with patch("app.modules.techpacks.get_settings", return_value=SimpleNamespace(s3_bucket="configured")), \
             patch("app.core.storage.save_file", return_value="s3://configured/pack.pdf") as save:
            self.upload()
        save.assert_called_once()
        self.assertEqual(self.count(DocumentContent), 0)
        self.assertEqual(self.db.scalar(select(Document)).storage_path, "s3://configured/pack.pdf")


