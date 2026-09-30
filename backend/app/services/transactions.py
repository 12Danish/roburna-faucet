import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterator
from uuid import UUID

from eth_account import Account
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from web3 import Web3
from web3.exceptions import TransactionNotFound
from web3.logs import DISCARD

from app.db.models import ACTIVE_CLAIM_STATES, Claim, TransactionAttempt
from app.services.chains import ChainClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SignedAttempt:
    attempt_number: int
    nonce: int
    transaction_hash: str
    raw_transaction: bytes
    gas_limit: int
    gas_price_wei: int | None
    max_fee_per_gas_wei: int | None
    max_priority_fee_per_gas_wei: int | None


class ClaimTemporarilyUnavailable(Exception):
    """Current chain state prevents payout; leave the reserved claim queued."""


class InvalidClaimRecipient(Exception):
    """The recipient no longer satisfies the contract's EOA requirement."""


class ChainConfigurationError(Exception):
    """The worker signer or chain does not match the deployed contract config."""


def choose_next_nonce(chain_pending_nonce: int, highest_recorded_nonce: int | None) -> int:
    """Choose a nonce above both the node's pending count and our durable attempts."""
    if chain_pending_nonce < 0:
        raise ValueError("chain_pending_nonce must be non-negative")
    database_next = 0 if highest_recorded_nonce is None else highest_recorded_nonce + 1
    return max(chain_pending_nonce, database_next)


@contextmanager
def distributor_lock(engine: Engine, chain_id: int, sender_address: str) -> Iterator[bool]:
    """Try to serialize a distributor on one chain across all worker processes."""
    lock_key = f"faucet-distributor:{chain_id}:{sender_address.lower()}"
    with engine.connect() as connection:
        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            ).scalar_one()
        )
        connection.commit()
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            connection.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )
            connection.commit()


