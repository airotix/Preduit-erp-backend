# Tech packs

Tech Pack is the first production stage. Catalog article details, production
item timelines, and the Tech Pack column of the stage board provide upload and
download controls. An existing article attachment marks its production stage
Received automatically; without an attachment it stays Pending. Receipt cannot
be set using manual stage completion.

Production items resolve their article from the sales variant, SKU, or an exact
unique article title within the tenant. Uploading from a linked production item
updates the catalog article and all matching production timelines. Unlinked
items keep their attachment on the production item. Replacement uploads retain
the earlier documents and use the latest uploaded file.

Files use the existing S3 storage when S3_BUCKET is configured. Otherwise bytes
are saved in PostgreSQL document_contents, with document metadata and downloads
using the existing documents module. Existing upload size/type limits apply.
Catalog and production permissions govern their respective upload controls;
tenant isolation covers both metadata and bytes.

Apply db/V066_pg__tech_pack_storage.sql before running this version against an
existing database. It is included in the deployment migration runner; fresh
databases also include the storage table in the consolidated schema. The local
Docker database migration has been applied.

Active older timelines acquire the first stage when viewed. Completed historical
timelines retain their completed production status and show a separate attachment
control. Manufacturing can be started while the file is pending, but an active
timeline cannot reach full completion/final QC until the tech pack is received.

Verification: backend unittest suite, TypeScript type checks, and database-backed
file download tests. S3 routing is tested with the existing storage adapter mocked.
