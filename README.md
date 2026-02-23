# ⬡ ChainPay — Blockchain-Powered Mobile Money

**A production-grade, multi-currency mobile money system with blockchain audit layer.**
Built for hackathon demo. Modeled on M-Pesa with blockchain, FX engine, and banking-grade cryptography.

---

## 🚀 Quick Start (Local)

### 1. Prerequisites
- Python 3.10+ (includes tkinter by default on Windows/macOS)
- On Ubuntu/Debian: `sudo apt install python3-tk`

### 2. Install Dependencies
```bash
pip install cryptography PyJWT
```

### 3. Run the App
```bash
python main.py
```

**Demo credentials:**
- Phone: `+254700000000` | PIN: `1234` (auto-created on first run)
- Recipient: `+254700000001` (auto-created, use for send money demo)

---

## 📦 Package as .exe (Windows)

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name ChainPay main.py
```

The `.exe` will be in `dist/ChainPay.exe`.

For a cleaner build with icon:
```bash
pyinstaller --onefile --windowed --name ChainPay --icon=icon.ico \
    --add-data "chainpay.db;." main.py
```

---

## 🏗 Architecture

```
chainpay/
├── main.py              ← Entry point + full GUI (Tkinter)
├── core/
│   ├── blockchain.py    ← Custom permissioned blockchain
│   ├── security.py      ← AES-256-GCM, PBKDF2, JWT, HMAC
│   ├── database.py      ← SQLite schema, all DB operations
│   └── wallet.py        ← Business logic: send, FX, compliance
├── chainpay.db          ← SQLite database (auto-created)
└── requirements.txt
```

---

## ⛓ Blockchain Design

### Custom Permissioned Blockchain
- **Consensus:** Proof-of-Authority (PoA) — validator signs blocks
- **Hashing:** SHA-3 256-bit (post-quantum resistant)
- **Data structure:** Merkle tree for transaction integrity
- **Block size:** Up to 50 transactions per block
- **Auto-mine:** Block mined when pool fills; manual mine in UI

### Block Structure
```python
Block {
    index:         int         # Position in chain
    transactions:  List[dict]  # Batched transactions
    previous_hash: str         # SHA-3 hash of prior block
    merkle_root:   str         # SHA-3 Merkle root of txs
    timestamp:     float       # Unix timestamp
    validator:     str         # PoA validator identity
    nonce:         int         # Reserved (PoW extension)
    block_hash:    str         # SHA-3 hash of this block
}
```

### Merkle Tree — O(n log n)
```
        Root
       /    \
    H(AB)  H(CD)
    /  \   /  \
  H(A) H(B) H(C) H(D)   ← Transaction hashes
```

Tampering any transaction changes its hash → changes branch hashes → changes root → detected during `validate_chain()`.

---

## 🔐 Security Architecture

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Encryption | AES-256-GCM | Data at rest + in transit |
| Key Derivation | PBKDF2-SHA256 (100k iter) | PIN → key derivation |
| Message Auth | HMAC-SHA3-256 | Transaction signing |
| Sessions | JWT HS256 | Stateless auth, 1hr expiry |
| Anti-replay | UUID tx_id dedup | Prevent duplicate submissions |
| Rate limiting | Sliding window (30 req/min) | Brute force protection |
| Lockout | 5 failed attempts → 5min lock | Brute force protection |

### Threat Model (STRIDE)
- **Spoofing:** Mitigated by HMAC-signed transactions + JWT auth
- **Tampering:** Mitigated by blockchain Merkle tree + AES-GCM integrity
- **Repudiation:** Mitigated by immutable audit log + blockchain
- **Info Disclosure:** AES-256-GCM encryption
- **DoS:** Rate limiting + circuit breaker pattern
- **Elevation:** RBAC (user/admin roles in JWT)

---

## 💱 FX Engine

**Algorithm:**
1. Load base rate from DB (seeded from real rates)
2. Apply ±0.3% random walk for live market simulation
3. Apply 1.5% bid/ask spread (0.75% each side)
4. Cache rate for 30 seconds (configurable TTL)
5. Cross-rates computed via USD pivot: `USD→A rate × USD→B rate`

**Fee Structure (tiered, similar to M-Pesa):**
| Amount (USD) | Fee |
|---|---|
| < $10 | 0.5% |
| $10–$100 | 1.0% |
| $100–$1,000 | 1.5% |
| > $1,000 | 2.0% |
| Cross-border | +0.5% surcharge |

---

## 🛡 Compliance Engine

### AML Rules (automatic enforcement):
1. **Single TX limit:** $2,000 max per transaction
2. **Daily limit:** $5,000 per 24 hours
3. **Velocity check:** Max 10 transactions/hour
4. **Structuring detection:** 3+ transactions of $900–$1,000 in 24h triggers SAR
5. **Sanctions screening:** Hardcoded blocklist (demo); real system integrates OFAC API

### KYC:
- All accounts auto-verified in demo (set to VERIFIED status)
- Production: integrate with Smile Identity, Sumsub, or Onfido

---

## 🗄 Database Schema

```sql
users       → Credentials, keys, KYC status
wallets     → Per-currency balances (stored in minor units = cents)
transactions → Full TX history with signatures
fx_rates    → Cached exchange rates with spreads
kyc_records → KYC verification records
audit_log   → Append-only compliance trail (NEVER deleted)
```

**Key design decisions:**
- Amounts stored as `INTEGER` (minor units) — avoids floating-point precision bugs
- WAL journal mode — better concurrent read performance
- Indexed on `sender`, `recipient`, `timestamp` — fast user history queries
- `audit_log` is append-only by convention (no DELETE operations)

---

## 🎯 Supported Currencies

| Code | Name | Symbol |
|------|------|--------|
| USD | US Dollar | $ |
| EUR | Euro | € |
| KES | Kenyan Shilling | KES |
| NGN | Nigerian Naira | ₦ |
| GBP | British Pound | £ |

---

## 📊 Algorithm Complexity

| Operation | Time | Space |
|---|---|---|
| Send money | O(n) | O(1) |
| FX conversion | O(1) cached | O(k) pairs |
| Blockchain validation | O(n·m) | O(n·m) |
| Merkle tree build | O(n log n) | O(n) |
| TX lookup by ID | O(1) | — |
| User TX history | O(n) | O(m) |
| Compliance check | O(n) | O(1) |

Where n = blocks/transactions, m = transactions per block, k = currency pairs.

---

## 🔮 Future Improvements (Post-Hackathon)

1. **Real blockchain node:** Connect to Ethereum (Polygon) or Hyperledger Fabric
2. **Real FX API:** Open Exchange Rates, ECB API, Central Bank of Kenya API
3. **USSD gateway:** Africa's Talking USSD API for true offline operation
4. **Biometric auth:** Windows Hello / Touch ID integration
5. **Offline vouchers:** Full NFC/Bluetooth P2P transfer with pre-signed cryptographic vouchers
6. **HSM simulation:** Dedicated key management service
7. **PostgreSQL:** Replace SQLite for multi-user concurrency
8. **Message queue:** Redis/Celery for async transaction processing
9. **KYC integration:** Smile Identity or Onfido for document verification
10. **Real OFAC sanctions:** Chainalysis or ComplyAdvantage API