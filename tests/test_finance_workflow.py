"""Real ledger, allocation and GL regression tests on isolated SQLite."""
import unittest
from decimal import Decimal
from unittest.mock import patch
from sqlalchemy import select, func
import test_procurement_production as fixtures
from fastapi import HTTPException
from app.models.finance import Account, Payment, SupplierBill, JournalEntry, JournalLine, CreditNote
from app.models.sales import Customer, Invoice
from app.models.procurement import Supplier, PoInvoice
from app.modules.finance import service as finance, repository as repo
from app.modules.finance.dto import PaymentCreate, PaymentUpdate, BillUpdate
from app.modules.procurement import service as procurement


class FinanceWorkflowTests(unittest.TestCase):
    def setUp(self):
        fixtures.ProcurementProductionTests.setUp(self)
        self.post.stop()  # exercise real balanced posting
        for code, name, kind in [("1000", "Cash", "Asset"), ("1100", "Bank", "Asset"),
                                 ("1200", "Accounts receivable", "Asset"), ("2000", "Accounts payable", "Liability"),
                                 ("4000", "Revenue", "Income"), ("1300", "Inventory", "Asset")]:
            self.db.add(Account(tenant_id=self.tid, code=code, name=name, acct_type=kind,
                opening_balance=100 if name == "Bank" else 0))
        self.customer = Customer(tenant_id=self.tid, name="Buyer", email="buyer@example.test", region="Local")
        self.supplier = Supplier(tenant_id=self.tid, name="Factory")
        self.db.add_all([self.customer, self.supplier])
        self.db.flush()

    tearDown = fixtures.ProcurementProductionTests.tearDown

    def invoice(self, amount=10000):
        doc = Invoice(tenant_id=self.tid, customer_id=self.customer.id, customer_name="Buyer",
            invoice_no="INV-TEST", amount=amount, status="Open", payment_type="bank")
        self.db.add(doc)
        self.db.flush()
        return doc

    def bill(self, amount=10000):
        return repo.create_bill(self.db, tenant_id=self.tid, supplier_name="Factory", po_ref="PO-TEST",
            amount=amount, due_on=None, status="Open")

    def payment(self, amount, *, type="Receipt", party=None, allocation=None, status="Cleared", method="Bank"):
        return finance.create_payment(self.db, tenant_id=self.tid, payload=PaymentCreate(
            party=party or ("Buyer" if type == "Receipt" else "Factory"), amount=amount, type=type,
            allocatedTo=allocation, status=status, method=method, date="2026-10-05"))

    def test_customer_partial_payment_positive_balance_and_remaining(self):
        inv = self.invoice()
        self.payment(4000, allocation=inv.invoice_no)
        stmt = finance.customer_statement(self.db, public_id=self.customer.public_id)
        self.assertEqual(stmt["closing"], 6000)
        self.assertEqual(stmt["balanceLabel"], "Receivable")
        self.assertEqual(stmt["totalDebit"], 10000)
        self.assertEqual(stmt["totalCredit"], 4000)
        self.assertEqual(repo.overview_metrics(self.db)["ar_total"], 6000)
        self.assertEqual(repo.list_ar_aging(self.db)[0]["current_amt"], 6000)
        self.assertEqual(repo.customer_parties(self.db)[0]["balance"], 6000)

    def test_customer_advance_and_excess_allocation(self):
        inv = self.invoice(100)
        with self.assertRaises(HTTPException):
            self.payment(150, allocation=inv.invoice_no)
        self.payment(150)
        stmt = finance.customer_statement(self.db, public_id=self.customer.public_id)
        self.assertEqual(stmt["closing"], -50)
        self.assertEqual(stmt["balanceLabel"], "Customer credit")

    def test_supplier_columns_advance_and_partial_payable(self):
        advance = self.payment(4000, type="Disbursement")
        stmt = finance.supplier_statement(self.db, public_id=self.supplier.public_id)
        self.assertEqual(stmt["closing"], 4000)
        self.assertEqual(stmt["balanceLabel"], "Supplier advance")
        bill = self.bill()
        finance.update_payment(self.db, public_id=advance.public_id, payload=PaymentUpdate(
            party="Factory", amount=4000, type="Disbursement", method="Bank", allocatedTo=bill.bill_no,
            date="2026-10-05", status="Cleared"))
        stmt = finance.supplier_statement(self.db, public_id=self.supplier.public_id)
        self.assertEqual(stmt["closing"], -6000)
        self.assertEqual(stmt["totalCredit"], 10000)
        self.assertEqual(stmt["totalDebit"], 4000)
        self.assertEqual(stmt["balanceLabel"], "Payable")
        self.assertEqual(repo.supplier_parties(self.db)[0]["balance"], -6000)
        self.assertEqual(repo.overview_metrics(self.db)["ap_total"], 6000)
        self.assertEqual(repo.list_ap_aging(self.db)[0]["current_amt"], 6000)

    def test_bank_cash_use_actual_cleared_movements_and_opening(self):
        self.invoice()
        bill = self.bill()
        bill.payment_type = "bank"
        self.db.add(CreditNote(tenant_id=self.tid, customer_id=self.customer.id, customer_name="Buyer",
            amount=200, payment_type="bank"))
        self.db.flush()
        self.payment(2000)
        self.payment(500, type="Disbursement")
        self.payment(999, status="Pending")
        self.payment(777, status="Failed")
        self.payment(300, method="Cash")
        bank = finance.cash_bank_ledger(self.db, kind="bank")
        self.assertEqual(len(bank["rows"]), 2)
        self.assertEqual(bank["totalDebit"], 500)
        self.assertEqual(bank["totalCredit"], 2000)
        self.assertEqual(bank["opening"], 100)
        self.assertEqual(bank["closing"], 1600)
        cash = finance.cash_bank_ledger(self.db, kind="cash")
        self.assertEqual(cash["closing"], 300)

    def test_pending_payment_clears_then_failure_reverses_once(self):
        inv = self.invoice(100)
        payment = self.payment(100, allocation=inv.invoice_no, status="Pending")
        self.assertFalse(payment.posted)
        self.assertEqual(inv.status, "Open")
        fields = dict(party="Buyer", amount=100, type="Receipt", method="Bank", allocatedTo=inv.invoice_no,
            date="2026-10-05")
        finance.update_payment(self.db, public_id=payment.public_id, payload=PaymentUpdate(**fields, status="Cleared"))
        self.assertTrue(payment.posted)
        self.assertEqual(inv.status, "Paid")
        finance.update_payment(self.db, public_id=payment.public_id, payload=PaymentUpdate(**fields, status="Failed"))
        self.assertEqual(inv.status, "Open")
        self.assertFalse(payment.posted)
        self.assertEqual(len(repo.cash_bank_ledger(self.db, payment_type="bank")), 0)
        count = self.db.scalar(select(func.count()).select_from(JournalEntry))
        finance.update_payment(self.db, public_id=payment.public_id, payload=PaymentUpdate(**fields, status="Failed"))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(JournalEntry)), count)
        bank = self.db.scalar(select(Account).where(Account.name == "Bank"))
        signed = self.db.scalar(select(func.sum(JournalLine.debit - JournalLine.credit)).where(JournalLine.account_id == bank.id))
        self.assertEqual(signed, 0)

    def test_payments_post_to_selected_bank_or_cash_account(self):
        bank_payment = self.payment(25)
        cash_payment = self.payment(15, method="Cash")
        for payment, name in [(bank_payment, "Bank"), (cash_payment, "Cash")]:
            lines = self.db.scalars(select(JournalLine).where(JournalLine.entry_id == payment.gl_journal_id)).all()
            self.assertTrue(any(self.db.get(Account, line.account_id).name == name for line in lines))
            self.assertEqual(sum(line.debit for line in lines), sum(line.credit for line in lines))

    def test_invoice_payable_link_reuses_legacy_bill_and_backfill_is_idempotent(self):
        po = fixtures.ProcurementProductionTests.create_po(self)
        invoice = self.db.scalar(select(PoInvoice))
        bill = self.db.get(SupplierBill, invoice.supplier_bill_id)
        self.assertEqual(bill.po_ref, po.po_no)
        self.assertEqual(invoice.supplier_name, po.supplier_name)
        self.assertEqual(bill.supplier_name, po.supplier_name)
        self.assertTrue(bill.posted)
        self.assertEqual(self.db.get(JournalEntry, bill.gl_journal_id).entry_on, bill.issued_date)
        invoice.supplier_bill_id = None
        self.db.flush()
        procurement.generate_missing_invoices(self.db, tenant_id=self.tid)
        self.assertEqual(invoice.supplier_bill_id, bill.id)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(SupplierBill)), 1)
        procurement.generate_missing_invoices(self.db, tenant_id=self.tid)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(SupplierBill)), 1)

    def test_wrong_party_allocation_rejected_and_full_payment_settles(self):
        bill = self.bill(100)
        with self.assertRaises(HTTPException):
            self.payment(100, type="Disbursement", party="Other supplier", allocation=bill.bill_no)
        self.payment(40, type="Disbursement", allocation=bill.bill_no)
        self.payment(60, type="Disbursement", allocation=bill.bill_no)
        self.assertEqual(bill.status, "Paid")
        self.assertEqual(finance.supplier_statement(self.db, public_id=self.supplier.public_id)["balanceLabel"], "Settled")

    def test_dashboard_summary_matches_party_and_cash_bank_balances(self):
        inv = self.invoice()
        bill = self.bill()
        self.payment(4000, allocation=inv.invoice_no)
        self.payment(3000, type="Disbursement", allocation=bill.bill_no)
        with patch.object(repo, "revenue_by_month", return_value=[]):
            data = finance.overview_screen(self.db, tenant_id=self.tid)
        self.assertEqual(data["ledgerSummary"], dict(customerDue=6000, customerCredit=0,
            supplierDue=7000, supplierAdvance=0, bankBalance=1100, cashBalance=0))
        self.assertEqual(data["kpis"]["ar"]["value"], 6000)
        self.assertEqual(data["kpis"]["ap"]["value"], 7000)
        self.assertEqual(len(data["recent"]), 6)
        self.assertTrue(all(row["memo"] for row in data["recent"]))

    def test_legacy_pending_payment_journal_repaired_once(self):
        payment = self.payment(100, status="Pending")
        bank = self.db.scalar(select(Account).where(Account.name == "Bank"))
        ar = self.db.scalar(select(Account).where(Account.name == "Accounts receivable"))
        journal = repo.create_full_journal(self.db, tenant_id=self.tid, reference=None,
            entry_on=None, entry_display="", memo="Legacy pending payment", lines=[
                dict(account=finance._label(bank), debit=100, credit=0),
                dict(account=finance._label(ar), debit=0, credit=100)])
        payment.posted, payment.gl_journal_id = True, journal.id
        self.db.flush()
        finance.post_unposted(self.db, tenant_id=self.tid)
        self.assertFalse(payment.posted)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(JournalEntry)), 2)
        finance.post_unposted(self.db, tenant_id=self.tid)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(JournalEntry)), 2)

    def test_opening_balances_keep_signed_due_and_advance_meaning(self):
        self.customer.opening_balance = 500
        self.supplier.opening_balance = -800
        self.db.flush()
        self.assertEqual(finance.customer_statement(self.db, public_id=self.customer.public_id)["closing"], 500)
        self.assertEqual(finance.supplier_statement(self.db, public_id=self.supplier.public_id)["closing"], -800)

    def test_inline_payment_method_change_reposts_to_cash(self):
        payment = self.payment(25)
        self.assertTrue(finance.update_ledger_payment_type(self.db, edit_type="payment",
            public_id=payment.public_id, payment_type="cash"))
        self.assertEqual(finance.cash_bank_ledger(self.db, kind="bank")["closing"], 100)
        self.assertEqual(finance.cash_bank_ledger(self.db, kind="cash")["closing"], 25)
        cash = self.db.scalar(select(Account).where(Account.name == "Cash"))
        self.assertEqual(self.db.scalar(select(func.sum(JournalLine.debit - JournalLine.credit))
            .where(JournalLine.account_id == cash.id)), 25)

    def test_posted_invoice_details_edit_preserves_payable_and_rejects_total_change(self):
        import json
        fixtures.ProcurementProductionTests.create_po(self)
        invoice = self.db.scalar(select(PoInvoice))
        bill = self.db.get(SupplierBill, invoice.supplier_bill_id)
        data = json.loads(invoice.data)
        data["contact"] = "Updated contact"
        procurement.update_invoice(self.db, public_id=invoice.public_id, data=data)
        self.assertEqual(invoice.supplier_bill_id, bill.id)
        data["totals"]["total"] += 50
        with self.assertRaises(HTTPException):
            procurement.update_invoice(self.db, public_id=invoice.public_id, data=data)
        self.assertEqual(float(bill.amount), float(invoice.total))
        with self.assertRaises(HTTPException):
            finance.update_bill(self.db, public_id=bill.public_id, payload=BillUpdate(
                supplier=bill.supplier_name, poRef=bill.po_ref, amount=bill.amount + 50))

    def test_recent_journals_show_header_only_records_without_hiding_postings(self):
        payment = self.payment(25)
        repo.create_journal(self.db, tenant_id=self.tid, reference="JE-SUMMARY",
            memo="Opening adjustment", debit=15, credit=15, status="Draft", date="05 Oct")
        rows = repo.recent_journal_lines(self.db, tenant_id=self.tid, limit=6)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], dict(date="05 Oct", entry="JE-SUMMARY", account="Summary only",
            memo="Opening adjustment", debit=15, credit=15))
        memo = self.db.get(JournalEntry, payment.gl_journal_id).memo
        self.assertTrue(all(row["memo"] == memo for row in rows[1:]))
        self.assertEqual(sum(row["debit"] for row in rows[1:]), 25)

    def test_recent_journals_respect_limit_deletion_and_tenant(self):
        import uuid
        import datetime
        self.payment(10)
        for i in range(8):
            repo.create_journal(self.db, tenant_id=self.tid, reference=f"JE-HEADER-{i}",
                memo="Header", debit=i, credit=i, status="Draft", date="")
        deleted = repo.create_journal(self.db, tenant_id=self.tid, reference="JE-DELETED",
            memo="Deleted", debit=5, credit=5, status="Draft", date="")
        deleted.is_deleted = True
        repo.create_journal(self.db, tenant_id=uuid.uuid4(), reference="JE-OTHER-TENANT",
            memo="Private", debit=5, credit=5, status="Draft", date="")
        self.db.flush()
        rows = repo.recent_journal_lines(self.db, tenant_id=self.tid, limit=6)
        self.assertEqual([r["entry"] for r in rows], [f"JE-HEADER-{i}" for i in range(7, 1, -1)])
        latest = self.db.scalar(select(JournalEntry).where(JournalEntry.entry_no == "JE-HEADER-7"))
        latest.entry_on = datetime.date(2026, 10, 5)
        self.db.flush()
        self.assertEqual(repo.recent_journal_lines(self.db, tenant_id=self.tid, limit=1)[0]["date"], "2026-10-05")
