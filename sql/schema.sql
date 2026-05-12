-- ─────────────────────────────────────────────────────────────────────
-- Adess Backend — Supabase Schema
-- ─────────────────────────────────────────────────────────────────────
-- Paste this whole file into the Supabase SQL editor and run it.
-- Safe to re-run: every statement uses IF (NOT) EXISTS guards.
-- ─────────────────────────────────────────────────────────────────────

-- pgcrypto provides gen_random_uuid(). On Supabase it's usually enabled,
-- but we make sure here.
CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- ─────────────────────────────────────────────────────────────────────
-- 1. user_profiles — extends auth.users with public profile fields
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.user_profiles (
    id          UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email       TEXT,
    full_name   TEXT,
    avatar_url  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.user_profiles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view own profile"   ON public.user_profiles;
DROP POLICY IF EXISTS "Users can update own profile" ON public.user_profiles;

CREATE POLICY "Users can view own profile"
    ON public.user_profiles FOR SELECT
    USING (auth.uid() = id);

CREATE POLICY "Users can update own profile"
    ON public.user_profiles FOR UPDATE
    USING (auth.uid() = id);


-- ─────────────────────────────────────────────────────────────────────
-- 2. ads — the canonical ad row
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.ads (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    description  TEXT,
    source_url   TEXT,
    image_url    TEXT,
    image_model  VARCHAR(64) NOT NULL DEFAULT 'dalle3',
    status       VARCHAR(32) NOT NULL DEFAULT 'draft'
                 CHECK (status IN ('draft', 'published', 'archived')),
    metadata     JSONB NOT NULL DEFAULT '{
                     "scraped_raw":   {},
                     "cleaned_data":  {},
                     "outline":       {},
                     "image_prompt":  "",
                     "keywords":      [],
                     "brand_colors":  [],
                     "tone":          ""
                 }'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ads_user_id_idx ON public.ads(user_id);
CREATE INDEX IF NOT EXISTS ads_created_at_idx ON public.ads(created_at DESC);

ALTER TABLE public.ads ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view own ads"   ON public.ads;
DROP POLICY IF EXISTS "Users can create ads"     ON public.ads;
DROP POLICY IF EXISTS "Users can update own ads" ON public.ads;
DROP POLICY IF EXISTS "Users can delete own ads" ON public.ads;

CREATE POLICY "Users can view own ads"
    ON public.ads FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "Users can create ads"
    ON public.ads FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own ads"
    ON public.ads FOR UPDATE
    USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own ads"
    ON public.ads FOR DELETE
    USING (auth.uid() = user_id);


-- ─────────────────────────────────────────────────────────────────────
-- 3. ad_versions — append-only audit trail
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.ad_versions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ad_id        UUID NOT NULL REFERENCES public.ads(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    description  TEXT,
    image_url    TEXT,
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ad_versions_ad_id_idx ON public.ad_versions(ad_id);

ALTER TABLE public.ad_versions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view own ad versions" ON public.ad_versions;

CREATE POLICY "Users can view own ad versions"
    ON public.ad_versions FOR SELECT
    USING (
        ad_id IN (SELECT id FROM public.ads WHERE user_id = auth.uid())
    );


-- ─────────────────────────────────────────────────────────────────────
-- 4. updated_at auto-bump trigger
-- ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS user_profiles_set_updated_at ON public.user_profiles;
CREATE TRIGGER user_profiles_set_updated_at
    BEFORE UPDATE ON public.user_profiles
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

DROP TRIGGER IF EXISTS ads_set_updated_at ON public.ads;
CREATE TRIGGER ads_set_updated_at
    BEFORE UPDATE ON public.ads
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
