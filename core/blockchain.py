"""
ChainPay Blockchain Core
========================
Custom permissioned blockchain for immutable transaction records.
Implements SHA-3 hashing, Merkle trees, and Proof-of-Authority consensus.

Time Complexity:
    - Block creation: O(n log n) where n = transactions (Merkle tree)
    - Chain validation: O(n * m) where n = blocks, m = transactions per block
    - Hash lookup: O(1) via index

Space Complexity: O(n * m) for full chain storage
"""

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict
import hmac
import secrets


# ─── Merkle Tree ─────────────────────────────────────────────────────────────

def sha3_256(data: str) -> str:
    """SHA-3 256-bit hash. Quantum-resistant alternative to SHA-2."""
    return hashlib.sha3_256(data.encode()).hexdigest()


def build_merkle_root(transactions: List[dict]) -> str:
    """
    Build Merkle root from transaction list.
    O(n log n) time, O(n) space.
    Returns deterministic root hash proving data integrity.
    """
    if not transactions:
        return sha3_256("EMPTY_BLOCK")

    leaves = [sha3_256(json.dumps(tx, sort_keys=True)) for tx in transactions]

    # Build tree bottom-up
    while len(leaves) > 1:
        if len(leaves) % 2 != 0:
            leaves.append(leaves[-1])  # Duplicate last leaf if odd
        leaves = [sha3_256(leaves[i] + leaves[i + 1]) for i in range(0, len(leaves), 2)]

    return leaves[0]


# ─── Data Structures ─────────────────────────────────────────────────────────

@dataclass
class Transaction:
    tx_id: str
    sender: str
    recipient: str
    amount: float
    currency: str
    tx_type: str          # SEND, DEPOSIT, WITHDRAW, FX_CONVERT, FEE
    fee: float
    timestamp: float
    metadata: dict = field(default_factory=dict)
    signature: str = ""
    status: str = "CONFIRMED"

    def to_dict(self) -> dict:
        return asdict(self)

    def signing_payload(self) -> str:
        """Deterministic payload for signature verification."""
        return json.dumps({
            "tx_id": self.tx_id,
            "sender": self.sender,
            "recipient": self.recipient,
            "amount": self.amount,
            "currency": self.currency,
            "timestamp": self.timestamp
        }, sort_keys=True)


@dataclass
class Block:
    index: int
    transactions: List[dict]
    previous_hash: str
    timestamp: float = field(default_factory=time.time)
    nonce: int = 0
    validator: str = "CHAINPAY_NODE_1"
    merkle_root: str = ""
    block_hash: str = ""

    def __post_init__(self):
        self.merkle_root = build_merkle_root(self.transactions)
        self.block_hash = self.compute_hash()

    def compute_hash(self) -> str:
        payload = json.dumps({
            "index": self.index,
            "transactions": self.transactions,
            "previous_hash": self.previous_hash,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
            "validator": self.validator,
            "merkle_root": self.merkle_root
        }, sort_keys=True)
        return sha3_256(payload)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "transactions": self.transactions,
            "previous_hash": self.previous_hash,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
            "validator": self.validator,
            "merkle_root": self.merkle_root,
            "block_hash": self.block_hash
        }


# ─── Blockchain ───────────────────────────────────────────────────────────────

class ChainPayBlockchain:
    """
    Permissioned blockchain with Proof-of-Authority consensus.
    
    Design choices:
    - PoA over PoW: No energy waste; suitable for enterprise fintech
    - SHA-3 hashing: Post-quantum resistance
    - Merkle trees: Efficient SPV (Simplified Payment Verification)
    - Batch blocks: Transactions batched per block for throughput
    """

    GENESIS_VALIDATOR = "CHAINPAY_GENESIS"
    MAX_TX_PER_BLOCK = 50

    def __init__(self, db_path: str = "chainpay.db"):
        self.chain: List[Block] = []
        self.pending_transactions: List[dict] = []
        self.tx_index: Dict[str, dict] = {}   # O(1) lookup by tx_id
        self._create_genesis_block()

    def _create_genesis_block(self):
        """Genesis block — the immutable foundation of the chain."""
        genesis = Block(
            index=0,
            transactions=[],
            previous_hash="0" * 64,
            timestamp=1700000000.0,
            validator=self.GENESIS_VALIDATOR
        )
        self.chain.append(genesis)

    @property
    def latest_block(self) -> Block:
        return self.chain[-1]

    def add_transaction(self, tx: Transaction) -> str:
        """
        Add transaction to pending pool. 
        Auto-mines block when pool reaches MAX_TX_PER_BLOCK.
        Returns tx_id.
        """
        # Anti-replay: reject duplicate tx_ids
        if tx.tx_id in self.tx_index:
            raise ValueError(f"Duplicate transaction: {tx.tx_id}")

        tx_dict = tx.to_dict()
        self.pending_transactions.append(tx_dict)
        self.tx_index[tx.tx_id] = tx_dict

        # Auto-mine when batch is full
        if len(self.pending_transactions) >= self.MAX_TX_PER_BLOCK:
            self.mine_block()

        return tx.tx_id

    def mine_block(self, force: bool = False) -> Optional[Block]:
        """
        Proof-of-Authority block creation.
        In PoA, validator identity replaces computational work.
        O(n log n) for Merkle tree construction.
        """
        if not self.pending_transactions and not force:
            return None

        block = Block(
            index=len(self.chain),
            transactions=self.pending_transactions.copy(),
            previous_hash=self.latest_block.block_hash
        )
        self.chain.append(block)
        self.pending_transactions.clear()
        return block

    def validate_chain(self) -> tuple[bool, str]:
        """
        Full chain validation. O(n * m) complexity.
        Checks: hash integrity, Merkle roots, chain linkage.
        """
        for i in range(1, len(self.chain)):
            current = self.chain[i]
            previous = self.chain[i - 1]

            # Verify hash linkage
            if current.previous_hash != previous.block_hash:
                return False, f"Chain broken at block {i}: hash mismatch"

            # Verify block hash integrity
            recomputed = current.compute_hash()
            if recomputed != current.block_hash:
                return False, f"Block {i} hash tampered"

            # Verify Merkle root
            expected_merkle = build_merkle_root(current.transactions)
            if expected_merkle != current.merkle_root:
                return False, f"Block {i} Merkle root tampered — transaction data modified"

        return True, "Chain valid"

    def get_transaction(self, tx_id: str) -> Optional[dict]:
        """O(1) transaction lookup via index."""
        return self.tx_index.get(tx_id)

    def get_user_transactions(self, user_id: str) -> List[dict]:
        """O(n) scan — acceptable for demo; production would use secondary index."""
        results = []
        for tx in self.tx_index.values():
            if tx["sender"] == user_id or tx["recipient"] == user_id:
                results.append(tx)
        return sorted(results, key=lambda x: x["timestamp"], reverse=True)

    def get_chain_stats(self) -> dict:
        return {
            "total_blocks": len(self.chain),
            "total_transactions": len(self.tx_index),
            "pending_transactions": len(self.pending_transactions),
            "chain_valid": self.validate_chain()[0],
            "latest_block_hash": self.latest_block.block_hash[:16] + "...",
            "latest_block_time": self.latest_block.timestamp
        }

    def get_recent_blocks(self, n: int = 10) -> List[dict]:
        return [b.to_dict() for b in reversed(self.chain[-n:])]


# Singleton instance
_blockchain_instance: Optional[ChainPayBlockchain] = None

def get_blockchain() -> ChainPayBlockchain:
    global _blockchain_instance
    if _blockchain_instance is None:
        _blockchain_instance = ChainPayBlockchain()
    return _blockchain_instance