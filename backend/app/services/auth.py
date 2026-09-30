import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from pydantic import ValidationError
from siwe import SiweMessage, VerificationError
from sqlalchemy.orm import Session, sessionmaker
from web3 import Web3

from app.db.models import Challenge
from app.services.chains import ChainClient


class ChallengeVerificationError(Exception):
    """Base class for an unusable or invalid wallet challenge."""


class ChallengeUnavailable(ChallengeVerificationError):
    """The challenge is missing, expired, consumed, or bound to other inputs."""


class InvalidWalletSignature(ChallengeVerificationError):
    """The signature does not prove control of the requested wallet."""


class ContractRecipient(ChallengeVerificationError):
    """The configured faucet only supports externally owned recipients."""


class ChainLookupUnavailable(ChallengeVerificationError):
    """The selected chain could not be queried to check the recipient."""


@dataclass(frozen=True)
class CreatedChallenge:
    challenge_id: UUID
    message: str
    expires_at: datetime


@dataclass(frozen=True)
class VerifiedChallenge:
    challenge_id: UUID
    chain_id: int
    wallet_address: str


def create_wallet_challenge(
    sessions: sessionmaker[Session],
    *,
    wallet_address: str,
    chain_id: int,
    domain: str,
    uri: str,
    ttl_seconds: int,
) -> CreatedChallenge:
    """Create and persist a short-lived SIWE message for one wallet and chain."""
    wallet = _normalize_wallet(wallet_address)
    if not 0 < chain_id < 2**256:
        raise ValueError("chain_id must be a positive uint256")
    if not 60 <= ttl_seconds <= 900:
        raise ValueError("challenge lifetime must be between 60 and 900 seconds")

    # Keep timestamps on whole seconds so the SIWE text and DB expiry are identical.
    issued_at = datetime.now(timezone.utc).replace(microsecond=0)
    expires_at = issued_at + timedelta(seconds=ttl_seconds)
    challenge_id = uuid4()
    nonce = secrets.token_hex(16)
    siwe_message = SiweMessage(
        domain=domain,
        address=Web3.to_checksum_address(wallet),
        statement="Sign this message to prove wallet control and request faucet tokens. This does not authorize a blockchain transaction.",
        uri=uri,
        version="1",
        chain_id=chain_id,
        nonce=nonce,
        issued_at=_format_siwe_time(issued_at),
        expiration_time=_format_siwe_time(expires_at),
        request_id=str(challenge_id),
    ).prepare_message()

    with sessions.begin() as session:
        session.add(
            Challenge(
                id=challenge_id,
                chain_id=chain_id,
                wallet_address=wallet,
                nonce=nonce,
                message=siwe_message,
                created_at=issued_at,
                expires_at=expires_at,
            )
        )
        session.flush()

    return CreatedChallenge(challenge_id, siwe_message, expires_at)


def verify_wallet_challenge(
    sessions: sessionmaker[Session],
    *,
    challenge_id: UUID,
    message_text: str,
    signature: str,
    wallet_address: str,
    chain_id: int,
    domain: str,
    uri: str,
    chain_client: ChainClient,
) -> VerifiedChallenge:
    """Verify SIWE ownership and EOA eligibility without consuming the challenge.

    The claim reservation transaction consumes it together with the claim row. This
    keeps verification read-only while making replay prevention atomic with claiming.
    """
    wallet = _normalize_wallet(wallet_address)
    with sessions() as session:
        challenge = session.get(Challenge, challenge_id)
        if challenge is None:
            raise ChallengeUnavailable
        # Copy persisted values before closing the session.
        persisted = {
            "chain_id": int(challenge.chain_id),
            "wallet_address": challenge.wallet_address,
            "nonce": challenge.nonce,
            "message": challenge.message,
            "expires_at": challenge.expires_at,
            "consumed_at": challenge.consumed_at,
        }

    now = datetime.now(timezone.utc)
    if (
        persisted["chain_id"] != chain_id
        or persisted["wallet_address"] != wallet
        or persisted["consumed_at"] is not None
        or persisted["expires_at"] <= now
        or message_text != persisted["message"]
    ):
        raise ChallengeUnavailable

    try:
        message = SiweMessage.from_message(message_text)
        if (
            message.address != Web3.to_checksum_address(wallet)
            or message.chain_id != chain_id
            or message.domain != domain
            or str(message.uri) != uri
            or message.nonce != persisted["nonce"]
            or message.request_id != str(challenge_id)
        ):
            raise ChallengeUnavailable
        message.verify(signature=signature, domain=domain, nonce=persisted["nonce"])
    except ChallengeVerificationError:
        raise
    except (VerificationError, ValidationError, ValueError, TypeError, IndexError):
        raise InvalidWalletSignature from None

    if chain_client.config.chain_id != chain_id:
        raise ChallengeUnavailable
    try:
        recipient_code = chain_client.web3.eth.get_code(Web3.to_checksum_address(wallet))
    except Exception:
        raise ChainLookupUnavailable from None
    if recipient_code:
        raise ContractRecipient

    return VerifiedChallenge(challenge_id, chain_id, wallet)


def _normalize_wallet(address: str) -> str:
    if not Web3.is_address(address):
        raise ValueError("wallet_address must be a 20-byte EVM address")
    return Web3.to_checksum_address(address).lower()


def _format_siwe_time(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")
