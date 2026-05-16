-- ============================================================
--  Insurance Platform — Database Schema (Structure Only)
--  Engine : PostgreSQL 15+
--  Author : DB Engineering
-- ============================================================

-- ------------------------------------------------------------
-- ENUMS
-- Keep domain values enforced at the DB level; avoids dirty
-- string comparisons scattered across application code.
-- ------------------------------------------------------------

CREATE TYPE policy_status AS ENUM (
    'ACTIVE',
    'EXPIRED',
    'PENDING_PAYMENT',
    'CANCELLED'
);

CREATE TYPE severity_label AS ENUM (
    'LOW',
    'HIGH',
    'TOTAL_LOSS'
);

CREATE TYPE claim_status AS ENUM (
    'APPROVED',
    'REJECTED',
    'MANUAL_REVIEW',
    'AWAITING_INPUT'
);

-- ------------------------------------------------------------
-- TABLE: users
-- Central identity record for every insured customer.
-- ------------------------------------------------------------

CREATE TABLE users (
    user_id     BIGSERIAL       PRIMARY KEY,
    name        VARCHAR(255)    NOT NULL,
    email       VARCHAR(320)    NOT NULL UNIQUE,

    -- Audit timestamps
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ     NOT NULL DEFAULT now()
);

COMMENT ON TABLE  users           IS 'Insured customers registered in the platform.';
COMMENT ON COLUMN users.user_id   IS 'Surrogate primary key — auto-incremented.';
COMMENT ON COLUMN users.name      IS 'Customer full legal name.';
COMMENT ON COLUMN users.email     IS 'Contact e-mail; unique across the platform.';

-- ------------------------------------------------------------
-- TABLE: policies
-- An insurance contract linked to a single user.
-- ------------------------------------------------------------

CREATE TABLE policies (
    policy_id           BIGSERIAL           PRIMARY KEY,
    user_id             BIGINT              NOT NULL
                            REFERENCES users (user_id)
                            ON UPDATE CASCADE
                            ON DELETE RESTRICT,

    -- Contract details
    type                VARCHAR(120)        NOT NULL,
    status              policy_status       NOT NULL DEFAULT 'PENDING_PAYMENT',
    deductible_usd      NUMERIC(12, 2)      NOT NULL CHECK (deductible_usd    >= 0),
    coverage_limit_usd  NUMERIC(14, 2)      NOT NULL CHECK (coverage_limit_usd > 0),
    start_date          DATE                NOT NULL,
    end_date            DATE                NOT NULL,

    -- Audit timestamps
    created_at          TIMESTAMPTZ         NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ         NOT NULL DEFAULT now(),

    -- Business rule: coverage window must be coherent
    CONSTRAINT chk_policy_dates CHECK (end_date > start_date),

    -- Business rule: deductible cannot exceed the coverage limit
    CONSTRAINT chk_deductible_vs_limit CHECK (deductible_usd < coverage_limit_usd)
);

COMMENT ON TABLE  policies                    IS 'Insurance contracts issued to customers.';
COMMENT ON COLUMN policies.policy_id          IS 'Surrogate primary key — auto-incremented.';
COMMENT ON COLUMN policies.user_id            IS 'Owner of the policy (FK -> users).';
COMMENT ON COLUMN policies.type               IS 'Coverage type label, e.g. "Premium Full Coverage".';
COMMENT ON COLUMN policies.status             IS 'Lifecycle state of the policy.';
COMMENT ON COLUMN policies.deductible_usd     IS 'Amount the customer pays before coverage kicks in (USD).';
COMMENT ON COLUMN policies.coverage_limit_usd IS 'Maximum payout amount the insurer will cover (USD).';
COMMENT ON COLUMN policies.start_date         IS 'First day of active coverage.';
COMMENT ON COLUMN policies.end_date           IS 'Last day of active coverage (exclusive).';

-- ------------------------------------------------------------
-- TABLE: claims
-- An incident report filed against a policy.
-- ------------------------------------------------------------

CREATE TABLE claims (
    claim_id                BIGSERIAL       PRIMARY KEY,
    user_id                 BIGINT          NOT NULL
                                REFERENCES users    (user_id)
                                ON UPDATE CASCADE
                                ON DELETE RESTRICT,
    policy_id               BIGINT          NOT NULL
                                REFERENCES policies (policy_id)
                                ON UPDATE CASCADE
                                ON DELETE RESTRICT,

    -- Incident information (sourced from uploaded PDF)
    incident_date           DATE            NOT NULL,
    incident_description    TEXT            NOT NULL,
    incident_location       TEXT,               -- Optional; extracted from PDF

    -- AI-assisted triage fields
    severity_label          severity_label  NOT NULL,
    reported_damage_amount  NUMERIC(14, 2)              -- nullable; filled after user input
                                CHECK (reported_damage_amount IS NULL
                                    OR reported_damage_amount >= 0),
    claim_status            claim_status    NOT NULL DEFAULT 'AWAITING_INPUT',
    ai_reasoning            TEXT            NOT NULL,   -- auditor-facing justification

    -- Audit timestamps
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT now()
);

COMMENT ON TABLE  claims                         IS 'Incident claims filed by customers against their policies.';
COMMENT ON COLUMN claims.claim_id                IS 'Surrogate primary key — auto-incremented.';
COMMENT ON COLUMN claims.user_id                 IS 'Customer who filed the claim (FK -> users).';
COMMENT ON COLUMN claims.policy_id               IS 'Policy the claim is filed against (FK -> policies).';
COMMENT ON COLUMN claims.incident_date           IS 'Date the incident occurred (from the uploaded PDF).';
COMMENT ON COLUMN claims.incident_description    IS 'Verbatim incident description extracted from the PDF.';
COMMENT ON COLUMN claims.severity_label          IS 'AI-interpreted severity: LOW, HIGH, or TOTAL_LOSS.';
COMMENT ON COLUMN claims.reported_damage_amount  IS 'Damage amount in USD supplied by the customer; NULL until provided.';
COMMENT ON COLUMN claims.claim_status            IS 'Processing state: APPROVED, REJECTED, MANUAL_REVIEW, or AWAITING_INPUT.';
COMMENT ON COLUMN claims.ai_reasoning            IS 'Textual justification generated by the AI model for auditors.';

-- ------------------------------------------------------------
-- INDEXES
-- Only selective, query-driven indexes — no speculative ones.
-- ------------------------------------------------------------

-- Look up all policies for a given user
CREATE INDEX idx_policies_user_id
    ON policies (user_id);

-- Look up all claims for a given user
CREATE INDEX idx_claims_user_id
    ON claims (user_id);

-- Look up all claims for a given policy
CREATE INDEX idx_claims_policy_id
    ON claims (policy_id);

-- Filter open/actionable claims without a full scan
CREATE INDEX idx_claims_status
    ON claims (claim_status)
    WHERE claim_status IN ('MANUAL_REVIEW', 'AWAITING_INPUT');

-- ------------------------------------------------------------
-- TRIGGERS — keep updated_at current automatically
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION trg_set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

CREATE TRIGGER trg_policies_updated_at
    BEFORE UPDATE ON policies
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

CREATE TRIGGER trg_claims_updated_at
    BEFORE UPDATE ON claims
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- ============================================================
--  End of schema
-- ============================================================