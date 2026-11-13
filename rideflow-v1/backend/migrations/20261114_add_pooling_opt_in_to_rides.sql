-- Stage A pooling preview opt-in. Safe to reapply; do not run against hosted
-- Supabase until the migration is reviewed and scheduled for deployment.
ALTER TABLE public.rides
    ADD COLUMN IF NOT EXISTS pooling_opt_in boolean NOT NULL DEFAULT false;
