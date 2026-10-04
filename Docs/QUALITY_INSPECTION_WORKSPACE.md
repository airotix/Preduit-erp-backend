# Quality inspection workspace

The primary path is Start inspection, resolve the checklist, log defects, and
Complete inspection. One compact summary shows resolved checks, sample size,
defects found and the allowed limit. A sticky action bar keeps the next action
visible. Completion is disabled while checks are Pending, no checklist exists,
an inspection save/photo upload is in progress, or reopened TNA is incomplete.
Existing backend completion and QC rules remain authoritative.

Checklist and Defects are the main panels. Batch, size quantities/ratios, AQL
sampling and history remain available in expandable sections. The defect input
suggests saved names and accepts new ones; unknown names use the details modal
and the existing transactional create-and-log endpoint. Failed saves keep inputs.

Working tabs contain the latest non-cancelled run per article and inspection
stage. History links can open older runs in a read-only tab alongside current
runs. Historical records still participate in the existing audit history, while
only latest runs determine current QC clearance.

The failed-inspection recovery dialog selects inspection-only or production TNA
recovery and accepts optional assignment, due date and notes. These details are
saved in the same transaction as recovery. Repeat recovery requests return the
existing recovery without overwriting saved action details. Invalid dates fail
before recovery changes. Hold, Scrap and Return to Vendor dispositions remain
available in the secondary corrective-action section.

No new migration is required. Verification includes the frontend TypeScript
check and backend tests for current/history tabs, corrective details, date
validation, recovery idempotency, and existing QC/receipt/shipment flows.
