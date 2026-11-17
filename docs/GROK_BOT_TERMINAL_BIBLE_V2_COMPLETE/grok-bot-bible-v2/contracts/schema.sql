-- SQL conceptuel V2: compléter migrations Alembic, RLS, indexes et contraintes de soldes.
CREATE TABLE strategy_version (
 id UUID PRIMARY KEY, strategy_id TEXT NOT NULL, version TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('DRAFT','IN_REVIEW','APPROVED','PUBLISHED','DEPRECATED')),
 config JSONB NOT NULL, config_sha256 CHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(strategy_id,version)
);
CREATE TABLE bot_instance (
 id UUID PRIMARY KEY, name TEXT NOT NULL, environment TEXT NOT NULL CHECK(environment IN ('DEMO','PAPER','LIVE')),
 status TEXT NOT NULL, strategy_version_id UUID REFERENCES strategy_version(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE session (
 id UUID PRIMARY KEY, bot_id UUID NOT NULL REFERENCES bot_instance(id), strategy_sha256 CHAR(64) NOT NULL,
 started_at TIMESTAMPTZ NOT NULL, ended_at TIMESTAMPTZ, status TEXT NOT NULL
);
CREATE TABLE domain_event (
 event_id UUID PRIMARY KEY, correlation_id UUID NOT NULL, aggregate_type TEXT NOT NULL, aggregate_id UUID NOT NULL,
 aggregate_sequence BIGINT NOT NULL, event_type TEXT NOT NULL, occurred_at TIMESTAMPTZ NOT NULL,
 received_at TIMESTAMPTZ NOT NULL DEFAULT now(), payload JSONB NOT NULL,
 UNIQUE(aggregate_type,aggregate_id,aggregate_sequence)
);
CREATE INDEX idx_event_correlation ON domain_event(correlation_id,occurred_at);
CREATE TABLE order_intent (
 id UUID PRIMARY KEY, bot_id UUID NOT NULL REFERENCES bot_instance(id),
 risk_approval_id UUID NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
 symbol TEXT NOT NULL, chain_id TEXT NOT NULL, token_address TEXT NOT NULL,
 notional NUMERIC(38,18) NOT NULL CHECK(notional>0), status TEXT NOT NULL
);
CREATE TABLE exchange_order (
 id UUID PRIMARY KEY, intent_id UUID NOT NULL REFERENCES order_intent(id),
 venue_order_id TEXT, client_order_id TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL, submitted_at TIMESTAMPTZ, confirmed_at TIMESTAMPTZ
);
CREATE TABLE fill (
 id UUID PRIMARY KEY, order_id UUID NOT NULL REFERENCES exchange_order(id), venue_fill_id TEXT NOT NULL,
 qty NUMERIC(38,18) NOT NULL CHECK(qty>0), price NUMERIC(38,18) NOT NULL CHECK(price>0),
 fee NUMERIC(38,18) NOT NULL DEFAULT 0, occurred_at TIMESTAMPTZ NOT NULL,
 UNIQUE(order_id,venue_fill_id)
);
CREATE TABLE ledger_entry (
 id UUID PRIMARY KEY, transaction_id UUID NOT NULL, account_code TEXT NOT NULL,
 asset_id TEXT NOT NULL, amount NUMERIC(38,18) NOT NULL, occurred_at TIMESTAMPTZ NOT NULL,
 source_event_id UUID REFERENCES domain_event(event_id)
);
CREATE INDEX idx_ledger_tx ON ledger_entry(transaction_id);
CREATE TABLE audit_event (
 id UUID PRIMARY KEY, actor_id TEXT NOT NULL, action TEXT NOT NULL, entity_type TEXT NOT NULL,
 entity_id TEXT NOT NULL, diff JSONB NOT NULL, occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
