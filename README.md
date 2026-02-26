# ⬡ ChainPay — Blockchain-Powered Mobile Money

<div align="center">

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?style=flat-square&logo=fastapi)
![SQLite](https://img.shields.io/badge/SQLite-WAL_Mode-003B57?style=flat-square&logo=sqlite)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)
![Status](https://img.shields.io/badge/Status-Production_MVP-brightgreen?style=flat-square)

**A production-grade, multi-currency mobile money platform with a custom permissioned blockchain, AES-256-GCM cryptography, AML compliance engine, and real M-Pesa STK Push integration.**

[Quick Start](#-quick-start) · [Architecture](#-architecture) · [Features](#-features) · [API Reference](#-api-reference) · [Security](#-security) · [Deployment](#-deployment)

</div>

---

## 📋 Table of Contents

- [Overview](#-overview)
- [Features](#-features)
- [Project Structure](#-project-structure)
- [Quick Start](#-quick-start)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Running the Server](#running-the-server)
  - [Running the Desktop Client](#running-the-desktop-client)
- [Configuration](#-configuration)
  - [Client Config](#client-config-chainpay_configjson)
  - [M-Pesa Config](#m-pesa-config-mpesa_configjson)
  - [Environment Variables](#environment-variables)
- [Demo Credentials](#-demo-credentials)
- [Architecture](#-architecture)
  - [System Overview](#system-overview)
  - [Component Breakdown](#component-breakdown)
  - [Database Schema](#database-schema)
  - [Blockchain Design](#blockchain-design)
- [API Reference](#-api-reference)
- [Security](#-security)
- [M-Pesa Integration](#-m-pesa-integration)
- [Compliance Engine](#-compliance-engine)
- [Packaging as Executable](#-packaging-as-executable)
- [Troubleshooting](#-troubleshooting)
- [Roadmap](#-roadmap)

---

## 🌍 Overview

ChainPay is a full-stack fintech application that replicates the core capabilities of M-Pesa — enhanced with a custom blockchain audit layer, multi-currency FX engine, and banking-grade cryptography. It is structured as a **client-server** application:

- A **FastAPI REST server** (`server.py`) manages all financial logic, database writes, and compliance checks
- A **Tkinter desktop client** (`main.py`) communicates exclusively with the server via authenticated HTTP APIs
- A **custom permissioned blockchain** provides an immutable, SHA-3 hashed audit trail of every transaction

> Built for hackathon demonstration with a production-first mindset. Every design decision — from integer balance storage to HMAC transaction signing — reflects real-world fintech engineering standards.

---

## ✨ Features

### 💰 Financial Operations
- **Multi-currency wallets** — USD, EUR, KES, NGN, GBP with independent balances
- **Peer-to-peer transfers** — phone-number-addressed payments with tiered fee engine
- **Currency conversion** — live FX rates with bid/ask spread via USD-pivot model
- **Deposit & Withdrawal** — wallet funding and cash-out flows
- **M-Pesa STK Push** — real Safaricom Daraja API integration for KES deposits

### ⛓ Blockchain Audit Layer
- **Custom Proof-of-Authority blockchain** — every transaction permanently recorded
- **SHA-3-256 hashing** — post-quantum resistant (FIPS 202 compliant)
- **Merkle tree integrity** — tamper detection at the transaction level
- **Chain validation** — full cryptographic verification via `/blockchain/validate`
- **Block explorer** — view recent blocks and pending transactions in the UI

### 🔐 Security & Cryptography
- **AES-256-GCM** encryption for sensitive data at rest
- **PBKDF2-HMAC-SHA256** (100,000 iterations) for PIN key derivation
- **HMAC-SHA3-256** transaction signing — anti-replay with UUID deduplication
- **JWT HS256** sessions with 1-hour expiry and in-memory revocation list
- **Brute-force protection** — 5-attempt lockout with 5-minute cooldown
- **Sliding window rate limiting** — 30 requests per 60-second window

### 🛡 Compliance & AML
- **AML rule engine** — velocity checks, daily limits, structuring detection
- **Sanctions screening** — configurable blocklist (OFAC-style)
- **KYC status tracking** — tiered verification levels
- **SAR auto-generation** — Suspicious Activity Reports for flagged patterns
- **Append-only audit log** — regulator-ready compliance trail

### 🔄 Transaction Management
- **Admin reversal workflow** — user requests → admin approves/rejects with audit trail
- **M-Pesa state machine** — PENDING → CONFIRMED/FAILED/EXPIRED with idempotency
- **Reconciliation engine** — automated ledger vs wallet balance cross-checks
- **Offline payment vouchers** — HMAC-signed pre-authorised payments

---

## 📁 Project Structure

```
chainpay/
│
├── 📄 main.py                  # Desktop GUI client (Tkinter) — connects to server via HTTP
├── 📄 server.py                # FastAPI REST API server — all business logic lives here
│
├── 📄 database.py              # SQLite schema, all DB operations (parameterised queries)
├── 📄 database_mpesa.py        # M-Pesa & reversal schema extension + CRUD
├── 📄 wallet.py                # WalletService, FXEngine, ComplianceEngine, fee calculator
├── 📄 blockchain.py            # Custom permissioned blockchain — SHA-3, Merkle tree, PoA
├── 📄 security.py              # AES-256-GCM, PBKDF2, HMAC, JWT, VoucherSystem
├── 📄 mpesa.py                 # Daraja API — OAuth, STK Push, callback processor
├── 📄 reconsiliation.py        # Automated ledger reconciliation engine
├── 📄 api_client.py            # HTTP client used by GUI — all server calls go here
├── 📄 config.py                # Client configuration loader (reads chainpay_config.json)
├── 📄 gui_mpesa.py             # M-Pesa UI components (dialogs, status polling)
├── 📄 users.py                 # CLI utility — view registered users and balances
│
├── 📄 chainpay_config.json     # Client config — server URL, app version
├── 📄 mpesa_config.json        # M-Pesa Daraja API credentials (never commit real keys)
├── 📄 requirements.txt         # Desktop client dependencies
├── 📄 requirements_server.txt  # Server dependencies (FastAPI, uvicorn, etc.)
│
└── 📄 chainpay.db              # SQLite database (auto-created on first run)
```

### Key Separation of Concerns

| File | Responsibility | Depends On |
|------|---------------|------------|
| `server.py` | API routes, auth middleware, request handling | All core modules |
| `main.py` | GUI rendering, user interaction | `api_client.py`, `config.py` only |
| `api_client.py` | HTTP calls to server | `config.py` |
| `wallet.py` | Money movement, FX, compliance | `database.py`, `blockchain.py`, `security.py` |
| `database.py` | All SQL — schema, CRUD, atomic writes | SQLite stdlib |
| `blockchain.py` | Block creation, Merkle tree, chain validation | stdlib only |
| `security.py` | Encryption, hashing, JWT, vouchers | `cryptography`, `PyJWT` |
| `mpesa.py` | Daraja API calls, callback parsing | stdlib urllib |

---

## 🚀 Quick Start

### Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.10+ | Required for `match` syntax and modern type hints |
| pip | Latest | `python -m pip install --upgrade pip` |
| tkinter | Bundled | Linux: `sudo apt install python3-tk` |
| ngrok | Any | Only needed for live M-Pesa callbacks |

Check your Python version:
```bash
python --version   # Must be 3.10 or higher
```

---

### Installation

**1. Clone or download the project:**
```bash
git clone https://github.com/your-username/chainpay.git
cd chainpay
```

**2. (Recommended) Create a virtual environment:**
```bash
python -m venv venv

# Activate it:
source venv/bin/activate        # macOS / Linux
venv\Scripts\activate           # Windows
```

**3. Install server dependencies:**
```bash
pip install -r requirements_server.txt
```

**4. Install desktop client dependencies:**
```bash
pip install -r requirements.txt
```

> On Ubuntu/Debian, if tkinter is missing:
> ```bash
> sudo apt update && sudo apt install python3-tk
> ```

---

### Running the Server

The FastAPI server **must be running** before the desktop client can function.

```bash
python server.py
```

On first launch, the server will:
1. Create `chainpay.db` with the full schema
2. Seed demo users (`+254700000000` and `+254700000001`)
3. Seed FX rates for all currency pairs
4. Start listening on `http://127.0.0.1:8443`

**Expected output:**
```
INFO:     ChainPay server starting...
INFO:     Database initialised: chainpay.db
INFO:     Demo users seeded
INFO:     Uvicorn running on http://127.0.0.1:8443
```

**Interactive API docs** (Swagger UI) — available while the server runs:
```
http://127.0.0.1:8443/docs
```

---

### Running the Desktop Client

Open a **second terminal** (keep the server running in the first):

```bash
python main.py
```

The client will:
1. Load `chainpay_config.json` to find the server URL
2. Test the connection on startup — a warning dialog appears if the server is unreachable
3. Display the login screen

> **Both terminals must stay open.** The server and client are separate processes.

---

## ⚙️ Configuration

### Client Config (`chainpay_config.json`)

Controls where the desktop client connects. Edit this before distributing the app.

```json
{
  "api_base_url": "http://127.0.0.1:8443",
  "verify_ssl": false,
  "app_name": "ChainPay",
  "app_version": "2.0.0"
}
```

| Key | Description | Examples |
|-----|-------------|---------|
| `api_base_url` | Server URL the client connects to | `http://127.0.0.1:8443` (local), `http://192.168.1.100:8443` (LAN), `https://abc123.ngrok-free.app` (internet) |
| `verify_ssl` | Verify TLS certificate | `false` for self-signed or HTTP; `true` for Let's Encrypt |
| `app_version` | Displayed in the UI header | Any string |

**Deployment scenarios:**

```json
// Local testing only (default)
{ "api_base_url": "http://127.0.0.1:8443" }

// LAN — all clients on same WiFi connect to one laptop running the server
{ "api_base_url": "http://192.168.1.100:8443" }

// Internet — ngrok tunnel exposes the server publicly
{ "api_base_url": "https://your-subdomain.ngrok-free.app", "verify_ssl": true }
```

---

### M-Pesa Config (`mpesa_config.json`)

Required only for live M-Pesa deposits. Uses Safaricom sandbox by default.

```json
{
  "environment": "sandbox",
  "consumer_key": "YOUR_SANDBOX_CONSUMER_KEY_HERE",
  "consumer_secret": "YOUR_SANDBOX_CONSUMER_SECRET_HERE",
  "shortcode": "174379",
  "passkey": "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919",
  "callback_url": "https://YOUR-NGROK-SUBDOMAIN.ngrok-free.app/api/v1/mpesa/callback",
  "account_ref": "ChainPay",
  "transaction_desc": "Wallet Deposit"
}
```

**Setup steps for sandbox testing:**
1. Create an account at [developer.safaricom.co.ke](https://developer.safaricom.co.ke)
2. Create an app to receive `consumer_key` and `consumer_secret`
3. Start ngrok: `ngrok http 8443`
4. Copy the ngrok HTTPS URL into `callback_url`
5. Use the Safaricom [STK Push simulator](https://developer.safaricom.co.ke/APIs/MpesaExpressSimulate) to trigger test payments

> ⚠️ **Never commit real production credentials to git.** Use environment variables in production (see below).

---

### Environment Variables

All M-Pesa credentials can be supplied via environment variables instead of the JSON file. Environment variables take priority.

```bash
# M-Pesa credentials
export MPESA_CONSUMER_KEY="your_key"
export MPESA_CONSUMER_SECRET="your_secret"
export MPESA_SHORTCODE="174379"
export MPESA_PASSKEY="your_passkey"
export MPESA_CALLBACK_URL="https://your-domain.com/api/v1/mpesa/callback"
export MPESA_ENV="sandbox"        # or "production"

# Then start the server
python server.py
```

---

## 🎭 Demo Credentials

These accounts are auto-created when the server starts for the first time:

| Role | Phone Number | PIN | Notes |
|------|-------------|-----|-------|
| Admin | `+254700000000` | `1234` | Full admin panel access |
| User | `+254700000001` | `1234` | Standard user — use as send recipient |

**First login flow:**
1. Launch server → `python server.py`
2. Launch client → `python main.py`
3. Log in with `+254700000000` / `1234`
4. Explore the dashboard — wallets are pre-funded in all currencies

**View all registered users from the terminal:**
```bash
python users.py
```

---

## 🏗 Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    DESKTOP CLIENT (main.py)                  │
│  Tkinter GUI ──► api_client.py ──► HTTP/HTTPS ──► server    │
└─────────────────────────────────────────────────────────────┘
                              │
                    JWT Bearer Token
                              │
┌─────────────────────────────────────────────────────────────┐
│                   FASTAPI SERVER (server.py)                 │
│                                                             │
│  Auth ──► Wallet ──► Compliance ──► DB Write ──► Blockchain │
│           FX Engine    AML Rules    SQLite WAL   SHA-3 PoA  │
│                                                             │
│  M-Pesa STK Push ──► Daraja API ──► Callback ──► Credit     │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         chainpay.db    blockchain       audit_log
         (SQLite WAL)   (in-memory       (append-only
                        + DB persist)    SQL table)
```

---

### Component Breakdown

#### `blockchain.py` — Custom Permissioned Blockchain

| Property | Value |
|----------|-------|
| Consensus | Proof-of-Authority (PoA) — `CHAINPAY_NODE_1` |
| Hash function | SHA-3 256-bit (post-quantum resistant) |
| Block integrity | Merkle tree root per block |
| Chain linkage | Each block stores SHA-3 hash of previous block |
| Block size | 50 transactions max (auto-mines when full) |
| TX lookup | O(1) via `tx_index` dictionary |
| Validation | O(n×m) full chain sweep — hash + Merkle check |

#### `wallet.py` — Financial Engine

- **`WalletService`** — send_money, deposit, withdraw, convert_currency
- **`FXEngine`** — live rate with ±0.3% random walk + 1.5% bid/ask spread + 30s TTL cache
- **`ComplianceEngine`** — pre-transaction AML check: limits, velocity, structuring, sanctions
- **`calculate_fee()`** — tiered fee: 0.5% → 2.0% based on USD equivalent amount

#### `security.py` — Cryptographic Primitives

- **`AESCipher`** — AES-256-GCM with random 96-bit nonce per encryption
- **`hash_password` / `verify_password`** — PBKDF2-HMAC-SHA256, 100k iterations, 256-bit salt
- **`SessionManager`** — JWT HS256, 1hr expiry, jti-based revocation, rate limiting, lockout
- **`VoucherSystem`** — HMAC-signed offline payment vouchers with anti-double-spend serial tracking
- **`generate_keypair()`** — private/public key pair (HMAC demo; production: secp256k1/Ed25519)

---

### Database Schema

```sql
users           → user_id, phone, name, pin_hash, private_key, public_key,
                  kyc_status, role, created_at, last_login, first_login_completed

wallets         → wallet_id, user_id, currency, balance (INTEGER minor units),
                  locked_balance, created_at
                  UNIQUE(user_id, currency)

transactions    → tx_id (UUID PK), sender, recipient, amount (INTEGER), currency,
                  tx_type, fee, timestamp, signature, status, metadata

fx_rates        → pair, rate, bid, ask, updated_at

kyc_records     → record_id, user_id, status, verified_at, document_type

audit_log       → entry_id, timestamp, user_id, action, result, metadata
                  [APPEND-ONLY — never deleted]

mpesa_transactions → mpesa_tx_id, internal_ref (idempotency), user_id, phone,
                     amount_kes, checkout_request_id, status, mpesa_receipt,
                     wallet_credited, chainpay_tx_id, initiated_at, expires_at

reversal_requests  → reversal_id, tx_id, requester_id, reason, status,
                     admin_id, admin_note, created_at, reviewed_at
```

> **Amounts stored as INTEGER minor units** — `125075` represents KES 1,250.75. This eliminates floating-point precision bugs entirely.

---

### Blockchain Design

```
Block N-1                    Block N
┌─────────────────┐         ┌─────────────────┐
│ index: N-1      │         │ index: N         │
│ previous_hash: …│◄────────│ previous_hash: H │
│ merkle_root: M  │         │ merkle_root: M'  │
│ transactions: […│         │ transactions: […]│
│ block_hash: H   │         │ block_hash: H'   │
└─────────────────┘         └─────────────────┘

Merkle tree for Block N:
             Root (M')
            /          \
       H(AB)           H(CD)
      /     \         /     \
  H(TX1) H(TX2)  H(TX3) H(TX4)

Tampering TX2 → H(TX2) changes → H(AB) changes
→ Root changes → merkle_root mismatch → validate_chain() detects it
```

---

## 📡 API Reference

All endpoints are prefixed with `/api/v1/`. Authenticated endpoints require `Authorization: Bearer <token>`.

### Authentication

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/auth/login` | ❌ | Login with phone + PIN; returns JWT |
| `POST` | `/auth/register` | ❌ | Create new account |
| `POST` | `/auth/change-pin` | ✅ | Change PIN for authenticated user |

### Wallet

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/wallet/balances` | ✅ | All wallet balances |
| `POST` | `/wallet/send` | ✅ | Send money by recipient phone |
| `POST` | `/wallet/convert` | ✅ | FX conversion between currencies |
| `POST` | `/wallet/deposit` | ✅ | Deposit to wallet |
| `POST` | `/wallet/withdraw` | ✅ | Withdraw from wallet |
| `GET` | `/wallet/transactions` | ✅ | Transaction history (`?limit=50`) |

### FX & Rates

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/fx/rates` | ❌ | Current exchange rates for all pairs |
| `GET` | `/fx/quote` | ❌ | Conversion quote (`?from=USD&to=KES&amount=100`) |

### Blockchain

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/blockchain/stats` | ✅ | Chain stats: blocks, transactions, validity |
| `GET` | `/blockchain/blocks` | ✅ | Recent blocks (`?n=20`) |
| `POST` | `/blockchain/mine` | ✅ | Force-mine pending transactions |
| `GET` | `/blockchain/validate` | ✅ | Full chain integrity verification |

### M-Pesa

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/mpesa/initiate` | ✅ | Initiate STK Push deposit |
| `POST` | `/mpesa/callback` | ❌ (Safaricom) | M-Pesa payment confirmation webhook |
| `GET` | `/mpesa/status/{ref}` | ✅ | Poll deposit status |
| `GET` | `/mpesa/history` | ✅ | User's M-Pesa deposit history |

### Reversals

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/reversal/eligible` | ✅ | Transactions eligible for reversal |
| `POST` | `/reversal/request` | ✅ | Submit reversal request |

### Admin *(role: admin required)*

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/admin/stats` | ✅ Admin | System overview statistics |
| `GET` | `/admin/users` | ✅ Admin | All registered users |
| `GET` | `/admin/suspicious` | ✅ Admin | AML-flagged transactions |
| `GET` | `/admin/audit-log` | ✅ Admin | Full compliance audit trail |
| `GET` | `/admin/reversals` | ✅ Admin | Pending reversal requests |
| `POST` | `/admin/reversals/{id}/approve` | ✅ Admin | Approve a reversal |
| `POST` | `/admin/reversals/{id}/reject` | ✅ Admin | Reject a reversal |
| `POST` | `/admin/users/{id}/suspend` | ✅ Admin | Suspend a user account |
| `GET` | `/admin/mpesa-transactions` | ✅ Admin | All M-Pesa transactions |

**Swagger UI:** `http://127.0.0.1:8443/docs` — interactive documentation available while server is running.

---

## 🔐 Security

### Cryptographic Stack

| Layer | Algorithm | Standard | Purpose |
|-------|-----------|----------|---------|
| Data encryption | AES-256-GCM | NIST SP 800-38D | Authenticated encryption; tamper detection |
| Key derivation | PBKDF2-HMAC-SHA256, 100k iter | NIST SP 800-132 | PIN → encryption key |
| Password storage | PBKDF2 + 256-bit random salt | OWASP ASVS L2 | One-way hash; timing-safe comparison |
| Transaction signing | HMAC-SHA3-256 | FIPS 202 + RFC 2104 | Anti-replay; non-repudiation |
| Session tokens | JWT HS256 | RFC 7519 | Stateless auth; 1hr expiry + revocation |
| Hashing (blockchain) | SHA-3 256-bit | FIPS 202 | Post-quantum resistant block hashing |
| Randomness | `secrets` module (CSPRNG) | OS entropy | All token/salt/nonce generation |

### Account Protection

- **Rate limiting** — 30 requests per 60-second sliding window per user
- **Login lockout** — account locked for 5 minutes after 5 failed PIN attempts
- **Weak PIN rejection** — common PINs (`1234`, `0000`, `1111`, etc.) rejected at registration
- **Token revocation** — logout immediately invalidates the JWT via jti revocation list
- **Role separation** — `user` and `admin` roles enforced in every JWT payload

### Important Security Notes

> ⚠️ **JWT secret is in-memory only** — lost on server restart. Production must persist to Redis or HashiCorp Vault.

> ⚠️ **Key storage** — cryptographic keys are stored in the SQLite DB (encrypted). Production must use an HSM or KMS (AWS KMS, Azure Key Vault).

> ⚠️ **Argon2id recommended** — current implementation uses PBKDF2. For production, migrate to `argon2-cffi` for memory-hard password hashing.

---

## 📱 M-Pesa Integration

ChainPay integrates with Safaricom's **Daraja API v1** for real mobile money deposits.

### How It Works

```
User requests deposit
       │
       ▼
Server calls STK Push API ──► Safaricom sends USSD prompt to user's phone
       │                                    │
       │                                    ▼
       │                          User enters M-Pesa PIN
       │                                    │
       ▼                                    ▼
PENDING state recorded          Safaricom calls callback URL
       │                                    │
       └────────────────────────────────────┘
                                            │
                                            ▼
                               Server processes callback:
                               ResultCode=0 → credit wallet
                               ResultCode≠0 → mark FAILED
                               Timeout 120s → mark EXPIRED
```

### Transaction State Machine

```
PENDING → CONFIRMED   (ResultCode=0, wallet credited exactly once)
PENDING → FAILED      (ResultCode≠0, no credit)
PENDING → EXPIRED     (120s timeout reached, no credit)
PENDING → DUPLICATE   (duplicate MpesaReceiptNumber, ignored)
```

### Supported Phone Formats

All of the following are normalised to `254XXXXXXXXX` automatically:
- `+254712345678`
- `0712345678`
- `254712345678`

### Supported Safaricom Prefixes
`070`, `071`, `072`, `074`, `075`, `076`, `077`, `078`, `079`, `011`, `010`

---

## 🛡 Compliance Engine

### AML Rules (auto-enforced on every transaction)

| Rule | Limit | Action |
|------|-------|--------|
| Single transaction | $2,000 max | Transaction rejected |
| Daily volume | $5,000 per 24h | Transaction rejected |
| Hourly velocity | 10 transactions/hour | Transaction rejected |
| Structuring detection | 3+ transactions of $900–$1,000 in 24h | SAR flag generated |
| Sanctions screening | Hardcoded blocklist | Transaction rejected |

### KYC Tiers

| Status | Capability |
|--------|-----------|
| `PENDING` | Account created, not yet verified |
| `VERIFIED` | Full transaction access (all demo accounts) |
| `REJECTED` | Account restricted — no transactions |
| `SUSPENDED` | Admin-suspended — no access |

### Audit Log

Every security-relevant event is written to the `audit_log` table:
- All transaction outcomes (success and failure)
- Login attempts (successful and failed)
- AML rule violations and SAR flags
- Admin actions (approvals, suspensions, reversals)
- Configuration changes

> The audit log is **append-only by convention** — no `DELETE` or `UPDATE` operations are ever performed on it.

---

## 📦 Packaging as Executable

### Windows (.exe)

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name ChainPay main.py
```

The `.exe` will appear at `dist/ChainPay.exe`. Double-click to run — no Python installation required on the target machine.

**With custom icon:**
```bash
pyinstaller --onefile --windowed --name ChainPay --icon=icon.ico \
    --add-data "chainpay_config.json;." main.py
```

**Include the database (for pre-seeded demo data):**
```bash
pyinstaller --onefile --windowed --name ChainPay \
    --add-data "chainpay.db;." \
    --add-data "chainpay_config.json;." \
    main.py
```

### macOS (.app)

```bash
pyinstaller --onefile --windowed --name ChainPay \
    --add-data "chainpay_config.json:." main.py
```

> Remember: `chainpay_config.json` must sit next to the executable so the client knows which server URL to connect to.

---

## 🔧 Troubleshooting

### "Cannot connect to server"
```
Make sure the server is running first:
  python server.py

Then launch the client in a separate terminal:
  python main.py

Check the server URL in chainpay_config.json matches where the server is listening.
```

### "Module not found: tkinter"
```bash
# Ubuntu / Debian
sudo apt update && sudo apt install python3-tk

# Fedora / RHEL
sudo dnf install python3-tkinter

# macOS (if using Homebrew Python)
brew install python-tk
```

### "Module not found: cryptography / jwt"
```bash
pip install -r requirements_server.txt
pip install -r requirements.txt
```

### M-Pesa STK Push not delivering
1. Verify `callback_url` in `mpesa_config.json` is a publicly accessible HTTPS URL
2. Start ngrok: `ngrok http 8443` and update the callback URL with the new ngrok address
3. Confirm the phone number starts with a valid Safaricom prefix (`07x` or `01x`)
4. In sandbox mode, use the [Safaricom simulator](https://developer.safaricom.co.ke/APIs/MpesaExpressSimulate) to manually trigger a callback

### "Blockchain chain invalid" warning
This indicates the in-memory blockchain was reset (server restarted) but the `tx_index` is now out of sync with previously persisted blocks. 

Restart the server cleanly:
```bash
# Stop the server (Ctrl+C), then:
python server.py
```

### "Account locked" on login
Wait 5 minutes, or as admin, clear the lockout via the admin panel or by restarting the server (lockout state is in-memory).

### View raw database contents
```bash
python users.py          # Lists all users and their wallet balances

# Or directly with SQLite CLI:
sqlite3 chainpay.db
sqlite> SELECT phone, name, kyc_status FROM users;
sqlite> SELECT user_id, currency, balance/100.0 as balance FROM wallets;
```

---

## 🗺 Roadmap

### Near-term (Production Hardening)
- [ ] PostgreSQL migration with row-level locking and read replicas
- [ ] Redis for JWT revocation, rate limiting, and session state
- [ ] Argon2id password hashing (replace PBKDF2)
- [ ] HashiCorp Vault or AWS KMS for cryptographic key storage
- [ ] Real OFAC/Chainalysis sanctions API integration
- [ ] Smile Identity or Onfido KYC integration
- [ ] External penetration test + SAST/DAST scanning

### Medium-term (Feature Expansion)
- [ ] Kafka-based async transaction processing
- [ ] Mobile app (Flutter) with shared server backend
- [ ] USSD gateway via Africa's Talking for feature phone support
- [ ] Multi-region deployment with automated failover
- [ ] OpenTelemetry distributed tracing
- [ ] ELK/OpenSearch centralised logging

### Long-term (Platform)
- [ ] Real blockchain node (Hyperledger Fabric or Polygon PoS)
- [ ] Open banking API (partner wallet SDK)
- [ ] Webhook subscription system for real-time notifications
- [ ] ML-based fraud scoring pipeline
- [ ] Developer portal with API key management
- [ ] Biometric authentication (Touch ID / Windows Hello)

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 🙏 Acknowledgements

- [Safaricom Daraja API](https://developer.safaricom.co.ke) — M-Pesa STK Push integration
- [FastAPI](https://fastapi.tiangolo.com) — async Python REST framework
- [cryptography](https://cryptography.io) — AES-GCM and PBKDF2 primitives
- [PyJWT](https://pyjwt.readthedocs.io) — JWT implementation

---

<div align="center">
  <sub>Built with 🔐 security-first principles · ChainPay v2.0</sub>
</div>