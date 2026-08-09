-- Area isolada de staging. Este arquivo nao publica dados nas tabelas do aplicativo.
CREATE SCHEMA IF NOT EXISTS collector;

CREATE TABLE IF NOT EXISTS collector.import_batches (
    id uuid PRIMARY KEY,
    source_sha256 text NOT NULL,
    source_url text NOT NULL,
    source_title text NOT NULL,
    authorization_basis text NOT NULL,
    reviewed_by text NOT NULL,
    reviewed_at timestamptz NOT NULL,
    content_sha256 text NOT NULL UNIQUE,
    model text NOT NULL,
    collection_filters jsonb NOT NULL DEFAULT '{}',
    filtered_out_questions integer NOT NULL DEFAULT 0 CHECK (filtered_out_questions >= 0),
    created_at timestamptz NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE collector.import_batches
    ADD COLUMN IF NOT EXISTS collection_filters jsonb NOT NULL DEFAULT '{}';
ALTER TABLE collector.import_batches
    ADD COLUMN IF NOT EXISTS filtered_out_questions integer NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS collector.question_staging (
    id uuid PRIMARY KEY,
    batch_id uuid NOT NULL REFERENCES collector.import_batches(id),
    question_number integer NOT NULL CHECK (question_number > 0),
    statement text NOT NULL,
    alternatives jsonb NOT NULL,
    correct_answer text,
    answer_status text NOT NULL CHECK (answer_status IN ('missing', 'matched', 'annulled')),
    matter text,
    subject text,
    board text,
    organization text,
    role text,
    year integer,
    source_pages integer[] NOT NULL DEFAULT '{}',
    review_notes text[] NOT NULL DEFAULT '{}',
    editorial_status text NOT NULL DEFAULT 'pending_review'
        CHECK (editorial_status IN ('pending_review', 'approved', 'rejected')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (batch_id, question_number)
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON SCHEMA collector FROM anon;
        REVOKE ALL ON ALL TABLES IN SCHEMA collector FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON SCHEMA collector FROM authenticated;
        REVOKE ALL ON ALL TABLES IN SCHEMA collector FROM authenticated;
    END IF;
END
$$;
