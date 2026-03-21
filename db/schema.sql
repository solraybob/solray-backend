-- ============================================================
-- Solray AI — PostgreSQL Schema (Supabase-compatible)
-- Phase 2 Database Schema
-- ============================================================
-- This schema is for production use with Supabase / PostgreSQL.
-- For local dev, SQLite is used via SQLAlchemy (see database.py).
-- ============================================================

-- Enable UUID extension (Supabase has this by default)
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- USERS
-- Core user account table. Stores identity + birth data.
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email        TEXT        NOT NULL UNIQUE,
    name         TEXT        NOT NULL,
    password_hash TEXT       NOT NULL,

    -- Birth data (required for chart calculations)
    birth_date   DATE        NOT NULL,
    birth_time   TEXT        NOT NULL,  -- 'HH:MM' format
    birth_city   TEXT,
    birth_lat    FLOAT,
    birth_lon    FLOAT,

    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- ============================================================
-- BLUEPRINTS
-- Cached blueprint JSON (full astrology + HD + Gene Keys result).
-- One blueprint per user, updated when birth data changes.
-- ============================================================
CREATE TABLE IF NOT EXISTS blueprints (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    blueprint_json JSONB     NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_blueprints_user ON blueprints(user_id);

-- Auto-update updated_at trigger (PostgreSQL)
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER blueprints_updated_at
    BEFORE UPDATE ON blueprints
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================================
-- DAILY FORECASTS
-- Cache table for per-user daily forecast results.
-- Avoids recomputing ephemeris on repeated requests same day.
-- ============================================================
CREATE TABLE IF NOT EXISTS daily_forecasts (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    forecast_date DATE       NOT NULL,
    forecast_json JSONB      NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Unique per user per day (used as cache key)
CREATE UNIQUE INDEX IF NOT EXISTS idx_forecasts_user_date 
    ON daily_forecasts(user_id, forecast_date);

CREATE INDEX IF NOT EXISTS idx_forecasts_user ON daily_forecasts(user_id);

-- ============================================================
-- SOUL CONNECTIONS
-- Tracks invites and accepted connections between users.
-- status: 'pending' | 'accepted' | 'declined'
-- ============================================================
CREATE TABLE IF NOT EXISTS soul_connections (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    requester_id UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recipient_id UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status       TEXT        NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'accepted', 'declined')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Prevent duplicate invites in either direction
    CONSTRAINT no_duplicate_connections 
        UNIQUE (requester_id, recipient_id)
);

CREATE INDEX IF NOT EXISTS idx_soul_requester ON soul_connections(requester_id);
CREATE INDEX IF NOT EXISTS idx_soul_recipient ON soul_connections(recipient_id);
