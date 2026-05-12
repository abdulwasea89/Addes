-- ─────────────────────────────────────────────────────────────────────
-- Adess Backend — Supabase Storage Policies for the `adess` bucket
-- ─────────────────────────────────────────────────────────────────────
-- The bucket is configured public-read in the dashboard, which already
-- lets anyone fetch an object by URL. These policies govern writes:
-- only authenticated users, and only into their own `{user_id}/...` path.
--
-- Run this AFTER creating the bucket. Safe to re-run.
-- ─────────────────────────────────────────────────────────────────────

DROP POLICY IF EXISTS "adess: public read"     ON storage.objects;
DROP POLICY IF EXISTS "adess: owner insert"    ON storage.objects;
DROP POLICY IF EXISTS "adess: owner update"    ON storage.objects;
DROP POLICY IF EXISTS "adess: owner delete"    ON storage.objects;

-- 1. Anyone can read objects in the `adess` bucket (bucket is public anyway,
--    but an explicit policy is required for signed-URL flows and admin tools).
CREATE POLICY "adess: public read"
    ON storage.objects FOR SELECT
    USING (bucket_id = 'adess');

-- 2. An authenticated user may upload files only under their own user-id
--    folder, e.g. `<auth.uid()>/foo.png`.
CREATE POLICY "adess: owner insert"
    ON storage.objects FOR INSERT
    TO authenticated
    WITH CHECK (
        bucket_id = 'adess'
        AND (storage.foldername(name))[1] = auth.uid()::text
    );

-- 3. Authenticated users may update their own files.
CREATE POLICY "adess: owner update"
    ON storage.objects FOR UPDATE
    TO authenticated
    USING (
        bucket_id = 'adess'
        AND (storage.foldername(name))[1] = auth.uid()::text
    );

-- 4. Authenticated users may delete their own files.
CREATE POLICY "adess: owner delete"
    ON storage.objects FOR DELETE
    TO authenticated
    USING (
        bucket_id = 'adess'
        AND (storage.foldername(name))[1] = auth.uid()::text
    );
