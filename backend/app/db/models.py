from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

UINT256_MAX = 2**256 - 1
ACTIVE_CLAIM_STATES = ("reserved", "submitted", "broadcast_unknown")


class Challenge(Base):
    __tablename__ = "challenges"
    __table_args__ = (
        CheckConstraint(f"chain_id BETWEEN 1 AND {UINT256_MAX}", name="challenge_chain_id_uint256"),
        CheckConstraint(
            "wallet_address ~ '^0x[0-9a-f]{40}$'",
            name="challenge_wallet_normalized_evm_address",
        ),
        CheckConstraint("expires_at > created_at", name="challenge_expiry_after_creation"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    chain_id: Mapped[Decimal] = mapped_column(Numeric(78, 0), nullable=False)
    wallet_address: Mapped[str] = mapped_column(String(42), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint(f"chain_id BETWEEN 1 AND {UINT256_MAX}", name="claim_chain_id_uint256"),
        CheckConstraint(
            "wallet_address ~ '^0x[0-9a-f]{40}$'",
            name="claim_wallet_normalized_evm_address",
        ),
        CheckConstraint(f"amount_wei BETWEEN 1 AND {UINT256_MAX}", name="claim_amount_uint256"),
        CheckConstraint(
            "status IN ('reserved', 'submitted', 'broadcast_unknown', 'confirmed', 'failed')",
            name="claim_status_valid",
        ),
        CheckConstraint(
            "(status = 'confirmed') = (confirmed_at IS NOT NULL)",
            name="claim_confirmation_state_consistent",
        ),
        CheckConstraint(
            "(status = 'failed') = (failed_at IS NOT NULL)",
            name="claim_failure_state_consistent",
        ),
        UniqueConstraint("id", "chain_id", name="claim_id_chain_unique"),
        Index(
            "claims_one_active_per_wallet",
            "chain_id",
            "wallet_address",
            unique=True,
            postgresql_where=text("status IN ('reserved', 'submitted', 'broadcast_unknown')"),
        ),
        Index(
            "claims_latest_confirmation_per_wallet",
            "chain_id",
            "wallet_address",
            text("confirmed_at DESC"),
            postgresql_where=text("status = 'confirmed'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    chain_id: Mapped[Decimal] = mapped_column(Numeric(78, 0), nullable=False)
    wallet_address: Mapped[str] = mapped_column(String(42), nullable=False)
    challenge_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("challenges.id"), nullable=False, unique=True
    )
    amount_wei: Mapped[Decimal] = mapped_column(Numeric(78, 0), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    reserved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
        onupdate=func.clock_timestamp(),
    )


class TransactionAttempt(Base):
    __tablename__ = "transaction_attempts"
    __table_args__ = (
        CheckConstraint(f"chain_id BETWEEN 1 AND {UINT256_MAX}", name="attempt_chain_id_uint256"),
        CheckConstraint(
            "sender_address ~ '^0x[0-9a-f]{40}$'",
            name="attempt_sender_normalized_evm_address",
        ),
        CheckConstraint(f"sender_nonce BETWEEN 0 AND {UINT256_MAX}", name="attempt_nonce_uint256"),
        CheckConstraint(
            "transaction_hash ~ '^0x[0-9a-f]{64}$'",
            name="attempt_hash_normalized_bytes32",
        ),
        CheckConstraint(
            "status IN ('prepared', 'broadcast_unknown', 'submitted', 'confirmed', 'reverted', 'replaced')",
            name="attempt_status_valid",
        ),
        CheckConstraint(
            "receipt_block_hash IS NULL OR receipt_block_hash ~ '^0x[0-9a-f]{64}$'",
            name="attempt_receipt_hash_normalized_bytes32",
        ),
        ForeignKeyConstraint(
            ["claim_id", "chain_id"],
            ["claims.id", "claims.chain_id"],
            name="attempt_claim_chain_fk",
        ),
        UniqueConstraint("claim_id", "attempt_number", name="attempt_number_per_claim"),
        UniqueConstraint("chain_id", "transaction_hash", name="attempt_hash_per_chain"),
        Index("transaction_attempts_by_claim", "claim_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    claim_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    chain_id: Mapped[Decimal] = mapped_column(Numeric(78, 0), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    sender_address: Mapped[str] = mapped_column(String(42), nullable=False)
    sender_nonce: Mapped[Decimal] = mapped_column(Numeric(78, 0), nullable=False)
    transaction_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    signed_raw_transaction: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    broadcast_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_block_number: Mapped[Decimal | None] = mapped_column(Numeric(78, 0))
    receipt_block_hash: Mapped[str | None] = mapped_column(String(66))
    failure_code: Mapped[str | None] = mapped_column(String(80))
