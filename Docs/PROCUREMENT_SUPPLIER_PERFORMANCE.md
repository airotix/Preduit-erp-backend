# Procurement point 7: supplier reliability and follow-up

## Entry points

Procurement → Supplier Performance (`/procurement/performance`) contains measured
supplier performance, a live issue queue and an internal follow-up register. The
supplier drill-down links to a supplier-filtered view. Its Scorecard tab and the
supplier list use the same calculation as the new page. Stored legacy scores,
price ratings and defect percentages are preserved in the database but are no
longer presented as calculated current results.

## Measurement policy

The scope is all recorded history, with visible numerators, denominators and data
coverage. No year-to-date or last-12-month claim is made without a real date basis.

- **On-time delivery:** completed PO count where every ordered variant's full
  quantity was received by the captured original due date, divided by completed
  POs with a captured original due date and fully dated receipt trail. Open late
  POs are shown separately, rather than counted as completed late deliveries.
- **Final QC first pass:** first finalized final-inspection result for each
  production style or batch variant. Reinspection does not erase the initial
  failure. This is an inspection assessment rate, not a defective-piece percentage.
- **Returned units:** all recorded supplier-return quantities divided by original
  receipt units plus physically received replacement units. All return reasons
  count; supplier fault is not inferred. Unlinked historical stock-only returns
  are outside this rate until linked in Supplier Returns.
- **Overdue POs:** approved/partially received POs with outstanding quantities and
  a full current expected date before today. Pending internal approvals and closed
  POs are excluded. Receipt ambiguities are flagged for reconciliation instead of
  being silently classified as late delivery.
- **Open returns:** unresolved supplier credit, refund or replacement records.

There is no invented overall weighted score, automatic supplier-status change, or
market-price comparison. Supplier status remains a separate manual commercial
decision. Absence of eligible evidence is shown as `—`, not a zero-percent score.

## Date and receipt provenance

New POs require a complete YYYY-MM-DD delivery date and capture `original_due_on`.
Later date changes preserve that baseline. Their current expected date controls
the operational overdue queue. The PO detail shows the original date or **Not
captured**. Legacy PO labels are not treated as proof of an original promise, so
legacy POs without this baseline are excluded from on-time calculations, even if
their current planned date is readable. A later edit does not invent that missing
baseline.

Physical ordinary and batch warehouse receipts capture `received_on`. New batch
receipt lines also retain their PO-line, variant and warehouse identifiers.
Historical receipt dates are accepted only if they include a complete year in ISO
or the existing `DD Mon YYYY` display format. No year is guessed from a day/month
label. Older receipt lines can be linked for measurement by their explicit PO-line
ID or a unique exact SKU within the source PO. Ambiguous matches are excluded and
their unit coverage is disclosed. Original receipts remain immutable history.

## Live issues and follow-ups

The queue identifies overdue delivery, ambiguous receipt mapping, latest failed
final-QC assessment, and unresolved supplier-return records. Each has a source
reference and a link to its owning module. Follow-ups can also be created manually
for a supplier without a source issue.

Procurement writers assign an active tenant user with Procurement write access,
enter a summary and set a due date. Statuses are Open, In progress and Resolved.
Updates require a note; resolution requires a recorded supplier response/outcome.
Owner changes, due-date changes and resolution notes retain actor/time history.
Overdue follow-ups are highlighted and active/resolved views are available.

Resolving a follow-up does not change a PO, inspection, inventory, return or Finance
record. A real source issue stays visible until resolved in its own module. If its
source disappears from the live queue, the follow-up stays open for manual review.
No supplier emails, external messages, scheduled notifications or payments are
sent or created by this workflow.

One active follow-up per source issue is enforced in the database. Request UUIDs,
row locks, replay checks, savepoints and a revision fingerprint protect retries,
conflicting submissions and stale edits. Resolved follow-ups remain history;
create a new follow-up if the issue recurs. Tenant RLS and Procurement read/write
permissions apply to the new endpoints.

## Deployment and validation

V080_pg__supplier_performance_followups.sql adds original due-date provenance,
actual receipt dates and the supplier_followups table. Applied successfully to
local Docker `preduit-db` / `preduit` and recorded in schema_migrations.

No tests, builds, type checks, API requests or browser checks were run, following
the user's instruction to test manually. Work was reviewed statically.