class FaucetTransactionWorker:
    def __init__(
        self,
        *,
        engine: Engine,
        sessions: sessionmaker[Session],
        chains: dict[int, ChainClient],
        private_key: bytes,
        replacement_after_seconds: int = 180,
        fee_bump_percent: int = 20,
        max_replacements: int = 3,
    ) -> None:
        self.engine = engine
        self.sessions = sessions
        self.chains = chains
        self.account = Account.from_key(private_key)
        self.private_key = private_key
        self.replacement_after_seconds = replacement_after_seconds
        self.fee_bump_percent = fee_bump_percent
        self.max_replacements = max_replacements
        if replacement_after_seconds < 30:
            raise ValueError("replacement_after_seconds must be at least 30")
        if not 10 <= fee_bump_percent <= 100:
            raise ValueError("fee_bump_percent must be between 10 and 100")
        if not 0 <= max_replacements <= 10:
            raise ValueError("max_replacements must be between 0 and 10")
        for chain_id, chain in chains.items():
            if self.account.address.lower() != chain.config.distributor_address.lower():
                raise ChainConfigurationError(
                    f"keystore address does not match distributor for chain {chain_id}"
                )

    def run_forever(self, poll_interval_seconds: int = 2) -> None:
        if poll_interval_seconds < 1:
            raise ValueError("poll_interval_seconds must be positive")
        logger.info("Faucet worker started for %d configured chain(s)", len(self.chains))
        while True:
            for chain in self.chains.values():
                try:
                    self.process_chain_once(chain)
                except Exception:
                    logger.exception("Worker iteration failed for chain %s", chain.config.chain_id)
            time.sleep(poll_interval_seconds)

    def process_chain_once(self, chain: ChainClient) -> bool:
        """Reconcile one outstanding payout or prepare and broadcast one reservation."""
        chain_id = chain.config.chain_id
        with distributor_lock(self.engine, chain_id, self.account.address) as lock_acquired:
            if not lock_acquired:
                return False
            active = self._get_active_attempt(chain_id)
            if active is not None:
                claim_id, attempt_id = active
                return self._reconcile_attempt(chain, claim_id, attempt_id)

            claim = self._get_next_reserved_claim(chain_id)
            if claim is None:
                return False
            try:
                prepared = self._prepare_attempt(chain, claim)
            except InvalidClaimRecipient:
                self._fail_claim(claim.id, "contract_recipient")
                return True
            except ClaimTemporarilyUnavailable as exc:
                logger.info("Claim %s deferred: %s", claim.id, exc)
                return False
            except ChainConfigurationError:
                raise
            except Exception:
                logger.exception("Unable to prepare claim %s", claim.id)
                return False

            attempt_id = self._persist_prepared_attempt(chain_id, claim, prepared)
            self._broadcast_attempt(chain, claim.id, attempt_id)
            return True

    def _get_active_attempt(self, chain_id: int) -> tuple[UUID, UUID] | None:
        with self.sessions() as session:
            row = session.execute(
                select(TransactionAttempt.claim_id, TransactionAttempt.id)
                .where(
                    TransactionAttempt.chain_id == Decimal(chain_id),
                    TransactionAttempt.sender_address == self.account.address.lower(),
                    TransactionAttempt.status.in_(("prepared", "broadcast_unknown", "submitted")),
                )
                .order_by(TransactionAttempt.sender_nonce, TransactionAttempt.attempt_number.desc())
                .limit(1)
            ).first()
            return (row[0], row[1]) if row else None

    def _get_next_reserved_claim(self, chain_id: int) -> Claim | None:
        with self.sessions() as session:
            return session.scalar(
                select(Claim)
                .where(
                    Claim.chain_id == Decimal(chain_id),
                    Claim.status == "reserved",
                )
                .order_by(Claim.reserved_at, Claim.id)
                .limit(1)
            )

    def _prepare_attempt(self, chain: ChainClient, claim: Claim) -> SignedAttempt:
        web3 = chain.web3
        chain_id = chain.config.chain_id
        if web3.eth.chain_id != chain_id:
            raise ChainConfigurationError("RPC chain ID changed after startup")
        self._check_distributor_role(chain)

        recipient = Web3.to_checksum_address(claim.wallet_address)
        amount = int(claim.amount_wei)
        if web3.eth.get_code(recipient):
            raise InvalidClaimRecipient
        if chain.contract.functions.paused().call():
            raise ClaimTemporarilyUnavailable("faucet is paused")
        max_payout = int(chain.contract.functions.maxPayout().call())
        spending_limit = int(chain.contract.functions.spendingLimit().call())
        spent = int(chain.contract.functions.spentInCurrentPeriod().call())
        balance = int(web3.eth.get_balance(chain.faucet_address))
        if amount > max_payout:
            raise ClaimTemporarilyUnavailable("claim amount exceeds current maxPayout")
        if amount > spending_limit - min(spent, spending_limit):
            raise ClaimTemporarilyUnavailable("period spending limit is exhausted")
        if amount > balance:
            raise ClaimTemporarilyUnavailable("faucet balance is insufficient")

        payout = chain.contract.functions.dispense(recipient, amount)
        estimate = int(payout.estimate_gas({"from": self.account.address}))
        gas_limit = (estimate * 120 + 99) // 100
        block_gas_limit = int(web3.eth.get_block("latest")["gasLimit"])
        if gas_limit > block_gas_limit:
            raise ClaimTemporarilyUnavailable("estimated payout gas exceeds the block gas limit")

        pending_nonce = int(web3.eth.get_transaction_count(self.account.address, "pending"))
        with self.sessions() as session:
            highest_nonce = session.scalar(
                select(func.max(TransactionAttempt.sender_nonce)).where(
                    TransactionAttempt.chain_id == Decimal(chain_id),
                    TransactionAttempt.sender_address == self.account.address.lower(),
                )
            )
        nonce = choose_next_nonce(
            pending_nonce,
            None if highest_nonce is None else int(highest_nonce),
        )
        fees = self._fee_fields(chain)
        transaction = payout.build_transaction(
            {
                "from": self.account.address,
                "chainId": chain_id,
                "nonce": nonce,
                "gas": gas_limit,
                "value": 0,
                **fees,
            }
        )
        signed = Account.sign_transaction(transaction, self.private_key)
        fee_fields = _extract_fee_fields(fees)
        with self.sessions() as session:
            attempt_number = int(
                session.scalar(
                    select(func.coalesce(func.max(TransactionAttempt.attempt_number), 0)).where(
                        TransactionAttempt.claim_id == claim.id
                    )
                )
            ) + 1
        return SignedAttempt(
            attempt_number=attempt_number,
            nonce=nonce,
            transaction_hash=_hash_hex(signed.hash),
            raw_transaction=bytes(signed.raw_transaction),
            gas_limit=gas_limit,
            **fee_fields,
        )

    def _fee_fields(self, chain: ChainClient) -> dict[str, int]:
        if chain.config.fee_mode == "legacy":
            return {"gasPrice": int(chain.web3.eth.gas_price)}
        if chain.config.fee_mode == "eip1559":
            latest = chain.web3.eth.get_block("latest")
            base_fee = latest.get("baseFeePerGas")
            if base_fee is None:
                raise ClaimTemporarilyUnavailable("RPC does not expose an EIP-1559 base fee")
            priority_fee = int(chain.web3.eth.max_priority_fee)
            return {
                "maxPriorityFeePerGas": priority_fee,
                "maxFeePerGas": int(base_fee) * 2 + priority_fee,
            }
        raise ChainConfigurationError("unsupported configured fee mode")

    def _check_distributor_role(self, chain: ChainClient) -> None:
        role = chain.contract.functions.DISTRIBUTOR_ROLE().call()
        if not chain.contract.functions.hasRole(role, self.account.address).call():
            raise ChainConfigurationError(
                f"distributor no longer has DISTRIBUTOR_ROLE on chain {chain.config.chain_id}"
            )

    def _persist_prepared_attempt(
        self, chain_id: int, claim: Claim, prepared: SignedAttempt
    ) -> UUID:
        with self.sessions.begin() as session:
            current_claim = session.scalar(
                select(Claim).where(Claim.id == claim.id).with_for_update()
            )
            if current_claim is None or current_claim.status != "reserved":
                raise RuntimeError("claim is no longer reserved")
            attempt = TransactionAttempt(
                claim_id=claim.id,
                chain_id=Decimal(chain_id),
                attempt_number=prepared.attempt_number,
                sender_address=self.account.address.lower(),
                sender_nonce=Decimal(prepared.nonce),
                transaction_hash=prepared.transaction_hash,
                signed_raw_transaction=prepared.raw_transaction,
                status="prepared",
                gas_limit=Decimal(prepared.gas_limit),
                gas_price_wei=_decimal_or_none(prepared.gas_price_wei),
                max_fee_per_gas_wei=_decimal_or_none(prepared.max_fee_per_gas_wei),
                max_priority_fee_per_gas_wei=_decimal_or_none(
                    prepared.max_priority_fee_per_gas_wei
                ),
                created_at=datetime.now(timezone.utc),
            )
            session.add(attempt)
            session.flush()
            return attempt.id

    def _broadcast_attempt(self, chain: ChainClient, claim_id: UUID, attempt_id: UUID) -> None:
        with self.sessions() as session:
            attempt = session.get(TransactionAttempt, attempt_id)
            if attempt is None:
                raise RuntimeError("prepared transaction attempt disappeared")
            raw_transaction = bytes(attempt.signed_raw_transaction)
            expected_hash = attempt.transaction_hash
        if chain.web3.eth.chain_id != chain.config.chain_id:
            raise ChainConfigurationError("RPC chain ID changed before transaction broadcast")
        self._check_distributor_role(chain)
        try:
            returned_hash = _hash_hex(chain.web3.eth.send_raw_transaction(raw_transaction))
            if returned_hash != expected_hash:
                raise RuntimeError("RPC returned a hash that differs from the signed transaction")
            status = "submitted"
            failure_code = None
        except Exception as exc:
            # A timeout or already-known response is ambiguous until queried by hash.
            pool_unavailable = "transaction pool not enabled" in str(exc).lower()
            try:
                chain.web3.eth.get_transaction(expected_hash)
                status = "submitted"
                failure_code = None
            except Exception:
                status = "broadcast_unknown"
                failure_code = (
                    "rpc_transaction_pool_unavailable"
                    if pool_unavailable else "broadcast_response_unknown"
                )
                logger.warning("Broadcast unresolved for transaction %s: %s", expected_hash, failure_code)

        with self.sessions.begin() as session:
            attempt = session.get(TransactionAttempt, attempt_id)
            claim = session.get(Claim, claim_id)
            if attempt is None or claim is None:
                return
            attempt.status = status
            attempt.failure_code = failure_code
            attempt.broadcast_at = datetime.now(timezone.utc)
            claim.status = status
            claim.failure_code = failure_code
            if claim.submitted_at is None:
                claim.submitted_at = datetime.now(timezone.utc)

    def _reconcile_attempt(self, chain: ChainClient, claim_id: UUID, active_id: UUID) -> bool:
        with self.sessions() as session:
            claim = session.get(Claim, claim_id)
            attempts = session.scalars(
                select(TransactionAttempt)
                .where(TransactionAttempt.claim_id == claim_id)
                .order_by(TransactionAttempt.attempt_number.desc())
            ).all()
            if claim is None:
                raise RuntimeError("claim referenced by transaction attempt is missing")
            recipient = claim.wallet_address
            amount = int(claim.amount_wei)
        if not attempts:
            raise RuntimeError("active transaction has no attempt history")

        receipts: list[tuple[TransactionAttempt, object]] = []
        for attempt in attempts:
            try:
                receipt = chain.web3.eth.get_transaction_receipt(attempt.transaction_hash)
                receipts.append((attempt, receipt))
            except TransactionNotFound:
                continue
            except Exception:
                logger.warning("Receipt lookup failed for %s", attempt.transaction_hash)
                return False
        if receipts:
            attempt, receipt = receipts[0]
            return self._record_receipt(chain, claim_id, attempt.id, receipt, recipient, amount)

        with self.sessions() as session:
            active = session.get(TransactionAttempt, active_id)
            if active is None:
                return False
            nonce = int(active.sender_nonce)
            tx_hash = active.transaction_hash
            raw_transaction = bytes(active.signed_raw_transaction)
            status = active.status
            attempt_number = active.attempt_number
            created_at = active.created_at
            gas_fields = _attempt_fee_fields(active)
            last_broadcast_at = active.broadcast_at
        try:
            chain.web3.eth.get_transaction(tx_hash)
            is_in_mempool = True
        except TransactionNotFound:
            is_in_mempool = False
        except Exception:
            logger.warning("Transaction lookup failed for %s", tx_hash)
            return False

        try:
            pending_nonce = int(
                chain.web3.eth.get_transaction_count(self.account.address, "pending")
            )
        except Exception:
            logger.warning("Pending nonce lookup failed on chain %s", chain.config.chain_id)
            return False
        if pending_nonce > nonce and not is_in_mempool:
            self._mark_attempt_unknown(claim_id, active_id, "nonce_consumed_without_receipt")
            return False

        age = (datetime.now(timezone.utc) - _as_utc(created_at)).total_seconds()
        if is_in_mempool and age < self.replacement_after_seconds:
            self._mark_broadcast_submitted(claim_id, active_id)
            return True
        if not is_in_mempool and status in {"prepared", "broadcast_unknown"}:
            if last_broadcast_at and (datetime.now(timezone.utc) - _as_utc(last_broadcast_at)).total_seconds() < 30:
                return False
            self._broadcast_attempt(chain, claim_id, active_id)
            return True
        if age >= self.replacement_after_seconds:
            if attempt_number > self.max_replacements:
                logger.error(
                    "Replacement limit reached for claim %s; transaction %s needs reconciliation",
                    claim_id,
                    tx_hash,
                )
                return False
            replacement_id = self._prepare_replacement(
                chain,
                claim_id,
                active_id,
                nonce,
                int(gas_fields["gas_limit"]),
                gas_fields["gas_price_wei"],
                gas_fields["max_fee_per_gas_wei"],
                gas_fields["max_priority_fee_per_gas_wei"],
            )
            self._broadcast_attempt(chain, claim_id, replacement_id)
            return True
        self._mark_broadcast_submitted(claim_id, active_id)
        return True

    def _record_receipt(
        self,
        chain: ChainClient,
        claim_id: UUID,
        winner_id: UUID,
        receipt: object,
        recipient: str,
        amount: int,
    ) -> bool:
        receipt_status = int(receipt["status"])
        winner_hash = _hash_hex(receipt["blockHash"])
        block_number = int(receipt["blockNumber"])
        if receipt_status == 1 and not self._has_expected_event(
            chain, receipt, recipient, amount
        ):
            self._mark_attempt_unknown(claim_id, winner_id, "receipt_event_mismatch")
            logger.error(
                "Successful transaction %s has no matching Dispensed event",
                _hash_hex(receipt["transactionHash"]),
            )
            return False

        current_block = int(chain.web3.eth.block_number)
        confirmations = max(0, current_block - block_number + 1)
        final = confirmations >= chain.config.confirmation_depth
        now = datetime.now(timezone.utc)
        with self.sessions.begin() as session:
            claim = session.get(Claim, claim_id)
            attempts = session.scalars(
                select(TransactionAttempt).where(TransactionAttempt.claim_id == claim_id)
            ).all()
            if claim is None:
                return False
            winner = next((item for item in attempts if item.id == winner_id), None)
            if winner is None:
                return False
            for attempt in attempts:
                if attempt.id != winner_id and attempt.status in (
                    "prepared",
                    "broadcast_unknown",
                    "submitted",
                ):
                    attempt.status = "replaced"
            session.flush()
            winner.receipt_block_number = Decimal(block_number)
            winner.receipt_block_hash = winner_hash
            winner.failure_code = None
            if receipt_status == 0 and final:
                winner.status = "reverted"
                claim.status = "failed"
                claim.failed_at = now
                claim.failure_code = "transaction_reverted"
                claim.confirmed_at = None
                return True
            winner.status = "confirmed" if final else "submitted"
            if final:
                claim.status = "confirmed"
                claim.confirmed_at = now
                claim.failed_at = None
                claim.failure_code = None
            else:
                claim.status = "submitted"
                claim.failure_code = None
                claim.confirmed_at = None
            return True

    @staticmethod
    def _has_expected_event(
        chain: ChainClient, receipt: object, recipient: str, amount: int
    ) -> bool:
        try:
            logs = chain.contract.events.Dispensed().process_receipt(receipt, errors=DISCARD)
        except Exception:
            return False
        expected_sender = chain.config.distributor_address.lower()
        expected_recipient = recipient.lower()
        for log in logs:
            if log["address"].lower() != chain.faucet_address.lower():
                continue
            args = log["args"]
            if (
                args["distributor"].lower() == expected_sender
                and args["recipient"].lower() == expected_recipient
                and int(args["amount"]) == amount
            ):
                return True
        return False

    def _prepare_replacement(
        self,
        chain: ChainClient,
        claim_id: UUID,
        previous_id: UUID,
        nonce: int,
        gas_limit: int,
        gas_price_wei: int | None,
        max_fee_per_gas_wei: int | None,
        max_priority_fee_per_gas_wei: int | None,
    ) -> UUID:
        with self.sessions() as session:
            claim = session.get(Claim, claim_id)
            previous = session.get(TransactionAttempt, previous_id)
            if claim is None or previous is None:
                raise RuntimeError("claim or transaction attempt disappeared before replacement")
            attempt_number = int(
                session.scalar(
                    select(func.coalesce(func.max(TransactionAttempt.attempt_number), 0)).where(
                        TransactionAttempt.claim_id == claim_id
                    )
                )
            ) + 1
            claim_wallet = claim.wallet_address
            amount = int(claim.amount_wei)

        fees = self._bumped_fee_fields(
            chain,
            gas_price_wei,
            max_fee_per_gas_wei,
            max_priority_fee_per_gas_wei,
        )
        transaction = chain.contract.functions.dispense(
            Web3.to_checksum_address(claim_wallet), amount
        ).build_transaction(
            {
                "from": self.account.address,
                "chainId": chain.config.chain_id,
                "nonce": nonce,
                "gas": gas_limit,
                "value": 0,
                **fees,
            }
        )
        signed = Account.sign_transaction(transaction, self.private_key)
        replacement = SignedAttempt(
            attempt_number=attempt_number,
            nonce=nonce,
            transaction_hash=_hash_hex(signed.hash),
            raw_transaction=bytes(signed.raw_transaction),
            gas_limit=gas_limit,
            **_extract_fee_fields(fees),
        )

        with self.sessions.begin() as session:
            prior = session.get(TransactionAttempt, previous_id)
            claim = session.scalar(select(Claim).where(Claim.id == claim_id).with_for_update())
            if prior is None or claim is None or claim.status not in ACTIVE_CLAIM_STATES:
                raise RuntimeError("claim state changed before replacement was persisted")
            prior.status = "replaced"
            session.flush()
            attempt = TransactionAttempt(
                claim_id=claim_id,
                chain_id=Decimal(chain.config.chain_id),
                attempt_number=replacement.attempt_number,
                sender_address=self.account.address.lower(),
                sender_nonce=Decimal(nonce),
                transaction_hash=replacement.transaction_hash,
                signed_raw_transaction=replacement.raw_transaction,
                status="prepared",
                gas_limit=Decimal(replacement.gas_limit),
                gas_price_wei=_decimal_or_none(replacement.gas_price_wei),
                max_fee_per_gas_wei=_decimal_or_none(replacement.max_fee_per_gas_wei),
                max_priority_fee_per_gas_wei=_decimal_or_none(
                    replacement.max_priority_fee_per_gas_wei
                ),
                created_at=datetime.now(timezone.utc),
            )
            session.add(attempt)
            session.flush()
            return attempt.id

    def _bumped_fee_fields(
        self,
        chain: ChainClient,
        gas_price_wei: int | None,
        max_fee_per_gas_wei: int | None,
        max_priority_fee_per_gas_wei: int | None,
    ) -> dict[str, int]:
        bump = self.fee_bump_percent
        if chain.config.fee_mode == "legacy":
            if gas_price_wei is None:
                raise RuntimeError("legacy transaction attempt is missing gasPrice")
            bumped = _ceil_percent(gas_price_wei, 100 + bump)
            return {"gasPrice": max(bumped, int(chain.web3.eth.gas_price))}
        if max_fee_per_gas_wei is None or max_priority_fee_per_gas_wei is None:
            raise RuntimeError("EIP-1559 transaction attempt is missing fee fields")
        latest = chain.web3.eth.get_block("latest")
        base_fee = latest.get("baseFeePerGas")
        if base_fee is None:
            raise ClaimTemporarilyUnavailable("RPC does not expose an EIP-1559 base fee")
        priority = max(
            _ceil_percent(max_priority_fee_per_gas_wei, 100 + bump),
            int(chain.web3.eth.max_priority_fee),
        )
        maximum = max(
            _ceil_percent(max_fee_per_gas_wei, 100 + bump),
            int(base_fee) * 2 + priority,
        )
        return {"maxPriorityFeePerGas": priority, "maxFeePerGas": maximum}

    def _mark_broadcast_submitted(self, claim_id: UUID, attempt_id: UUID) -> None:
        now = datetime.now(timezone.utc)
        with self.sessions.begin() as session:
            attempt = session.get(TransactionAttempt, attempt_id)
            claim = session.get(Claim, claim_id)
            if attempt is not None:
                attempt.status = "submitted"
                attempt.failure_code = None
                if attempt.broadcast_at is None:
                    attempt.broadcast_at = now
            if claim is not None and claim.status in ACTIVE_CLAIM_STATES:
                claim.status = "submitted"
                claim.failure_code = None
                if claim.submitted_at is None:
                    claim.submitted_at = now

    def _mark_attempt_unknown(self, claim_id: UUID, attempt_id: UUID, code: str) -> None:
        with self.sessions.begin() as session:
            attempt = session.get(TransactionAttempt, attempt_id)
            claim = session.get(Claim, claim_id)
            if attempt is not None:
                siblings = session.scalars(
                    select(TransactionAttempt).where(
                        TransactionAttempt.claim_id == claim_id,
                        TransactionAttempt.id != attempt_id,
                        TransactionAttempt.status.in_(("prepared", "broadcast_unknown", "submitted")),
                    )
                ).all()
                for sibling in siblings:
                    sibling.status = "replaced"
                session.flush()
                attempt.status = "broadcast_unknown"
                attempt.failure_code = code
            if claim is not None and claim.status in ACTIVE_CLAIM_STATES:
                claim.status = "broadcast_unknown"

    def _fail_claim(self, claim_id: UUID, code: str) -> None:
        with self.sessions.begin() as session:
            claim = session.get(Claim, claim_id)
            if claim is None or claim.status not in ACTIVE_CLAIM_STATES:
                return
            claim.status = "failed"
            claim.failed_at = datetime.now(timezone.utc)
            claim.confirmed_at = None
            claim.failure_code = code


