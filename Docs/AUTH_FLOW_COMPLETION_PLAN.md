# Auth Flow — Completion Plan

Status of the current build: the self-managed JWT auth system (login, refresh with
rotation/reuse-detection, email-verification OTP, password reset, invitations,
lockout, rate limiting, RBAC, Super Admin) is implemented and wired end to end.
The frontend has the `(auth)` pages, `AuthProvider`, `RequireAuth` gating, and an
api-client that refreshes-and-replays on 401.

This plan closes the remaining gaps to make the flow complete end to end. Three
things are outstanding; Phase 1 is the headline (the commit that added auth was
titled *"auth flow except setup pages"*).

Target happy path: **signup → verify email → company setup → dashboard**, with
login, invite/accept, forgot/reset, and lockout all already working.

---

## Phase 1 — Company setup step (primary gap)

Today `signup` registers the company with a **placeholder name**
(`` `${first}'s workspace` ``, `signup/page.tsx:40`), and after email verification
the user is sent straight to `/dashboard/overview`. There is no page to capture the
real business details and **no backend endpoint to update the tenant** (the only
company write is `register_company`).

### 1a. Backend — tenant setup flag + update endpoint

- **Migration `V044__tenant_setup_flag.sql`**: add
  `setup_complete BIT NOT NULL DEFAULT 0` to `dbo.tenants`.
  (Lets the app force the setup step exactly once and never again.)
- **Model** (`models/core.py`): add `setup_complete: Mapped[bool]` to `Tenant`.
- **Service** (`modules/auth/service.py`): add
  `complete_company_setup(*, tenant_id, name, currency, region)` — runs on the
  system session with the tenant context set; updates `Tenant.name`,
  `base_currency_code`, `region`, re-slugifies if the name changed, sets
  `setup_complete = True`; returns the refreshed profile's `company` block.
- **Endpoint** (`modules/auth/router.py`): `PATCH /auth/company`, guarded by
  `require_permission("admin.settings")` (owner/Admin only). Body:
  `{ companyName, currency, region? }`.
- **Profile** (`_profile` / `/auth/me`): include `company.setupComplete` and
  `company.currency` so the frontend can gate on them.

### 1b. Frontend — setup page + routing + gate

- **New page** `app/(auth)/setup/page.tsx`: "Step 2 of 2" — business name + base
  currency (reuse `CURRENCIES` from `lib/currency`) + optional region. Styled with
  the existing `(auth)` chrome.
- **Auth context** (`lib/auth.tsx`): add `completeSetup(payload)` (PATCH
  `/auth/company`, then refresh `user`); extend `AuthUser.company` with
  `setupComplete` and `currency`.
- **Post-verify routing** (`verify/page.tsx`): on success, if
  `!user.company.setupComplete` → `router.push("/setup")`, else `/dashboard/overview`.
- **Gate** (`components/auth/require-auth.tsx`): if authenticated but
  `!user.company.setupComplete` and not already on `/setup`, redirect to `/setup`
  (so it can't be skipped by navigating straight to a module).
- **Stepper** (`components/auth/auth-chrome.tsx`): add a `/setup` aside and fix the
  `ONBOARDING` `done/active` indices so signup→setup→verify read correctly.

---

## Phase 2 — Reconcile the role set

`core/roles.py` is the authoritative set: **Super Admin, Admin, Manager,
Merchandiser, Accountant, Logistics / Inventory** (no "User Overview"). But the
older admin user-create surfaces still reference "User Overview" and omit the new
set, and "User Overview" now maps to **zero permissions** (an effectively
locked-out user).

- **Backend `modules/admin/dto.py`**: replace the hand-written `RoleName` Literal
  with the assignable set derived from `core/roles.py` (`ROLES` minus `SUPER_ADMIN`,
  since company admins can't assign platform admin). Drop "User Overview".
- **Backend `modules/admin/service.py`**: update `_ROLE_TONE` to the new names.
- **Backend `modules/onboarding/service.py`** and **`db/seed_dev_admin.sql`**:
  reconcile `_DEFAULT_ROLES` and the seeded roles/users to the `roles.py` set
  (remove "User Overview"; remap the demo "User Overview" user).
- **Frontend `modules/admin/schema.ts`** + **`modules/admin/data.ts`**: update the
  role enum and mock rows to the assignable set.
- **Frontend admin Users screen**: source the role dropdown from `GET /auth/roles`
  (already returns `ROLES` minus Super Admin) instead of any hardcoded list, so it
  can never drift again.

Single source of truth after this: `core/roles.py` on the backend; `GET /auth/roles`
on the frontend.

---

## Phase 3 — Email delivery (config, low code)

`core/mailer.py` already sends via SMTP when configured, and otherwise "dev-reveals"
the OTP/reset/invite codes in the API response (guarded by `dev_auth_bypass`).

- Document and set the SMTP env vars (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
  `SMTP_PASSWORD`, `MAIL_FROM`) in `.env.example` with a short "leave blank in dev"
  note. No code change required for real delivery.

---

## Phase 4 — Hardening & cleanup

- **Refuse insecure secret in prod**: on startup, if `env == "prod"` and
  `settings.jwt_secret_is_default`, raise (there's already a `jwt_secret_is_default`
  helper). Same idea for `dev_auth_bypass` never being true in prod.
- **Login page**: the "Business name" field is cosmetic (doesn't affect auth) —
  either wire it as a display hint or remove it to avoid confusion.
- **Dead config**: the old `entra_*` settings are unused now — mark deprecated or
  remove.

---

## Phase 5 — Verify & test

- Apply migrations (V039–V043 already exist; add **V044**).
- `py_compile` backend + `npm run typecheck` frontend; confirm no new errors
  against the known pre-existing baseline.
- Manual end-to-end pass with `mailer` dev-reveal:
  1. `/signup` → `/verify` (enter dev code) → `/setup` → `/dashboard/overview`.
  2. `/login`, `/logout`.
  3. Admin invites a teammate → `/accept-invite` → lands in app with the right role.
  4. `/forgot-password` → `/reset-password`.
  5. Trip the lockout threshold → `/locked`.
  6. Role permission spot-check (e.g. Accountant sees Finance, is blocked elsewhere).
- Optional: run the flow verification through a subagent for an independent pass.

---

## Sequencing & effort

1. **Phase 1** (setup step) — the real "completion". Backend: 1 migration + 1 model
   field + 1 service fn + 1 route + profile field. Frontend: 1 page + context method
   + 2 routing/gate edits + stepper tweak.
2. **Phase 2** (roles) — small, mechanical, touches ~6 files; do alongside Phase 1.
3. **Phase 3** (email) — config only.
4. **Phase 4** (hardening) — small.
5. **Phase 5** (verify) — throughout.

Phases 1 and 2 are what make the flow genuinely complete and testable; 3–4 are
production-readiness.
