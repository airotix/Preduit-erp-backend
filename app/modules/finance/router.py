"""Finance HTTP routes."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import tenant_db
from app.core.security import Principal, require_tenant
from app.modules.finance import service
from app.modules.finance import fx as fx_service
from app.modules.finance.dto import (
    AccountCreate, AccountUpdate, BankAccountCreate, BankTxnCreate, BillCreate,
    BillUpdate, BudgetLineCreate, FixedAssetCreate, JournalEntryCreate,
    JournalEntryUpdate, JournalFull, PaymentCreate, PaymentUpdate, PeriodCreate,
)

router = APIRouter(prefix="/finance", tags=["finance"])


@router.get("/overview")
def finance_overview(currency: str = Query("PKR"), db: Session = Depends(tenant_db)):
    return service.convert(db, service.overview_screen(db), currency=currency)


# ---- Exchange rates (dated; keeps the currency conversion current) ----
@router.get("/fx/rates")
def fx_rates(db: Session = Depends(tenant_db)):
    return fx_service.rates_screen(db)


@router.post("/fx/sync-rates")
def fx_sync_rates(principal: Principal = Depends(require_tenant), db: Session = Depends(tenant_db)):
    return fx_service.sync_rates(db, principal.tenant_id)


@router.get("/accounts")
def finance_accounts(db: Session = Depends(tenant_db)):
    return service.accounts_min(db)


@router.get("/statements")
def finance_statements(currency: str = Query("PKR"),
                       principal: Principal = Depends(require_tenant),
                       db: Session = Depends(tenant_db)):
    payload = service.statements(db, tenant_id=principal.tenant_id)
    return service.convert(db, payload, currency=currency)


# ---- Period close ----
@router.get("/periods")
def periods(db: Session = Depends(tenant_db)):
    return service.periods_screen(db)


@router.post("/periods", status_code=status.HTTP_201_CREATED)
def create_period(payload: PeriodCreate, principal: Principal = Depends(require_tenant),
                  db: Session = Depends(tenant_db)):
    p = service.create_period(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(p.public_id), "name": p.name}


@router.post("/periods/{public_id}/close")
def close_period(public_id: str, principal: Principal = Depends(require_tenant),
                 db: Session = Depends(tenant_db)):
    p = service.set_period(db, public_id=public_id, status="Closed")
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Period not found")
    return {"public_id": str(p.public_id), "status": p.status}


@router.post("/periods/{public_id}/reopen")
def reopen_period(public_id: str, principal: Principal = Depends(require_tenant),
                  db: Session = Depends(tenant_db)):
    p = service.set_period(db, public_id=public_id, status="Open")
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Period not found")
    return {"public_id": str(p.public_id), "status": p.status}


# ---- Budgets ----
@router.get("/budget")
def budget(year: int = Query(...), currency: str = Query("PKR"),
           db: Session = Depends(tenant_db)):
    return service.convert(db, service.budget_actual(db, fiscal_year=year), currency=currency)


@router.post("/budget", status_code=status.HTTP_201_CREATED)
def create_budget(payload: BudgetLineCreate, principal: Principal = Depends(require_tenant),
                  db: Session = Depends(tenant_db)):
    bl = service.create_budget_line(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(bl.public_id)}


# ---- Fixed assets ----
@router.get("/fixed-assets")
def fixed_assets(currency: str = Query("PKR"), db: Session = Depends(tenant_db)):
    return service.convert(db, service.fixed_assets_screen(db), currency=currency)


@router.post("/fixed-assets", status_code=status.HTTP_201_CREATED)
def create_fixed_asset(payload: FixedAssetCreate, principal: Principal = Depends(require_tenant),
                       db: Session = Depends(tenant_db)):
    fa = service.create_fixed_asset(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(fa.public_id), "asset_no": fa.asset_no}


@router.post("/fixed-assets/{public_id}/depreciate")
def depreciate(public_id: str, principal: Principal = Depends(require_tenant),
               db: Session = Depends(tenant_db)):
    fa = service.depreciate_asset(db, tenant_id=principal.tenant_id, public_id=public_id)
    if fa is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Asset not found")
    return {"public_id": str(fa.public_id), "accumulated": float(fa.accumulated)}


# ---- VAT return ----
@router.get("/vat-return")
def vat_return(currency: str = Query("PKR"), principal: Principal = Depends(require_tenant),
               db: Session = Depends(tenant_db)):
    return service.convert(db, service.vat_return(db, tenant_id=principal.tenant_id), currency=currency)


# ---- Bank reconciliation ----
@router.get("/bank-accounts")
def bank_accounts(db: Session = Depends(tenant_db)):
    return service.bank_screen(db)


@router.post("/bank-accounts", status_code=status.HTTP_201_CREATED)
def create_bank_account(payload: BankAccountCreate, principal: Principal = Depends(require_tenant),
                        db: Session = Depends(tenant_db)):
    b = service.create_bank_account(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(b.public_id), "name": b.name}


@router.get("/bank-accounts/{public_id}/transactions")
def bank_transactions(public_id: str, currency: str = Query("PKR"),
                      db: Session = Depends(tenant_db)):
    d = service.bank_account_detail(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bank account not found")
    return service.convert(db, d, currency=currency)


@router.post("/bank-accounts/{public_id}/transactions", status_code=status.HTTP_201_CREATED)
def add_bank_transaction(public_id: str, payload: BankTxnCreate,
                         principal: Principal = Depends(require_tenant),
                         db: Session = Depends(tenant_db)):
    t = service.add_bank_transaction(db, tenant_id=principal.tenant_id, public_id=public_id, payload=payload)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bank account not found")
    return {"public_id": str(t.public_id)}


@router.post("/bank-accounts/{public_id}/auto-match")
def auto_match(public_id: str, principal: Principal = Depends(require_tenant),
               db: Session = Depends(tenant_db)):
    n = service.auto_match_bank(db, public_id=public_id)
    if n is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bank account not found")
    return {"matched": n}


@router.post("/bank-accounts/{public_id}/reconcile")
def reconcile(public_id: str, principal: Principal = Depends(require_tenant),
              db: Session = Depends(tenant_db)):
    n = service.reconcile_bank(db, public_id=public_id)
    if n is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bank account not found")
    return {"reconciled": n}


@router.post("/journal-entries", status_code=status.HTTP_201_CREATED)
def create_journal_entry(payload: JournalFull, principal: Principal = Depends(require_tenant),
                         db: Session = Depends(tenant_db)):
    je = service.create_full_journal(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(je.public_id), "entry_no": je.entry_no}


@router.post("/journal-entries/{public_id}/reverse")
def reverse_journal_entry(public_id: str, principal: Principal = Depends(require_tenant),
                          db: Session = Depends(tenant_db)):
    je = service.reverse_journal(db, public_id=public_id)
    if je is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Journal entry not found")
    return {"public_id": str(je.public_id), "entry_no": je.entry_no}


@router.post("/gl/sync")
def gl_sync(principal: Principal = Depends(require_tenant), db: Session = Depends(tenant_db)):
    posted = service.post_unposted(db, tenant_id=principal.tenant_id)
    return {"posted": posted}


@router.get("/customer-ledger")
def customer_ledger(currency: str = Query("PKR"), db: Session = Depends(tenant_db)):
    return service.convert(db, service.customer_ledger_screen(db), currency=currency)


@router.get("/customer-ledger/{public_id}")
def customer_ledger_statement(public_id: str, currency: str = Query("PKR"),
                              db: Session = Depends(tenant_db)):
    d = service.customer_statement(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    return service.convert(db, d, currency=currency)


@router.get("/profitability")
def finance_profitability(currency: str = Query("PKR"), db: Session = Depends(tenant_db)):
    return service.convert(db, service.profitability_screen(db), currency=currency)


@router.get("/supplier-ledger")
def supplier_ledger(currency: str = Query("PKR"), db: Session = Depends(tenant_db)):
    return service.convert(db, service.supplier_ledger_screen(db), currency=currency)


@router.get("/supplier-ledger/{public_id}")
def supplier_ledger_statement(public_id: str, currency: str = Query("PKR"),
                              db: Session = Depends(tenant_db)):
    d = service.supplier_statement(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Supplier not found")
    return service.convert(db, d, currency=currency)


@router.get("/coa/screen")
def coa_screen(limit: int = Query(100, le=500), offset: int = Query(0, ge=0),
               db: Session = Depends(tenant_db)):
    return service.coa_screen(db, limit=limit, offset=offset)


@router.post("/coa", status_code=status.HTTP_201_CREATED)
def create_account(payload: AccountCreate, principal: Principal = Depends(require_tenant),
                   db: Session = Depends(tenant_db)):
    a = service.create_account(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(a.public_id), "code": a.code}


@router.put("/coa/{public_id}")
def update_account(public_id: str, payload: AccountUpdate,
                   principal: Principal = Depends(require_tenant),
                   db: Session = Depends(tenant_db)):
    a = service.update_account(db, public_id=public_id, payload=payload)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    return {"public_id": str(a.public_id), "code": a.code}


@router.get("/journals/screen")
def journals_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                    db: Session = Depends(tenant_db)):
    return service.journals_screen(db, limit=limit, offset=offset)


@router.post("/journals", status_code=status.HTTP_201_CREATED)
def create_journal(payload: JournalEntryCreate, principal: Principal = Depends(require_tenant),
                   db: Session = Depends(tenant_db)):
    je = service.create_journal(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(je.public_id), "entry_no": je.entry_no}


@router.put("/journals/{public_id}")
def update_journal(public_id: str, payload: JournalEntryUpdate,
                   principal: Principal = Depends(require_tenant),
                   db: Session = Depends(tenant_db)):
    je = service.update_journal(db, public_id=public_id, payload=payload)
    if je is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Journal entry not found")
    return {"public_id": str(je.public_id), "entry_no": je.entry_no}


@router.get("/journals/{public_id}/detail")
def journal_detail(public_id: str, db: Session = Depends(tenant_db)):
    d = service.journal_detail(db, public_id=public_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Journal entry not found")
    return d


@router.get("/payments/screen")
def payments_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                    db: Session = Depends(tenant_db)):
    return service.payments_screen(db, limit=limit, offset=offset)


@router.post("/payments", status_code=status.HTTP_201_CREATED)
def create_payment(payload: PaymentCreate, principal: Principal = Depends(require_tenant),
                   db: Session = Depends(tenant_db)):
    p = service.create_payment(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(p.public_id), "payment_no": p.payment_no}


@router.put("/payments/{public_id}")
def update_payment(public_id: str, payload: PaymentUpdate,
                   principal: Principal = Depends(require_tenant),
                   db: Session = Depends(tenant_db)):
    p = service.update_payment(db, public_id=public_id, payload=payload)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    return {"public_id": str(p.public_id), "payment_no": p.payment_no}


@router.get("/araging/screen")
def araging_screen(db: Session = Depends(tenant_db)):
    return service.araging_screen(db)


@router.post("/araging/send-reminders")
def send_reminders(principal: Principal = Depends(require_tenant),
                   db: Session = Depends(tenant_db)):
    return service.send_ar_reminders(db, tenant_id=principal.tenant_id)


@router.get("/apaging/screen")
def apaging_screen(db: Session = Depends(tenant_db)):
    return service.apaging_screen(db)


@router.post("/apaging/schedule-payments")
def schedule_payments(principal: Principal = Depends(require_tenant),
                      db: Session = Depends(tenant_db)):
    return service.schedule_ap_payments(db, tenant_id=principal.tenant_id)


@router.get("/bills/screen")
def bills_screen(limit: int = Query(50, le=200), offset: int = Query(0, ge=0),
                 db: Session = Depends(tenant_db)):
    return service.bills_screen(db, limit=limit, offset=offset)


@router.post("/bills", status_code=status.HTTP_201_CREATED)
def create_bill(payload: BillCreate, principal: Principal = Depends(require_tenant),
                db: Session = Depends(tenant_db)):
    b = service.create_bill(db, tenant_id=principal.tenant_id, payload=payload)
    return {"public_id": str(b.public_id), "bill_no": b.bill_no}


@router.put("/bills/{public_id}")
def update_bill(public_id: str, payload: BillUpdate,
                principal: Principal = Depends(require_tenant),
                db: Session = Depends(tenant_db)):
    b = service.update_bill(db, public_id=public_id, payload=payload)
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bill not found")
    return {"public_id": str(b.public_id), "bill_no": b.bill_no}
