/* ============================================================================
 * Preduit ERP — V033__fx_gain_loss.sql  (NO-OP)
 *
 * The transactional FX gain/loss module was removed at the client's request.
 * The FX booking columns and fx_revaluations table this migration originally
 * created are no longer needed. Exchange-rate sync (Frankfurter/ECB) is retained
 * and uses the existing dbo.exchange_rates table from V001, so nothing to create.
 *
 * Kept as a no-op to preserve migration numbering. Safe to run on databases
 * where the earlier version was already applied (it simply does nothing here).
 * ==========================================================================*/
PRINT 'V033 is a no-op (FX gain/loss module removed; rate sync uses V001 exchange_rates).';
GO
