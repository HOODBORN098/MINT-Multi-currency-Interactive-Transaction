"""
ChainPay — server_mpesa_routes.py
===================================
FastAPI route handlers to be registered on the main `app` in server.py.

ADD TO server.py:
    from server_mpesa_routes import register_mpesa_routes
    register_mpesa_routes(app)

Also add at startup (after init_db()):
    from database_mpesa import init_mpesa_schema
    init_mpesa_schema()

This module provides:
  POST /api/v1/mpesa/initiate          — User starts M-Pesa deposit
  POST /api/v1/mpesa/callback          — Safaricom webhook (no auth)
  GET  /api/v1/mpesa/status/{ref}      — Poll deposit status
  GET  /api/v1/mpesa/history           — User's M-Pesa deposit history
  POST /api/v1/reversal/request        — User requests a reversal
  GET  /api/v1/reversal/eligible       — List eligible transactions
  GET  /api/v1/admin/reversals         — Admin: pending reversals
  POST /api/v1/admin/reversals/{id}/approve
  POST /api/v1/admin/reversals/{id}/reject
  GET  /api/v1/admin/mpesa-transactions — Admin: all M-Pesa transactions
  GET  /api/v1/help                    — Help panel content
"""

import json
import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, validator

# These imports assume the project layout where server.py is in the root
# and core/ contains blockchain.py, security.py, database.py, wallet.py
import core.database as db
from core.wallet import WalletService
from core.security import get_session_manager
from core.blockchain import get_blockchain, Transaction as BCTransaction

import mpesa as mpesa_lib
import database_mpesa as mpesa_db

logger = logging.getLogger("chainpay.mpesa_routes")

bearer = HTTPBearer(auto_error=True)


# ── Auth helpers (mirrors server.py) ─────────────────────────────────────────

