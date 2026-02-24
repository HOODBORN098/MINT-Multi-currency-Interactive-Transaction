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
    Now includes better transaction details and confirmation.
    """
    win = tk.Toplevel(self.root)
    win.title("Request Transaction Reversal")
    win.geometry("600x550")
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

    # Build dropdown options with more details
    tx_map = {}
    options = []
    for tx in eligible:
        ts = time.strftime("%H:%M %d/%m", time.localtime(tx["timestamp"]))
        # Get recipient name if available
        recipient_display = tx.get('recipient_name', tx['recipient'][:8])
        label = f"{ts} — {tx['currency']} {tx['amount']:,.2f} → {recipient_display}"
        options.append(label)
        tx_map[label] = tx

    # Transaction info frame
    info_frame = tk.Frame(body, bg="#f5f5f5", relief="groove", bd=1)
    info_frame.pack(fill="x", pady=(0, 16))

    tk.Label(info_frame, text="Select transaction to reverse:",
             font=("Helvetica", 10, "bold"), bg="#f5f5f5", anchor="w").pack(anchor="w", padx=10, pady=(10, 0))

    selected_var = tk.StringVar()
    combo = ttk.Combobox(info_frame, textvariable=selected_var, values=options,
                         state="readonly", font=("Helvetica", 10), width=50)
    combo.pack(padx=10, pady=5, fill="x")
    combo.bind("<<ComboboxSelected>>", lambda e: update_details())

    # Details display
    details_frame = tk.Frame(info_frame, bg="#f5f5f5", padx=10, pady=10)
    details_frame.pack(fill="x")

    details_text = tk.Text(details_frame, height=6, font=("Helvetica", 9),
                           relief="solid", bd=1, wrap="word")
    details_text.pack(fill="x")
    details_text.config(state="disabled")

    def update_details():
        selected = selected_var.get()
        if selected in tx_map:
            tx = tx_map[selected]
            details_text.config(state="normal")
            details_text.delete("1.0", tk.END)
            details_text.insert("1.0",
                f"Transaction ID: {tx['tx_id']}\n"
                f"Date/Time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(tx['timestamp']))}\n"
                f"Amount: {tx['currency']} {tx['amount']:,.2f}\n"
                f"Recipient: {tx.get('recipient_name', tx['recipient'])}\n"
                f"Recipient Phone: {tx.get('recipient_phone', 'N/A')}\n"
                f"Fee Paid: {tx.get('fee', 0):,.2f} {tx['currency']}"
            )
            details_text.config(state="disabled")

    if options:
        selected_var.set(options[0])
        update_details()

    # Reason input
    reason_frame = tk.Frame(body, bg="white")
    reason_frame.pack(fill="x", pady=(0, 16))

    tk.Label(reason_frame, text="Reason for reversal (required):",
             font=("Helvetica", 10, "bold"), bg="white", anchor="w").pack(fill="x")
    reason_text = tk.Text(reason_frame, height=3, font=("Helvetica", 10),
                          relief="solid", bd=1)
    reason_text.pack(fill="x", pady=(4, 8))

    # Warning label
    warning_label = tk.Label(reason_frame,
        text="⚠️ Reversal will return funds to your wallet. The recipient will be notified.",
        font=("Helvetica", 9), fg="#E53935", bg="white", wraplength=500)
    warning_label.pack(fill="x", pady=(0, 8))

    status_var = tk.StringVar()
    status_lbl = tk.Label(body, textvariable=status_var, bg="white",
                          font=("Helvetica", 10), wraplength=500,
                          fg="#1565C0", justify="left")
    status_lbl.pack(fill="x")

    submit_btn = ttk.Button(body, text="Submit Reversal Request")
    submit_btn.pack(fill="x", pady=(8, 0))

    def submit():
        selected = selected_var.get()
        if not selected or selected not in tx_map:
            status_var.set("❌ Please select a transaction")
            return

        reason = reason_text.get("1.0", "end").strip()
        if not reason:
            status_var.set("❌ Please provide a reason for reversal")
            return

        tx = tx_map[selected]
        tx_id = tx['tx_id']

        submit_btn.config(state="disabled")
        status_var.set("⏳ Submitting reversal request…")

        def worker():
            try:
                import api_client as api
                result = api._post("api/v1/reversal/request", {
                    "tx_id": tx_id,
                    "reason": reason
                })
                self.after(0, lambda: show_result(result))
            except RuntimeError as e:
                self.after(0, lambda: on_error(str(e)))

        def show_result(result):
            status_var.set(
                f"✅ {result.get('message', 'Request submitted.')}\n"
                f"Reference: {result.get('reversal_id', '')[:8]}…\n"
                f"An admin will review your request."
            )
            submit_btn.config(state="normal")
            # Refresh after 2 seconds
            self.after(2000, win.destroy)

        def on_error(err):
            status_var.set(f"❌ {err}")
            submit_btn.config(state="normal")

        threading.Thread(target=worker, daemon=True).start()

    submit_btn.config(command=submit)


# ─── Admin Reversal Panel ─────────────────────────────────────────────────────

def show_admin_reversal_panel(self):
    """
    Enhanced admin view: lists pending reversal requests with Approve / Reject buttons
    and full transaction details.
    """
    win = tk.Toplevel(self.root)
    win.title("Admin — Reversal Requests")
    win.geometry("1000x700")
    win.grab_set()

    header = tk.Frame(win, bg="#7B1FA2", pady=12)
    header.pack(fill="x")
    tk.Label(header, text="🔄 Pending Reversal Requests",
             font=("Helvetica", 15, "bold"),
             bg="#7B1FA2", fg="white").pack()

    # Filter frame
    filter_frame = tk.Frame(win, bg="white", padx=10, pady=10)
    filter_frame.pack(fill="x")

    tk.Label(filter_frame, text="Filter by status:",
             font=("Helvetica", 10), bg="white").pack(side="left", padx=5)

    status_var = tk.StringVar(value="PENDING")
    status_combo = ttk.Combobox(filter_frame, textvariable=status_var,
                                 values=["PENDING", "APPROVED", "REJECTED", "ALL"],
                                 state="readonly", width=15)
    status_combo.pack(side="left", padx=5)
    status_combo.bind("<<ComboboxSelected>>", lambda e: load_with_ids())

    ttk.Button(filter_frame, text="Refresh",
               command=load_with_ids).pack(side="right", padx=5)

    # Main content frame with two columns
    main_frame = tk.Frame(win, bg="white")
    main_frame.pack(fill="both", expand=True, padx=10, pady=10)

    # Left column - Requests list
    left_frame = tk.Frame(main_frame, bg="white", width=450)
    left_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))
    left_frame.pack_propagate(False)

    tk.Label(left_frame, text="Reversal Requests",
             font=("Helvetica", 11, "bold"), bg="white").pack(anchor="w", pady=(0, 5))

    # Treeview for requests
    cols = ("created", "requester", "amount", "reason")
    tree = ttk.Treeview(left_frame, columns=cols, show="headings", height=18)
    tree.heading("created", text="Date")
    tree.heading("requester", text="Requester")
    tree.heading("amount", text="Amount")
    tree.heading("reason", text="Reason")
    tree.column("created", width=120)
    tree.column("requester", width=120)
    tree.column("amount", width=80)
    tree.column("reason", width=130)

    vsb = ttk.Scrollbar(left_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.pack(side="left", fill="both", expand=True)
    vsb.pack(side="right", fill="y")
    tree.bind("<<TreeviewSelect>>", on_select)

    # Right column - Details and actions
    right_frame = tk.Frame(main_frame, bg="#f5f5f5", width=500)
    right_frame.pack(side="left", fill="both", expand=True, padx=(5, 0))
    right_frame.pack_propagate(False)

    tk.Label(right_frame, text="Request Details",
             font=("Helvetica", 11, "bold"), bg="#f5f5f5").pack(anchor="w", padx=10, pady=(10, 5))

    # Details text area
    details_text = tk.Text(right_frame, height=12, font=("Helvetica", 10),
                           relief="solid", bd=1, wrap="word", bg="white")
    details_text.pack(fill="x", padx=10, pady=5)
    details_text.config(state="disabled")

    # Action frame
    action_frame = tk.Frame(right_frame, bg="#f5f5f5", padx=10, pady=10)
    action_frame.pack(fill="x")

    tk.Label(action_frame, text="Admin Note:",
             font=("Helvetica", 10), bg="#f5f5f5").pack(anchor="w")
    note_entry = tk.Entry(action_frame, font=("Helvetica", 10), width=40)
    note_entry.pack(fill="x", pady=(2, 10))

    btn_frame = tk.Frame(action_frame, bg="#f5f5f5")
    btn_frame.pack(fill="x")

    approve_btn = ttk.Button(btn_frame, text="✅ Approve Reversal",
                             command=lambda: action(True), state="disabled")
    approve_btn.pack(side="left", padx=5)

    reject_btn = ttk.Button(btn_frame, text="❌ Reject Reversal",
                            command=lambda: action(False), state="disabled")
    reject_btn.pack(side="left", padx=5)

    # Store full data
    _rev_data = {}
    _current_rev_id = None

    def load_with_ids():
        tree.delete(*tree.get_children())
        _rev_data.clear()
        try:
            import api_client as api
            status = status_var.get()
            if status == "ALL":
                data = api._get("api/v1/admin/reversals/history")
            else:
                data = api._get(f"api/v1/admin/reversals?status={status}")

            for r in data.get("reversals", []):
                ts = time.strftime("%d/%m %H:%M", time.localtime(r["created_at"]))
                amt = r.get("amount", 0)
                iid = tree.insert("", "end", values=(
                    ts,
                    r.get("requester_name", r["requester_id"][:8]),
                    f"{amt:,.2f} {r.get('currency', 'KES')}",
                    (r.get("reason") or "")[:30],
                ))
                _rev_data[iid] = r
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=win)

    def on_select(event):
        nonlocal _current_rev_id
        sel = tree.selection()
        if not sel:
            return

        rev = _rev_data.get(sel[0])
        if not rev:
            return

        _current_rev_id = rev["reversal_id"]

        # Update details
        details_text.config(state="normal")
        details_text.delete("1.0", tk.END)
        details_text.insert("1.0",
            f"Reversal ID: {rev['reversal_id']}\n"
            f"Transaction ID: {rev['tx_id']}\n"
            f"Requested by: {rev.get('requester_name', 'N/A')} ({rev.get('requester_phone', 'N/A')})\n"
            f"Recipient: {rev.get('recipient_name', 'N/A')} ({rev.get('recipient_phone', 'N/A')})\n"
            f"Amount: {rev.get('currency', 'KES')} {rev.get('amount', 0):,.2f}\n"
            f"Original TX Date: {time.strftime('%Y-%m-%d %H:%M', time.localtime(rev.get('tx_timestamp', 0)))}\n"
            f"Reason: {rev.get('reason', 'N/A')}\n"
            f"Status: {rev.get('status', 'N/A')}"
        )
        details_text.config(state="disabled")

        approve_btn.config(state="normal" if rev['status'] == 'PENDING' else "disabled")
        reject_btn.config(state="normal" if rev['status'] == 'PENDING' else "disabled")

    def action(approve: bool):
        if not _current_rev_id:
            return

        note = note_entry.get().strip()
        verb = "approve" if approve else "reject"

        if not messagebox.askyesno("Confirm",
                                    f"Are you sure you want to {verb} this reversal?",
                                    parent=win):
            return

        try:
            import api_client as api
            if approve:
                result = api._post(f"api/v1/admin/reversals/{_current_rev_id}/approve",
                                   {"note": note})
            else:
                result = api._post(f"api/v1/admin/reversals/{_current_rev_id}/reject",
                                   {"note": note})

            messagebox.showinfo("Success", result.get("message", "Done"), parent=win)
            load_with_ids()
            details_text.config(state="normal")
            details_text.delete("1.0", tk.END)
            details_text.config(state="disabled")
            approve_btn.config(state="disabled")
            reject_btn.config(state="disabled")
            note_entry.delete(0, tk.END)
        except RuntimeError as e:
            messagebox.showerror("Error", str(e), parent=win)

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