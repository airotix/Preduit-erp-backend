# Procurement point 3: suppliers and prices in Catalog

Catalog → article → Pricing contains Suppliers & purchase prices. Choose an
existing Procurement supplier and click Add supplier. Each added supplier has its
own colour × size matrix, matching the article's colour chart, with agreed EUR
prices in the cells. Enter prices and save that supplier's chart to persist the
assignment; newly added cards are explicitly marked as not saved until then.
An article may have several suppliers, each with independent variant prices.
Blank prices mean the variant is not supplied by that supplier. A supplier's
article pricing can be paused for new POs without changing existing documents.
Supplier records are created in Procurement; the article page manages the
article/supplier agreement. Changes are recorded in the audit log.

The supplier editor shows no SKU list. It uses the article's displayed colour and
size axes. Cells without an actual current variant are displayed as unavailable;
they cannot create extra variants or prices. Archived variants and records lacking
valid colour/size attributes are excluded on both read and save. Existing duplicate
variant records with the same colour/size share a single price cell: saving applies
that cell's price to each matching current record without deleting stock history.
Conflicting old prices require an explicit single price before saving. Saving one
supplier does not overwrite unfinished edits on another supplier's chart.

Manual POs keep the current article/colour/size quantity editor. Supplier choices
come from saved Catalog agreements, with a common supplier required across all
articles in one PO. Size prices and totals reflect the selected supplier, and
price fields are read only. Missing variant prices block submission. Sales
shortage POs show only suppliers configured for every missing variant of an item.
Replenishment rules also require a configured price for their supplier/variant.

All PO paths use the same server-side resolution: lock the article, resolve the
exact variant, check its active supplier agreement, and capture its current
agreed price and supplier identity on the PO. Submitted prices cannot override
Catalog prices. Later price edits and paused agreements affect new POs only.
Historical POs, invoice snapshots and Finance entries are not repriced.

The old single supplier-price field remains as a labelled legacy reference cost;
it no longer determines procurement pricing and is not required for activation.
Sales price requirements are unchanged. Supplier-specific prices must be set up
explicitly: V078 does not guess an article's current supplier from historical
POs or copy a generic cost into arbitrary supplier agreements.

Migration: V078_pg__catalog_supplier_prices.sql. Tests and runtime checks are
left to the user as requested. This work does not implement Procurement point 4
(change/cancellation impact modal) or points 6 and 7.