def _require_auth(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
    sm      = get_session_manager()
    payload = sm.verify_token(creds.credentials)
    if not payload:
        raise HTTPException(401, "Invalid or expired token")
    user = db.get_user_by_id(payload["sub"])
    if not user or user.get("is_suspended"):
        raise HTTPException(403, "Account suspended or not found")
    return payload


def _require_admin(payload: dict = Depends(_require_auth)) -> dict:
    if payload.get("role") not in ("admin", "compliance"):
        raise HTTPException(403, "Admin access required")
    return payload


# ── Request models ────────────────────────────────────────────────────────────

class MpesaDepositRequest(BaseModel):
    phone:  str   = Field(..., description="M-Pesa phone number (e.g. +254700000000)")
    amount: float = Field(..., gt=0, le=150000, description="Amount in KES (max 150,000)")

    @validator("phone")
    def validate_phone(cls, v):
        ok, result = mpesa_lib.validate_kenyan_phone(v)
        if not ok:
            raise ValueError(result)
        return v

    @validator("amount")
    def validate_amount(cls, v):
        if v < 1:
            raise ValueError("Minimum deposit is KES 1")
        if v > 150000:
            raise ValueError("Maximum single deposit is KES 150,000 (M-Pesa limit)")
        return round(v, 2)


class ReversalRequest(BaseModel):
    tx_id:  str = Field(..., description="Transaction ID to reverse")
    reason: str = Field("", max_length=200)


class AdminReversalAction(BaseModel):
    note: str = Field("", max_length=300)


# ── Route factory ─────────────────────────────────────────────────────────────

def register_mpesa_routes(app):
    """Register all M-Pesa and reversal routes on the FastAPI app."""

    router = APIRouter()

    # ── 1. Initiate M-Pesa Deposit ─────────────────────────────────────────

    @router.post("/api/v1/mpesa/initiate")
    def mpesa_initiate(
        req: MpesaDepositRequest,
        payload: dict = Depends(_require_auth),
    ):
        """
        Initiate an M-Pesa STK Push deposit.

        Flow:
          1. Validate phone & amount
          2. Create PENDING mpesa_transaction record
          3. Call Daraja STK Push API
          4. Store CheckoutRequestID
          5. Return {mpesa_tx_id, checkout_request_id, message}

        Wallet is NOT credited here — only after confirmed callback.
        """
        user_id = payload["sub"]

        # Create pending record BEFORE calling Safaricom (idempotency)
        pending = mpesa_db.create_mpesa_tx(
            user_id   = user_id,
            phone     = req.phone,
            amount_kes= req.amount,
        )

        try:
            stk_resp = mpesa_lib.initiate_stk_push(
                phone_number = req.phone,
                amount       = int(req.amount),
                account_ref  = "ChainPay",
                description  = "Wallet Deposit",
                internal_ref = pending["internal_ref"],
            )
        except RuntimeError as e:
            # Update record to FAILED so UI can show proper error
            mpesa_db.fail_mpesa_tx(
                pending["internal_ref"], -1, str(e), {}
            )
            raise HTTPException(502, f"M-Pesa service error: {e}")

        # Check Safaricom accepted the request
        if stk_resp.get("ResponseCode") != "0":
            mpesa_db.fail_mpesa_tx(
                pending["internal_ref"],
                -1,
                stk_resp.get("ResponseDescription", "STK Push rejected"),
                stk_resp,
            )
            raise HTTPException(502, stk_resp.get("ResponseDescription", "STK Push failed"))

        # Store Safaricom's tracking IDs
        mpesa_db.update_mpesa_checkout_id(
            pending["internal_ref"],
            stk_resp["CheckoutRequestID"],
            stk_resp["MerchantRequestID"],
        )

        db.audit_action(user_id, "MPESA_STK_INITIATED", {
            "amount_kes":         req.amount,
            "phone":              req.phone,
            "checkout_request_id": stk_resp["CheckoutRequestID"],
        })

        return {
            "message":            "M-Pesa STK Push sent. Please check your phone and enter your PIN.",
            "mpesa_tx_id":        pending["mpesa_tx_id"],
            "internal_ref":       pending["internal_ref"],
            "checkout_request_id": stk_resp["CheckoutRequestID"],
            "customer_message":   stk_resp.get("CustomerMessage", ""),
            "expires_in_seconds": 120,
        }


    # ── 2. Safaricom Callback (no authentication — public webhook) ──────────

    @router.post("/api/v1/mpesa/callback")
    async def mpesa_callback(request: Request, background: BackgroundTasks):
        """
        Safaricom STK Push result callback.

        CRITICAL SECURITY NOTES:
          - This endpoint has no JWT auth (Safaricom cannot send JWT).
          - We validate authenticity by matching CheckoutRequestID to our own DB record.
          - We validate amount matches what we initiated.
          - MpesaReceiptNumber deduplication prevents double-credits.
          - Wallet credited ONLY inside an ACID transaction after all validations pass.

        Always returns HTTP 200 to Safaricom (even on our internal failure)
        to prevent Safaricom from retrying indefinitely.
        """
        try:
            body = await request.json()
        except Exception:
            logger.error("Callback: could not parse JSON body")
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        logger.info(f"M-Pesa callback received: {json.dumps(body)[:500]}")

        parsed = mpesa_lib.process_callback(body)
        checkout_id = parsed["checkout_request_id"]

        if not checkout_id:
            logger.warning("Callback: missing CheckoutRequestID")
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        # ── Idempotency: find our pending record ───────────────────────────
        record = mpesa_db.get_mpesa_tx_by_checkout(checkout_id)
        if not record:
            logger.warning(f"Callback: unknown CheckoutRequestID {checkout_id}")
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        # Already processed?
        if record["status"] not in ("PENDING",):
            logger.info(f"Callback: already processed (status={record['status']}), ignoring.")
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        if not parsed["success"]:
            # Payment failed or cancelled
            mpesa_db.fail_mpesa_tx(
                record["internal_ref"],
                parsed["result_code"],
                parsed["result_desc"],
                body,
            )
            logger.info(f"M-Pesa payment failed: {parsed['result_desc']} (code {parsed['result_code']})")
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        # ── Duplicate receipt guard ────────────────────────────────────────
        if parsed["receipt_number"] and mpesa_db.get_mpesa_tx_by_receipt(parsed["receipt_number"]):
            logger.error(f"DUPLICATE RECEIPT DETECTED: {parsed['receipt_number']} — ignoring.")
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        # ── Amount validation ──────────────────────────────────────────────
        expected_kes = record["amount_kes"] / 100.0  # convert minor units
        received_kes = parsed["amount"]
        if abs(received_kes - expected_kes) > 1.0:  # allow ±1 KES tolerance
            logger.error(
                f"Amount mismatch! Expected {expected_kes} KES, received {received_kes} KES. "
                f"receipt={parsed['receipt_number']}"
            )
            mpesa_db.fail_mpesa_tx(
                record["internal_ref"], -98,
                f"Amount mismatch: expected {expected_kes}, got {received_kes}",
                body,
            )
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        # ── ATOMIC: Credit wallet + record transaction ─────────────────────
        background.add_task(
            _credit_wallet_for_mpesa,
            record       = record,
            received_kes = received_kes,
            parsed       = parsed,
            raw_callback = body,
        )

        return {"ResultCode": 0, "ResultDesc": "Accepted"}


    def _credit_wallet_for_mpesa(record: dict, received_kes: float,
                                  parsed: dict, raw_callback: dict):
        """
        Execute the atomic wallet credit. Runs as a background task so the
        HTTP response to Safaricom is not blocked by DB writes.

        Uses WalletService.deposit() which already wraps everything in a
        get_db() context (ACID). We then update the mpesa_transactions record.
        """
        user_id = record["user_id"]

        ok, msg, tx_result = WalletService.deposit(
            user_id  = user_id,
            amount   = received_kes,
            currency = "KES",
            method   = f"MPESA:{parsed['receipt_number']}",
        )

        if not ok:
            logger.error(f"Wallet credit failed for receipt {parsed['receipt_number']}: {msg}")
            mpesa_db.fail_mpesa_tx(
                record["internal_ref"], -97,
                f"Wallet credit failed: {msg}",
                raw_callback,
            )
            return

        # Confirm M-Pesa record
        mpesa_db.confirm_mpesa_tx(
            internal_ref    = record["internal_ref"],
            result_code     = parsed["result_code"],
            result_desc     = parsed["result_desc"],
            amount_kes      = received_kes,
            receipt         = parsed["receipt_number"],
            mpesa_phone     = parsed["phone"],
            chainpay_tx_id  = tx_result["tx_id"],
            raw_callback    = raw_callback,
        )

        # Record on blockchain
        bc = get_blockchain()
        try:
            bc.add_transaction(BCTransaction(
                tx_id     = tx_result["tx_id"],
                sender    = "MPESA",
                recipient = user_id,
                amount    = received_kes,
                currency  = "KES",
                tx_type   = "MPESA_DEPOSIT",
                fee       = 0.0,
                timestamp = time.time(),
                metadata  = {
                    "receipt":  parsed["receipt_number"],
                    "phone":    parsed["phone"],
                },
            ))
        except ValueError:
            pass  # Blockchain already has this tx_id (duplicate add guard)

        db.audit_action(user_id, "MPESA_DEPOSIT_CONFIRMED", {
            "amount_kes":    received_kes,
            "receipt":       parsed["receipt_number"],
            "chainpay_tx_id": tx_result["tx_id"],
        })

        logger.info(
            f"✓ KES {received_kes:.2f} credited to user {user_id} "
            f"| receipt: {parsed['receipt_number']}"
        )


    # ── 3. Poll deposit status ─────────────────────────────────────────────

    @router.get("/api/v1/mpesa/status/{internal_ref}")
    def mpesa_status(
        internal_ref: str,
        payload: dict = Depends(_require_auth),
    ):
        """
        Poll the status of a pending M-Pesa deposit.
        Returns: {status, amount_kes, receipt_number, result_desc, wallet_credited}
        """
        with mpesa_db._get_db() as conn:
            row = conn.execute(
                "SELECT * FROM mpesa_transactions WHERE internal_ref=? AND user_id=?",
                (internal_ref, payload["sub"])
            ).fetchone()

        if not row:
            raise HTTPException(404, "Deposit record not found")

        r = dict(row)
        return {
            "status":         r["status"],
            "amount_kes":     r["amount_kes"] / 100.0,
            "receipt_number": r["mpesa_receipt"],
            "result_desc":    r["result_desc"],
            "wallet_credited": bool(r["wallet_credited"]),
            "initiated_at":   r["initiated_at"],
            "expires_at":     r["expires_at"],
        }


    # ── 4. User M-Pesa deposit history ────────────────────────────────────

    @router.get("/api/v1/mpesa/history")
    def mpesa_history(payload: dict = Depends(_require_auth)):
        records = mpesa_db.get_user_mpesa_history(payload["sub"])
        for r in records:
            r["amount_kes"] = r["amount_kes"] / 100.0
        return {"deposits": records}


    # ── 5. List reversal-eligible transactions ─────────────────────────────

    @router.get("/api/v1/reversal/eligible")
    def reversal_eligible(payload: dict = Depends(_require_auth)):
        """
        Returns transactions eligible for reversal:
          - type = SEND
          - status = CONFIRMED
          - timestamp within last 24 hours
          - no existing PENDING/APPROVED reversal request
        """
        user_id = payload["sub"]
        since   = time.time() - 86400
        txs = db.get_user_transactions(user_id, limit=200)

        eligible = []
        for tx in txs:
            if (tx["tx_type"] == "SEND"
                    and tx["status"] == "CONFIRMED"
                    and tx["sender"] == user_id
                    and tx["timestamp"] >= since):
                # Check no existing reversal request
                with mpesa_db._get_db() as conn:
                    existing = conn.execute(
                        "SELECT reversal_id FROM reversal_requests "
                        "WHERE tx_id=? AND status IN ('PENDING','APPROVED','COMPLETED')",
                        (tx["tx_id"],)
                    ).fetchone()
                if not existing:
                    eligible.append({
                        "tx_id":     tx["tx_id"],
                        "amount":    tx["amount"] / 100.0,
                        "currency":  tx["currency"],
                        "recipient": tx.get("metadata", {}).get("recipient_phone", tx["recipient"]),
                        "timestamp": tx["timestamp"],
                        "note":      tx.get("metadata", {}).get("note", ""),
                    })
        return {"eligible_transactions": eligible}


    # ── 6. Request a reversal ──────────────────────────────────────────────

    @router.post("/api/v1/reversal/request")
    def request_reversal(
        req: ReversalRequest,
        payload: dict = Depends(_require_auth),
    ):
        """
        User requests a reversal of a completed SEND transaction.
        Must be within 24 hours.
        """
        user_id = payload["sub"]

        # Verify the transaction belongs to this user
        tx = db.get_transaction_by_id(req.tx_id) if hasattr(db, "get_transaction_by_id") else _get_tx(req.tx_id)
        if not tx:
            raise HTTPException(404, "Transaction not found")
        if tx["sender"] != user_id:
            raise HTTPException(403, "You can only reverse your own transactions")
        if tx["tx_type"] != "SEND":
            raise HTTPException(400, "Only SEND transactions can be reversed")
        if tx["status"] != "CONFIRMED":
            raise HTTPException(400, "Only confirmed transactions can be reversed")
        if time.time() - tx["timestamp"] > 86400:
            raise HTTPException(400, "Reversal window has expired (24 hours)")

        try:
            reversal = mpesa_db.create_reversal_request(
                tx_id        = req.tx_id,
                requester_id = user_id,
                reason       = req.reason,
            )
        except ValueError as e:
            raise HTTPException(409, str(e))

        db.audit_action(user_id, "REVERSAL_REQUESTED", {
            "tx_id": req.tx_id, "reversal_id": reversal["reversal_id"],
            "reason": req.reason,
        })

        return {
            "message":    "Reversal request submitted. An admin will review it shortly.",
            "reversal_id": reversal["reversal_id"],
        }


    # ── 7. Admin: pending reversals ────────────────────────────────────────

    @router.get("/api/v1/admin/reversals")
    def admin_reversals(
        include_history: bool = False,
        payload: dict = Depends(_require_admin),
    ):
        if include_history:
            return {"reversals": mpesa_db.get_reversal_history()}
        return {"reversals": mpesa_db.get_pending_reversals()}


    # ── 8. Admin: approve reversal ─────────────────────────────────────────

    @router.post("/api/v1/admin/reversals/{reversal_id}/approve")
    def admin_approve_reversal(
        reversal_id: str,
        req: AdminReversalAction,
        payload: dict = Depends(_require_admin),
    ):
        """
        Approve a reversal:
          1. Fetch original transaction
          2. Atomic: debit recipient, credit sender
          3. Mark original tx as REVERSED
          4. Complete reversal record
        """
        admin_id = payload["sub"]
        reversal = mpesa_db.get_reversal_request(reversal_id)
        if not reversal:
            raise HTTPException(404, "Reversal request not found")
        if reversal["status"] != "PENDING":
            raise HTTPException(400, f"Reversal already {reversal['status']}")

        tx = _get_tx(reversal["tx_id"])
        if not tx:
            raise HTTPException(404, "Original transaction not found")

        amount   = tx["amount"] / 100.0
        currency = tx["currency"]
        sender   = tx["sender"]
        recipient= tx["recipient"]

        # ── Atomic reversal ──────────────────────────────────────────────
        try:
            with db.get_db() as conn:
                # Debit recipient
                if not db.update_balance(conn, recipient, currency, -tx["amount"]):
                    raise HTTPException(400, "Recipient has insufficient balance to reverse")
                # Credit sender
                db.update_balance(conn, sender, currency, tx["amount"])
                # Mark original TX as REVERSED
                conn.execute(
                    "UPDATE transactions SET status='REVERSED' WHERE tx_id=?",
                    (tx["tx_id"],)
                )
                # Insert reversal transaction record
                rev_tx_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO transactions
                       (tx_id, sender, recipient, amount, currency, tx_type,
                        fee, timestamp, status, metadata)
                       VALUES (?,?,?,?,?,'REVERSAL',0,?,'CONFIRMED',?)""",
                    (rev_tx_id, recipient, sender, tx["amount"], currency,
                     time.time(), json.dumps({"original_tx_id": tx["tx_id"],
                                              "reversal_id": reversal_id}))
                )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"Reversal execution failed: {e}")

        mpesa_db.approve_reversal(reversal_id, admin_id, req.note)
        mpesa_db.complete_reversal(reversal_id)

        db.audit_action(admin_id, "REVERSAL_APPROVED", {
            "reversal_id": reversal_id,
            "tx_id":       tx["tx_id"],
            "amount":      amount,
            "currency":    currency,
        })
        db.audit_action(sender, "REVERSAL_CREDITED", {
            "reversal_id": reversal_id,
            "amount":      amount,
            "currency":    currency,
        })

        return {
            "message":    "Reversal approved and balances updated.",
            "reversal_id": reversal_id,
            "reversed_tx_id": tx["tx_id"],
            "new_tx_id":  rev_tx_id,
        }


    # ── 9. Admin: reject reversal ──────────────────────────────────────────

    @router.post("/api/v1/admin/reversals/{reversal_id}/reject")
    def admin_reject_reversal(
        reversal_id: str,
        req: AdminReversalAction,
        payload: dict = Depends(_require_admin),
    ):
        admin_id = payload["sub"]
        reversal = mpesa_db.get_reversal_request(reversal_id)
        if not reversal:
            raise HTTPException(404, "Reversal request not found")
        if reversal["status"] != "PENDING":
            raise HTTPException(400, f"Reversal already {reversal['status']}")

        mpesa_db.reject_reversal(reversal_id, admin_id, req.note)
        db.audit_action(admin_id, "REVERSAL_REJECTED", {
            "reversal_id": reversal_id,
            "note":        req.note,
        })

        return {"message": "Reversal request rejected.", "reversal_id": reversal_id}


    # ── 10. Admin: all M-Pesa transactions ─────────────────────────────────

    @router.get("/api/v1/admin/mpesa-transactions")
    def admin_mpesa_txs(
        status: Optional[str] = None,
        limit: int = 200,
        payload: dict = Depends(_require_admin),
    ):
        records = mpesa_db.get_all_mpesa_txs(status=status, limit=limit)
        for r in records:
            r["amount_kes"] = r["amount_kes"] / 100.0
        return {"transactions": records}


    # ── 11. Help panel ─────────────────────────────────────────────────────

    @router.get("/api/v1/help")
    def get_help():
        """
        Returns structured help content for the in-app Help Panel.
        No authentication required (public content).
        """
        return {
            "sections": [
                {
                    "title": "How to Deposit Using M-Pesa",
                    "icon":  "📱",
                    "steps": [
                        "Tap 'Deposit' on your dashboard.",
                        "Enter or confirm your M-Pesa phone number.",
                        "Enter the amount in KES you wish to deposit.",
                        "Tap 'Deposit via M-Pesa'.",
                        "A prompt will appear on your phone — enter your M-Pesa PIN.",
                        "Wait a moment. Your wallet will be credited automatically once Safaricom confirms payment.",
                        "You will see a confirmation on screen and receive an M-Pesa SMS receipt.",
                    ],
                    "note": "Minimum deposit: KES 1. Maximum: KES 150,000 per transaction."
                },
                {
                    "title": "How to Send Money",
                    "icon":  "💸",
                    "steps": [
                        "Tap 'Send Money' on your dashboard.",
                        "Enter the recipient's phone number (must be a registered ChainPay user).",
                        "Enter the amount and select the currency.",
                        "Review the confirmation screen: recipient name, amount, fee, and total deduction.",
                        "Tap 'Confirm' to execute the transfer.",
                        "The recipient is credited instantly.",
                    ],
                    "note": "Ensure you verify the recipient's name before confirming."
                },
                {
                    "title": "How Currency Conversion Works",
                    "icon":  "💱",
                    "steps": [
                        "Your base currency is KES.",
                        "To convert, go to 'Convert Currency' and select the destination currency.",
                        "You will see the current exchange rate and a 1.5% spread fee.",
                        "Confirm the conversion — KES is debited and the target currency is credited.",
                        "Conversion rates update every 30 seconds.",
                    ],
                    "note": "All conversions are final and cannot be reversed."
                },
                {
                    "title": "What to Do If You Sent Money to the Wrong Person",
                    "icon":  "⚠️",
                    "steps": [
                        "Act quickly — reversals are only possible within 24 hours.",
                        "Go to 'Transaction History' and find the transaction.",
                        "Tap 'Request Reversal' and explain the reason.",
                        "An admin will review and contact both parties.",
                        "If approved, the amount is returned to your wallet.",
                    ],
                    "note": "Reversals are NOT guaranteed. Always double-check the recipient before sending."
                },
                {
                    "title": "Reversal Process",
                    "icon":  "🔄",
                    "content": (
                        "Reversals are admin-controlled for security. "
                        "Once you submit a request, it enters a review queue. "
                        "The admin verifies both sides and — if approved — "
                        "atomically restores your balance and deducts from the recipient. "
                        "You will receive an in-app notification with the outcome."
                    )
                },
                {
                    "title": "Contact Support",
                    "icon":  "🆘",
                    "content": (
                        "For urgent issues, contact us at support@chainpay.app or "
                        "call our helpline. For M-Pesa disputes, you can also contact "
                        "Safaricom directly on *234#."
                    )
                },
                {
                    "title": "Security Best Practices",
                    "icon":  "🔐",
                    "tips": [
                        "Never share your PIN with anyone, including ChainPay staff.",
                        "Log out after each session on shared devices.",
                        "Enable phone screen lock to protect your M-Pesa.",
                        "Verify the recipient name on every transfer before confirming.",
                        "If you suspect unauthorized access, change your PIN immediately.",
                        "ChainPay will never ask for your M-Pesa PIN via SMS or call.",
                    ]
                },
            ]
        }

    app.include_router(router)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_tx(tx_id: str) -> Optional[dict]:
    """Fetch a single transaction by ID."""
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM transactions WHERE tx_id=?", (tx_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["metadata"] = json.loads(d.get("metadata") or "{}")
        except Exception:
            d["metadata"] = {}
        return d