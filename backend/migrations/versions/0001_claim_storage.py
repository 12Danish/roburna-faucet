"""Create challenge, claim, and transaction-attempt storage."""

from alembic import op

revision = "0001_claim_storage"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE challenges (
            id UUID PRIMARY KEY,
            chain_id NUMERIC(78, 0) NOT NULL
                CHECK (chain_id BETWEEN 1 AND 115792089237316195423570985008687907853269984665640564039457584007913129639935),
            wallet_address VARCHAR(42) NOT NULL
                CHECK (wallet_address ~ '^0x[0-9a-f]{40}$'),
            nonce VARCHAR(128) NOT NULL UNIQUE,
            message TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ,
            CONSTRAINT challenges_expiry_after_creation
                CHECK (expires_at > created_at)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE claims (
            id UUID PRIMARY KEY,
            chain_id NUMERIC(78, 0) NOT NULL
                CHECK (chain_id BETWEEN 1 AND 115792089237316195423570985008687907853269984665640564039457584007913129639935),
            wallet_address VARCHAR(42) NOT NULL
                CHECK (wallet_address ~ '^0x[0-9a-f]{40}$'),
            challenge_id UUID NOT NULL UNIQUE REFERENCES challenges(id),
            amount_wei NUMERIC(78, 0) NOT NULL
                CHECK (amount_wei BETWEEN 1 AND 115792089237316195423570985008687907853269984665640564039457584007913129639935),
            status TEXT NOT NULL CHECK (
                status IN ('reserved', 'submitted', 'broadcast_unknown', 'confirmed', 'failed')
            ),
            reserved_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            submitted_at TIMESTAMPTZ,
            confirmed_at TIMESTAMPTZ,
            failed_at TIMESTAMPTZ,
            failure_code VARCHAR(80),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT claims_id_chain_unique UNIQUE (id, chain_id),
            CONSTRAINT claims_confirmation_state
                CHECK ((status = 'confirmed') = (confirmed_at IS NOT NULL)),
            CONSTRAINT claims_failure_state
                CHECK ((status = 'failed') = (failed_at IS NOT NULL))
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX claims_one_active_per_wallet
        ON claims (chain_id, wallet_address)
        WHERE status IN ('reserved', 'submitted', 'broadcast_unknown')
        """
    )
    op.execute(
        """
        CREATE INDEX claims_latest_confirmation_per_wallet
        ON claims (chain_id, wallet_address, confirmed_at DESC)
        WHERE status = 'confirmed'
        """
    )
    op.execute(
        """
        CREATE TABLE transaction_attempts (
            id UUID PRIMARY KEY,
            claim_id UUID NOT NULL,
            chain_id NUMERIC(78, 0) NOT NULL
                CHECK (chain_id BETWEEN 1 AND 115792089237316195423570985008687907853269984665640564039457584007913129639935),
            attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
            sender_address VARCHAR(42) NOT NULL
                CHECK (sender_address ~ '^0x[0-9a-f]{40}$'),
            sender_nonce NUMERIC(78, 0) NOT NULL
                CHECK (sender_nonce BETWEEN 0 AND 115792089237316195423570985008687907853269984665640564039457584007913129639935),
            transaction_hash VARCHAR(66) NOT NULL
                CHECK (transaction_hash ~ '^0x[0-9a-f]{64}$'),
            signed_raw_transaction BYTEA NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('prepared', 'broadcast_unknown', 'submitted', 'confirmed', 'reverted', 'replaced')
            ),
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            broadcast_at TIMESTAMPTZ,
            receipt_block_number NUMERIC(78, 0),
            receipt_block_hash VARCHAR(66)
                CHECK (receipt_block_hash IS NULL OR receipt_block_hash ~ '^0x[0-9a-f]{64}$'),
            failure_code VARCHAR(80),
            CONSTRAINT transaction_attempt_claim_chain_fk
                FOREIGN KEY (claim_id, chain_id) REFERENCES claims(id, chain_id),
            CONSTRAINT transaction_attempt_number_per_claim UNIQUE (claim_id, attempt_number),
            CONSTRAINT transaction_hash_per_chain UNIQUE (chain_id, transaction_hash)
        )
        """
    )
    op.execute(
        "CREATE INDEX transaction_attempts_by_claim ON transaction_attempts (claim_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE transaction_attempts")
    op.execute("DROP INDEX claims_latest_confirmation_per_wallet")
    op.execute("DROP INDEX claims_one_active_per_wallet")
    op.execute("DROP TABLE claims")
    op.execute("DROP TABLE challenges")