def load_distributor_private_key(keystore_path: str, password: str) -> bytes:
    """Decrypt an Ethereum V3 keystore without exposing the key in process arguments."""
    import json
    from pathlib import Path

    try:
        keystore = json.loads(Path(keystore_path).read_text(encoding="utf-8"))
        return Account.decrypt(keystore, password)
    except Exception as exc:
        raise RuntimeError("could not decrypt distributor keystore; check its path and password") from exc


def _extract_fee_fields(fees: dict[str, int]) -> dict[str, int | None]:
    return {
        "gas_price_wei": fees.get("gasPrice"),
        "max_fee_per_gas_wei": fees.get("maxFeePerGas"),
        "max_priority_fee_per_gas_wei": fees.get("maxPriorityFeePerGas"),
    }


def _attempt_fee_fields(attempt: TransactionAttempt) -> dict[str, int | None]:
    return {
        "gas_limit": None if attempt.gas_limit is None else int(attempt.gas_limit),
        "gas_price_wei": None if attempt.gas_price_wei is None else int(attempt.gas_price_wei),
        "max_fee_per_gas_wei": (
            None
            if attempt.max_fee_per_gas_wei is None
            else int(attempt.max_fee_per_gas_wei)
        ),
        "max_priority_fee_per_gas_wei": (
            None
            if attempt.max_priority_fee_per_gas_wei is None
            else int(attempt.max_priority_fee_per_gas_wei)
        ),
    }


def _decimal_or_none(value: int | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _hash_hex(value: object) -> str:
    rendered = value.hex() if hasattr(value, "hex") else str(value)
    if not rendered.startswith("0x"):
        rendered = "0x" + rendered
    return rendered.lower()


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _ceil_percent(value: int, percent: int) -> int:
    return (value * percent + 99) // 100
