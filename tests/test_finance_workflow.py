"""Real ledger, allocation and GL regression tests on isolated SQLite."""
import unittest
from decimal import Decimal
from unittest.mock import patch
from sqlalchemy import select, func
import test_procurement_production as fixtures
from fastapi import HTTPException
from app.models.finance import Account, Payment, SupplierBill, JournalEntry, JournalLine, CreditNote, BankTransaction
from app.models.sales import Customer, Invoice
from app.models.procurement import Supplier, PoInvoice
from app.modules.finance import service as finance, repository as repo
from app.modules.finance.dto import PaymentCreate, PaymentUpdate, BillUpdate, BankAccountCreate, BankTxnCreate, LedgerEntryIn, JournalFull
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

    def test_empty_workspace_bootstraps_chart_and_posts_legacy_cash_payment_once(self):
        for account in self.db.scalars(select(Account)).all():
            self.db.delete(account)
        self.db.flush()
        payment = repo.create_payment(self.db, tenant_id=self.tid, party="Buyer", amount=25,
            pay_type="Receipt", method="Cash", reference=None, allocated_to=None,
            pay_date="2026-10-05", status="Cleared", notes=None)
        with patch.object(repo, "base_currency", return_value="PKR"):
            overview = finance.overview_screen(self.db, tenant_id=self.tid)
        self.assertTrue(payment.posted)
        self.assertEqual(overview["kpis"]["cashBank"]["value"], 25)
        accounts = self.db.scalars(select(Account)).all()
        self.assertEqual(len(accounts), 7)
        self.assertTrue(all(account.currency_code == "PKR" for account in accounts))
        finance.overview_screen(self.db, tenant_id=self.tid)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(JournalEntry)), 1)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Account)), 7)

    def test_existing_chart_is_not_replaced_by_initialization(self):
        cash = self.db.scalar(select(Account).where(Account.name == "Cash"))
        cash.name = "Petty cash"
        self.db.flush()
        finance._ensure_initial_chart(self.db, self.tid)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Account)), 6)
        self.assertEqual(cash.name, "Petty cash")

    def test_manual_money_movements_match_dashboard_and_reports(self):
        self.db.add(Account(tenant_id=self.tid, code="3000", name="Opening equity", acct_type="Equity", opening_balance=100))
        self.db.flush()
        finance.create_cash_bank_entry(self.db, tenant_id=self.tid, kind="bank",
            description="Other deposit", debit=0, credit=Decimal("200.25"))
        finance.create_cash_bank_entry(self.db, tenant_id=self.tid, kind="bank",
            description="Other payment", debit=Decimal("40.10"), credit=0)
        bank = finance.cash_bank_ledger(self.db, kind="bank")
        self.assertEqual(bank["closing"], 260.15)
        self.assertEqual(bank["totalCredit"], 200.25)
        self.assertEqual(bank["totalDebit"], 40.10)
        self.assertEqual(repo.overview_metrics(self.db)["cash_total"], 260.15)
        report = finance.statements(self.db, tenant_id=self.tid)
        self.assertEqual(report["cashFlow"]["endingCash"], 260.15)
        self.assertTrue(report["trialBalance"]["balanced"])
        self.assertTrue(report["balanceSheet"]["balanced"])
        count = self.db.scalar(select(func.count()).select_from(JournalEntry))
        finance.post_unposted(self.db, tenant_id=self.tid)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(JournalEntry)), count)

    def test_manual_party_payments_use_notes_direction(self):
        self.invoice(100)
        self.bill(100)
        finance.create_ledger_entry(self.db, tenant_id=self.tid, customer_public_id=self.customer.public_id,
            description="Customer receipt", debit=0, credit=30, payment_type="bank")
        finance.create_supplier_ledger_entry(self.db, tenant_id=self.tid, supplier_public_id=self.supplier.public_id,
            description="Supplier payment", debit=20, credit=0, payment_type="bank")
        bank = finance.cash_bank_ledger(self.db, kind="bank")
        self.assertEqual((bank["totalDebit"], bank["totalCredit"], bank["closing"]), (20, 30, 110))
        finance.post_unposted(self.db, tenant_id=self.tid)
        self.assertEqual(repo.overview_metrics(self.db)["cash_total"], 110)
        self.assertEqual(finance.customer_statement(self.db, public_id=self.customer.public_id)["closing"], 70)
        self.assertEqual(finance.supplier_statement(self.db, public_id=self.supplier.public_id)["closing"], -80)

    def test_manual_channel_change_reverses_and_reposts_once(self):
        entry = finance.create_cash_bank_entry(self.db, tenant_id=self.tid, kind="bank",
            description="Deposit", debit=0, credit=25)
        finance.update_ledger_payment_type(self.db, edit_type="manual", public_id=entry.public_id, payment_type="cash")
        self.assertEqual(finance.cash_bank_ledger(self.db, kind="bank")["closing"], 100)
        self.assertEqual(finance.cash_bank_ledger(self.db, kind="cash")["closing"], 25)
        self.assertEqual(repo.overview_metrics(self.db)["cash_total"], 125)
        count = self.db.scalar(select(func.count()).select_from(JournalEntry))
        finance.post_unposted(self.db, tenant_id=self.tid)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(JournalEntry)), count)
        with self.assertRaises(HTTPException):
            finance.update_ledger_payment_type(self.db, edit_type="manual", public_id=entry.public_id, payment_type=None)

    def test_existing_manual_movement_backfills_only_once(self):
        repo.create_ledger_entry(self.db, tenant_id=self.tid, customer_id=None, supplier_id=None,
            description="Legacy bank deposit", debit=0, credit=30, payment_type="bank")
        finance.post_unposted(self.db, tenant_id=self.tid)
        self.assertEqual(repo.overview_metrics(self.db)["cash_total"], 130)
        self.assertEqual(finance.cash_bank_ledger(self.db, kind="bank")["closing"], 130)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(JournalEntry)), 1)
        finance.post_unposted(self.db, tenant_id=self.tid)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(JournalEntry)), 1)

    def test_direct_bank_journal_is_a_money_out_debit(self):
        clearing = finance._ledger_clearing_account(self.db, self.tid)
        finance.create_full_journal(self.db, tenant_id=self.tid, payload=JournalFull(
            date="2026-10-05", memo="Payment service fee", lines=[
                dict(account=finance._label(clearing), debit=7.50),
                dict(account="1100 · Bank", credit=7.50)]))
        bank = finance.cash_bank_ledger(self.db, kind="bank")
        self.assertEqual((bank["totalDebit"], bank["totalCredit"], bank["closing"]), (7.50, 0, 92.50))
        self.assertEqual(bank["rows"][0]["source"], "Journal")
        self.assertEqual(repo.overview_metrics(self.db)["cash_total"], 92.50)

    def test_legacy_chart_balance_is_used_as_opening_consistently(self):
        bank = self.db.scalar(select(Account).where(Account.name == "Bank"))
        bank.opening_balance, bank.balance = 0, 100
        self.db.flush()
        self.assertEqual(finance.cash_bank_ledger(self.db, kind="bank")["opening"], 100)
        self.assertEqual(repo.overview_metrics(self.db)["cash_total"], 100)

    def test_missing_selected_bank_account_does_not_post_receipt_to_cash(self):
        bank = self.db.scalar(select(Account).where(Account.name == "Bank"))
        bank.is_deleted = True
        self.db.flush()
        with self.assertRaises(HTTPException) as caught:
            self.payment(25)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(JournalEntry)), 0)

    def test_payments_to_other_parties_do_not_change_supplier_debt(self):
        payment = self.payment(45, type="Disbursement", party="Electricity provider")
        ap = self.db.scalar(select(Account).where(Account.name == "Accounts payable"))
        lines = self.db.scalars(select(JournalLine).where(JournalLine.entry_id == payment.gl_journal_id)).all()
        self.assertFalse(any(line.account_id == ap.id for line in lines))
        bank = finance.cash_bank_ledger(self.db, kind="bank")
        self.assertEqual(bank["closing"], 55)
        self.assertEqual(bank["rows"][0]["partyType"], "other")

    def test_record_payment_respects_selected_invoice_and_bill_method(self):
        from app.modules.sales import service as sales
        invoice = self.invoice(50)
        sales.record_invoice_payment(self.db, tenant_id=self.tid, public_id=invoice.public_id, amount_paid=50, paid=True)
        bill = self.bill(20)
        bill.payment_type = "cash"
        finance.record_bill_payment(self.db, tenant_id=self.tid, public_id=bill.public_id, amount_paid=20, paid=True)
        self.assertEqual(finance.cash_bank_ledger(self.db, kind="bank")["closing"], 150)
        self.assertEqual(finance.cash_bank_ledger(self.db, kind="cash")["closing"], -20)

    def test_manual_entry_rejects_zero_both_and_negative_amounts(self):
        from pydantic import ValidationError
        for debit, credit in ((0, 0), (10, 10), (-1, 0), (0, -1)):
            with self.subTest(debit=debit, credit=credit), self.assertRaises(ValidationError):
                LedgerEntryIn(description="Invalid", debit=debit, credit=credit)
            with self.assertRaises(HTTPException):
                finance.create_cash_bank_entry(self.db, tenant_id=self.tid, kind="bank",
                    description="Invalid", debit=debit, credit=credit)

    def test_missing_party_does_not_create_an_unattributed_movement(self):
        import uuid
        for function, keyword in ((finance.create_ledger_entry, "customer_public_id"),
                                  (finance.create_supplier_ledger_entry, "supplier_public_id")):
            with self.assertRaises(HTTPException) as caught:
                function(self.db, tenant_id=self.tid, **{keyword: uuid.uuid4()},
                    description="Invalid party", debit=0, credit=1, payment_type="bank")
            self.assertEqual(caught.exception.status_code, 404)

    def bank_account(self):
        return finance.create_bank_account(self.db, tenant_id=self.tid,
            payload=BankAccountCreate(name="Main bank", gl_code="1100"))

    def bank_line(self, account, amount):
        return finance.add_bank_transaction(self.db, tenant_id=self.tid, public_id=account.public_id,
            payload=BankTxnCreate(description="Statement movement", amount=amount, txn_date="2026-10-05"))

    def test_bank_statement_opening_direction_and_balance(self):
        account = self.bank_account()
        self.bank_line(account, 200)
        self.bank_line(account, -30)
        detail = finance.bank_account_detail(self.db, public_id=account.public_id)
        self.assertEqual((detail["opening"], detail["totalDebit"], detail["totalCredit"], detail["statementBalance"]),
                         (100, 30, 200, 270))
        self.assertEqual([(row["debit"], row["credit"], row["balance"]) for row in detail["rows"]],
                         [(0, 200, 300), (30, 0, 270)])

    def test_bank_match_excludes_cash_uncleared_and_wrong_direction(self):
        account = self.bank_account()
        self.bank_line(account, 25)
        self.payment(25, method="Cash")
        self.payment(25, status="Pending")
        self.payment(25, status="Failed")
        self.payment(25, type="Disbursement")
        self.assertEqual(finance.auto_match_bank(self.db, public_id=account.public_id), 0)
        payment = self.payment(25)
        self.assertEqual(finance.auto_match_bank(self.db, public_id=account.public_id), 1)
        self.assertEqual(self.db.scalar(select(BankTransaction)).matched_payment_id, payment.id)

    def test_bank_match_does_not_guess_between_identical_payments(self):
        account = self.bank_account()
        self.bank_line(account, 25)
        self.payment(25)
        self.payment(25)
        self.assertEqual(finance.auto_match_bank(self.db, public_id=account.public_id), 0)

    def test_changed_payment_invalidates_reconciliation(self):
        account = self.bank_account()
        line = self.bank_line(account, 25)
        payment = self.payment(25)
        self.assertEqual(finance.auto_match_bank(self.db, public_id=account.public_id), 1)
        self.assertEqual(finance.reconcile_bank(self.db, public_id=account.public_id), 1)
        finance.update_payment(self.db, public_id=payment.public_id, payload=PaymentUpdate(
            party="Buyer", amount=25, type="Receipt", method="Bank", date="2026-10-05", status="Failed"))
        self.assertIsNone(line.matched_payment_id)
        self.assertEqual(line.status, "Unmatched")

    def test_reconcile_rechecks_legacy_matches(self):
        account = self.bank_account()
        line = self.bank_line(account, 25)
        payment = self.payment(25, method="Cash")
        line.matched_payment_id, line.status = payment.id, "Matched"
        self.db.flush()
        self.assertEqual(finance.reconcile_bank(self.db, public_id=account.public_id), 0)
        self.assertEqual(line.status, "Unmatched")
        self.assertIsNone(line.matched_payment_id)

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
