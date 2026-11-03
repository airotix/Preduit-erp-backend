/* ============================================================================
 * Preduit ERP — V061__inspection_defect_image.sql
 * Per-defect photo evidence: store the uploaded document id directly on the
 * inspection defect, so images are attached inline when logging a defect (no
 * separate Photos/Defects tab needed). Idempotent. Run after V060.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.inspection_defects', 'image_doc_id') IS NULL
    ALTER TABLE dbo.inspection_defects ADD image_doc_id NVARCHAR(64) NULL;
GO

PRINT 'V061: inspection_defects.image_doc_id added.';
GO
