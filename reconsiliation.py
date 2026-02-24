"""
ChainPay GUI — M-Pesa Deposit, Reversal, and Help Panels
==========================================================
Paste these methods into the ChainPayApp class in main.py.

Usage in main.py:
  1. Import: from gui_mpesa import (show_mpesa_deposit_panel,
                                    show_reversal_panel, show_help_panel)
  2. Bind buttons in build_dashboard():
       ttk.Button(..., text="Deposit via M-Pesa", command=self.show_mpesa_deposit_panel)
       ttk.Button(..., text="Request Reversal",   command=self.show_reversal_panel)
       ttk.Button(..., text="Help",               command=self.show_help_panel)
  3. Add `from gui_mpesa import *` and call super().__init__() appropriately,
     OR copy-paste these methods directly into ChainPayApp.

All API calls go through api_client.py — no direct DB access from GUI.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time


# ─── M-Pesa Deposit Panel ────────────────────────────────────────────────────

def show_mpesa_deposit_panel(self):
    """
    M-Pesa STK Push deposit panel.
    Presents phone confirmation + amount entry, then polls for result.
    """
    win = tk.Toplevel(self.root)
    win.title("Deposit via M-Pesa")
    win.geometry("440x420")
    win.resizable(False, False)
    win.grab_set()

    # ── Header ────────────────────────────────────────────────────────────
    header = tk.Frame(win, bg="#4CAF50", pady=14)
    header.pack(fill="x")
    tk.Label(header, text="📱 Deposit via M-Pesa",
             font=("Helvetica", 16, "bold"),
             bg="#4CAF50", fg="white").pack()
    tk.Label(header, text="Lipa Na M-Pesa — Wallet top-up",
             font=("Helvetica", 10), bg="#4CAF50", fg="#e8f5e9").pack()

    body = tk.Frame(win, padx=24, pady=18, bg="white")
    body.pack(fill="both", expand=True)

    # ── Phone field ────────────────────────────────────────────────────────
    tk.Label(body, text="M-Pesa Phone Number", font=("Helvetica", 10, "bold"),
             bg="white", anchor="w").pack(fill="x")
    tk.Label(body, text="Enter the phone registered with M-Pesa",
             font=("Helvetica", 9), fg="gray", bg="white", anchor="w").pack(fill="x")

    phone_var = tk.StringVar()
    # Pre-fill with user's own phone
    try:
        import api_client as api
        current = api.get_current_user()
        if current:
            phone_var.set(current.get("phone", ""))
    except Exception:
        pass

    phone_entry = ttk.Entry(body, textvariable=phone_var, font=("Helvetica", 12))
    phone_entry.pack(fill="x", pady=(4, 14))

    # ── Amount field ───────────────────────────────────────────────────────
    tk.Label(body, text="Amount (KES)", font=("Helvetica", 10, "bold"),
             bg="white", anchor="w").pack(fill="x")
    tk.Label(body, text="Minimum: KES 1   •   Maximum: KES 150,000",
             font=("Helvetica", 9), fg="gray", bg="white", anchor="w").pack(fill="x")

    amount_var = tk.StringVar()
    amount_entry = ttk.Entry(body, textvariable=amount_var, font=("Helvetica", 12))
    amount_entry.pack(fill="x", pady=(4, 18))

    # ── Status / result label ──────────────────────────────────────────────
    status_var = tk.StringVar(value="")
    status_lbl = tk.Label(body, textvariable=status_var, bg="white",
                           font=("Helvetica", 10), wraplength=380,
                           justify="left", fg="#1565C0")
    status_lbl.pack(fill="x", pady=(0, 10))

    # ── Deposit button ─────────────────────────────────────────────────────
    deposit_btn = ttk.Button(body, text="Send M-Pesa STK Push →")
    deposit_btn.pack(fill="x", pady=(0, 8))

    tk.Label(body, text="You will receive a PIN prompt on your phone.",
             font=("Helvetica", 9), fg="gray", bg="white").pack()

    def do_deposit():
        phone  = phone_var.get().strip()
        amount_str = amount_var.get().strip()

        if not phone:
            messagebox.showerror("Error", "Please enter your M-Pesa phone number.", parent=win)
            return
        try:
            amount = float(amount_str)
            if amount <= 0:
                raise ValueError()
        except ValueError:
            messagebox.showerror("Error", "Please enter a valid amount.", parent=win)
            return

        deposit_btn.config(state="disabled")
        status_var.set("⏳ Initiating M-Pesa STK Push…")
        win.update()

        def worker():
            try:
                import api_client as api
                result = api._post("api/v1/mpesa/initiate", {
                    "phone": phone,
                    "amount": amount,
                })
                internal_ref = result.get("internal_ref")
                status_var.set(
                    f"✅ STK Push sent!\n"
                    f"{result.get('customer_message', 'Check your phone and enter your M-Pesa PIN.')}\n\n"
                    f"Waiting for confirmation…"
                )
                # Poll for up to 120 seconds
                _poll_mpesa_status(win, status_var, deposit_btn, internal_ref,
                                   amount, self)
            except RuntimeError as e:
                status_var.set(f"❌ {e}")
                deposit_btn.config(state="normal")

        threading.Thread(target=worker, daemon=True).start()

    deposit_btn.config(command=do_deposit)


def _poll_mpesa_status(win, status_var, btn, internal_ref, amount, app_self,
                       attempts=0, max_attempts=24):
    """
    Recursively poll deposit status every 5 seconds for up to 2 minutes.
    """
    if attempts >= max_attempts:
        status_var.set(
            "⏰ Timeout: No response received within 2 minutes.\n"
            "If you entered your PIN, the deposit may still arrive shortly.\n"
            "Check your transaction history."
        )
        btn.config(state="normal")
        return

    def check():
        try:
            import api_client as api
            result = api._get(f"api/v1/mpesa/status/{internal_ref}")
            status = result.get("status")

            if status == "CONFIRMED":
                status_var.set(
                    f"✅ Deposit confirmed!\n"
                    f"KES {amount:,.2f} has been added to your wallet.\n"
                    f"Receipt: {result.get('receipt_number', 'N/A')}"
                )
                btn.config(state="normal")
                # Refresh dashboard balances
                try:
                    app_self.refresh_balances()
                except Exception:
                    pass

            elif status == "FAILED":
                status_var.set(
                    f"❌ Payment failed.\n{result.get('result_desc', 'Please try again.')}"
                )
                btn.config(state="normal")

            elif status == "EXPIRED":
                status_var.set("⏰ STK Push expired. Please try again.")
                btn.config(state="normal")

            else:
                # Still PENDING — schedule next poll
                status_var.set(
                    f"⏳ Waiting for M-Pesa confirmation… ({(attempts+1)*5}s elapsed)\n"
                    "Please enter your PIN on your phone if prompted."
                )
                win.after(5000, lambda: _poll_mpesa_status(
                    win, status_var, btn, internal_ref, amount, app_self,
                    attempts + 1, max_attempts
                ))
        except Exception as e:
            status_var.set(f"⚠️ Status check error: {e}")
            win.after(5000, lambda: _poll_mpesa_status(
                win, status_var, btn, internal_ref, amount, app_self,
                attempts + 1, max_attempts
            ))

    threading.Thread(target=check, daemon=True).start()


# ─── Transaction Reversal Panel ──────────────────────────────────────────────

def show_reversal_panel(self):
    """
    Shows the user's reversal-eligible transactions in a dropdown.
    Allows submitting a reversal request with a reason.
    """
    win = tk.Toplevel(self.root)
    win.title("Request Transaction Reversal")
    win.geometry("520x460")
    win.resizable(False, False)
    win.grab_set()

    # ── Header ────────────────────────────────────────────────────────────
    header = tk.Frame(win, bg="#E53935", pady=14)
    header.pack(fill="x")
    tk.Label(header, text="🔄 Request Reversal",
             font=("Helvetica", 16, "bold"),
             bg="#E53935", fg="white").pack()
    tk.Label(header, text="Only transfers sent in the last 24 hours are eligible",
             font=("Helvetica", 10), bg="#E53935", fg="#ffcdd2").pack()

    body = tk.Frame(win, padx=24, pady=18, bg="white")
    body.pack(fill="both", expand=True)

    tk.Label(body, text="Loading eligible transactions…",
             font=("Helvetica", 10), fg="gray", bg="white").pack()
    win.update()

    # Clear and rebuild body
    for w in body.winfo_children():
        w.destroy()

    # Fetch eligible transactions
    eligible = []
    try:
        import api_client as api
        result = api._get("api/v1/reversal/eligible")
        eligible = result.get("eligible_transactions", [])
    except Exception as e:
        tk.Label(body, text=f"Error loading transactions: {e}",
                 fg="red", bg="white").pack()
        return

    if not eligible:
        tk.Label(body,
                 text="No eligible transactions found.\n\nOnly confirmed SEND transfers\nwithin the last 24 hours can be reversed.",
                 font=("Helvetica", 11), bg="white", justify="center",
                 wraplength=360).pack(pady=30)
        return

    # Build dropdown options
    tx_map = {}
    options = []
    for tx in eligible:
        ts = time.strftime("%H:%M %d/%m", time.localtime(tx["timestamp"]))
        label = f"{ts} — {tx['currency']} {tx['amount']:,.2f} → {tx['recipient']}"
        options.append(label)
        tx_map[label] = tx["tx_id"]

    tk.Label(body, text="Select transaction to reverse:",
             font=("Helvetica", 10, "bold"), bg="white", anchor="w").pack(fill="x")

    selected_var = tk.StringVar(value=options[0])
    combo = ttk.Combobox(body, textvariable=selected_var, values=options,
                          state="readonly", font=("Helvetica", 10))
    combo.pack(fill="x", pady=(4, 16))

    tk.Label(body, text="Reason (optional):",
             font=("Helvetica", 10, "bold"), bg="white", anchor="w").pack(fill="x")
    reason_text = tk.Text(body, height=4, font=("Helvetica", 10),
                           relief="solid", bd=1)
    reason_text.pack(fill="x", pady=(4, 16))

    status_var = tk.StringVar()
    status_lbl = tk.Label(body, textvariable=status_var, bg="white",
                           font=("Helvetica", 10), wraplength=380,
                           fg="#1565C0", justify="left")
    status_lbl.pack(fill="x")

    submit_btn = ttk.Button(body, text="Submit Reversal Request")
    submit_btn.pack(fill="x", pady=(8, 0))

    def submit():
        tx_id  = tx_map[selected_var.get()]
        reason = reason_text.get("1.0", "end").strip()
        submit_btn.config(state="disabled")
        status_var.set("⏳ Submitting…")

        def worker():
            try:
                import api_client as api
                result = api._post("api/v1/reversal/request", {
                    "tx_id": tx_id, "reason": reason
                })
                status_var.set(
                    f"✅ {result.get('message', 'Request submitted.')}\n"
                    f"Reference: {result.get('reversal_id', '')[:8]}…"
                )
            except RuntimeError as e:
                status_var.set(f"❌ {e}")
                submit_btn.config(state="normal")

        threading.Thread(target=worker, daemon=True).start()

    submit_btn.config(command=submit)


# ─── Admin Reversal Panel ─────────────────────────────────────────────────────

def show_admin_reversal_panel(self):
    """
    Admin view: lists pending reversal requests with Approve / Reject buttons.
    Only callable from admin dashboard.
    """
    win = tk.Toplevel(self.root)
    win.title("Admin — Reversal Requests")
    win.geometry("800x500")
    win.grab_set()

    header = tk.Frame(win, bg="#7B1FA2", pady=12)
    header.pack(fill="x")
    tk.Label(header, text="🔄 Pending Reversal Requests",
             font=("Helvetica", 15, "bold"),
             bg="#7B1FA2", fg="white").pack()

    # Table
    cols = ("reversal_id", "tx_id", "requester", "amount", "currency",
            "reason", "created_at")
    tree = ttk.Treeview(win, columns=cols, show="headings", height=14)
    for col in cols:
        tree.heading(col, text=col.replace("_", " ").title())
        tree.column(col, width=110, anchor="center")
    tree.pack(fill="both", expand=True, padx=8, pady=8)

    def load():
        tree.delete(*tree.get_children())
        try:
            import api_client as api
            data = api._get("api/v1/admin/reversals")
            for r in data.get("reversals", []):
                ts = time.strftime("%d/%m %H:%M", time.localtime(r["created_at"]))
                amt = r.get("amount", 0) / 100.0
                tree.insert("", "end", values=(
                    r["reversal_id"][:8] + "…",
                    r["tx_id"][:8] + "…",
                    r.get("requester_phone", r["requester_id"][:8]),
                    f"{amt:,.2f}",
                    r.get("currency", "KES"),
                    (r.get("reason") or "")[:30],
                    ts,
                ))
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=win)

    # Store full reversal_ids mapped to tree items
    _rev_ids = {}

    def load_with_ids():
        tree.delete(*tree.get_children())
        _rev_ids.clear()
        try:
            import api_client as api
            data = api._get("api/v1/admin/reversals")
            for r in data.get("reversals", []):
                ts  = time.strftime("%d/%m %H:%M", time.localtime(r["created_at"]))
                amt = r.get("amount", 0) / 100.0
                iid = tree.insert("", "end", values=(
                    r["reversal_id"][:8] + "…",
                    r["tx_id"][:8] + "…",
                    r.get("requester_phone", r["requester_id"][:8]),
                    f"{amt:,.2f}",
                    r.get("currency", "KES"),
                    (r.get("reason") or "")[:30],
                    ts,
                ))
                _rev_ids[iid] = r["reversal_id"]
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=win)

    def action(approve: bool):
        sel = tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a request first.", parent=win)
            return
        rev_id = _rev_ids.get(sel[0])
        if not rev_id:
            return
        verb = "approve" if approve else "reject"
        note = ""
        if not messagebox.askyesno("Confirm", f"Are you sure you want to {verb} this reversal?",
                                   parent=win):
            return

        try:
            import api_client as api
            endpoint = f"api/v1/admin/reversals/{rev_id}/{'approve' if approve else 'reject'}"
            result = api._post(endpoint, {"note": note})
            messagebox.showinfo("Done", result.get("message", "Done"), parent=win)
            load_with_ids()
        except RuntimeError as e:
            messagebox.showerror("Error", str(e), parent=win)

    btn_frame = tk.Frame(win)
    btn_frame.pack(pady=4)
    ttk.Button(btn_frame, text="✅ Approve",
               command=lambda: action(True)).pack(side="left", padx=8)
    ttk.Button(btn_frame, text="❌ Reject",
               command=lambda: action(False)).pack(side="left", padx=8)
    ttk.Button(btn_frame, text="🔄 Refresh",
               command=load_with_ids).pack(side="left", padx=8)

    load_with_ids()


# ─── Help Panel ──────────────────────────────────────────────────────────────

def show_help_panel(self):
    """
    Full in-app Help Panel.
    Fetches content from /api/v1/help and renders it in a scrollable window.
    """
    win = tk.Toplevel(self.root)
    win.title("ChainPay Help Center")
    win.geometry("560x600")
    win.resizable(True, True)
    win.grab_set()

    # Header
    header = tk.Frame(win, bg="#1565C0", pady=14)
    header.pack(fill="x")
    tk.Label(header, text="❓ ChainPay Help Center",
             font=("Helvetica", 16, "bold"),
             bg="#1565C0", fg="white").pack()

    # Scrollable content
    canvas = tk.Canvas(win, highlightthickness=0)
    scrollbar = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
    scroll_frame = tk.Frame(canvas, bg="white")

    scroll_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )

    canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    def _mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    canvas.bind_all("<MouseWheel>", _mousewheel)

    # Fetch and render
    def render():
        try:
            import api_client as api
            data = api._get("api/v1/help", authenticated=False)
            sections = data.get("sections", [])
        except Exception:
            # Fallback to static content
            sections = _static_help_sections()

        for section in sections:
            section_frame = tk.Frame(scroll_frame, bg="white",
                                     bd=1, relief="groove")
            section_frame.pack(fill="x", padx=12, pady=6)

            title_row = tk.Frame(section_frame, bg="#E3F2FD", pady=6)
            title_row.pack(fill="x")
            tk.Label(title_row,
                     text=f"{section.get('icon', '•')}  {section['title']}",
                     font=("Helvetica", 12, "bold"),
                     bg="#E3F2FD", fg="#1565C0", anchor="w",
                     padx=10).pack(fill="x")

            content_frame = tk.Frame(section_frame, bg="white", padx=12, pady=8)
            content_frame.pack(fill="x")

            if "steps" in section:
                for i, step in enumerate(section["steps"], 1):
                    tk.Label(content_frame, text=f"{i}. {step}",
                             font=("Helvetica", 10), bg="white",
                             anchor="w", wraplength=480, justify="left",
                             pady=2).pack(fill="x")
            if "tips" in section:
                for tip in section["tips"]:
                    tk.Label(content_frame, text=f"• {tip}",
                             font=("Helvetica", 10), bg="white",
                             anchor="w", wraplength=480, justify="left",
                             pady=2).pack(fill="x")
            if "content" in section:
                tk.Label(content_frame, text=section["content"],
                         font=("Helvetica", 10), bg="white",
                         anchor="w", wraplength=480, justify="left",
                         pady=2).pack(fill="x")
            if "note" in section:
                tk.Label(content_frame,
                         text=f"ℹ️  {section['note']}",
                         font=("Helvetica", 9, "italic"), fg="#555",
                         bg="#FFFDE7", anchor="w", wraplength=480,
                         padx=8, pady=4).pack(fill="x", pady=(6, 0))

    threading.Thread(target=render, daemon=True).start()


def _static_help_sections():
    """Fallback static help content when server is unreachable."""
    return [
        {
            "title": "How to Deposit via M-Pesa",
            "icon": "📱",
            "steps": [
                "Tap 'Deposit via M-Pesa' on your dashboard.",
                "Confirm your phone number and enter the amount in KES.",
                "You'll receive an M-Pesa PIN prompt on your phone.",
                "Enter your PIN. Your wallet is credited after confirmation.",
            ]
        },
        {
            "title": "How to Send Money",
            "icon": "💸",
            "steps": [
                "Tap 'Send Money'.",
                "Enter recipient phone number.",
                "Verify recipient name on confirmation screen.",
                "Confirm to execute the transfer.",
            ]
        },
        {
            "title": "Reversals",
            "icon": "🔄",
            "content": "Go to Transaction History → select a transfer within 24h → Request Reversal. Admin approval required."
        },
        {
            "title": "Contact Support",
            "icon": "🆘",
            "content": "Email: support@chainpay.app | M-Pesa disputes: *234#"
        },
    ]