"""
ChainPay Desktop Application — v2.0 Client-Server Edition
==========================================================
CHANGES FROM v1:
  1. All DB/wallet calls replaced with api_client.py (REST HTTP calls).
  2. Admin nav button HIDDEN for non-admin users (not just access-denied content).
  3. PIN Management screen added (Settings page with Change PIN dialog).
  4. Currency conversion confirmation receipt improved with quote details.
  5. Server connection status shown in header bar.
  6. Login attempts brute-force handled server-side; client shows server error.
  7. RegisterScreen validates weak PINs client-side before sending to server.
  8. Logout clears api_client token.
  9. Startup shows server URL + connection test result.
 10. FX Quote is fetched from server (api_client.get_fx_quote).
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import time
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import api_client
from config import CONFIG

# ── Supported currencies (display only — server is authoritative) ──────────
SUPPORTED_CURRENCIES = ["USD", "EUR", "KES", "NGN", "GBP"]
CURRENCY_SYMBOLS     = {"USD": "$", "EUR": "€", "KES": "KES ", "NGN": "₦", "GBP": "£"}
CURRENCY_NAMES       = {
    "USD": "US Dollar",       "EUR": "Euro",
    "KES": "Kenyan Shilling", "NGN": "Nigerian Naira",
    "GBP": "British Pound",
}

WEAK_PINS = {"0000", "1111", "2222", "3333", "4444", "5555",
             "6666", "7777", "8888", "9999", "1234", "4321",
             "0123", "9876", "1122", "2211", "1212"}


def format_amount(amount: float, currency: str) -> str:
    symbol = CURRENCY_SYMBOLS.get(currency, currency + " ")
    if currency in ("KES", "NGN"):
        return f"{symbol}{amount:,.0f}"
    return f"{symbol}{amount:,.2f}"


# ── Palette ───────────────────────────────────────────────────────────────────
BG_DARK    = "#0a0e1a"
BG_PANEL   = "#111827"
BG_CARD    = "#1a2235"
BG_INPUT   = "#1f2d42"
ACCENT     = "#00d4ff"
ACCENT2    = "#0099cc"
GREEN      = "#00e676"
RED        = "#ff4444"
YELLOW     = "#ffd740"
ORANGE     = "#ff9800"
TEXT_MAIN  = "#e8f0fe"
TEXT_DIM   = "#8899aa"
TEXT_MONO  = "#00d4ff"
BORDER     = "#2a3a50"

FONT_MONO    = ("Courier", 11, "bold")
FONT_MONO_LG = ("Courier", 16, "bold")
FONT_UI      = ("Helvetica", 10)
FONT_UI_SM   = ("Helvetica", 9)
FONT_UI_LG   = ("Helvetica", 13, "bold")
FONT_LABEL   = ("Helvetica", 9)


# ── Helpers ───────────────────────────────────────────────────────────────────

def styled_button(parent, text, command, style="primary", **kwargs):
    colors = {
        "primary": (ACCENT, BG_DARK, ACCENT2),
        "success": (GREEN, BG_DARK, "#00b84a"),
        "danger":  (RED, BG_DARK, "#cc0000"),
        "ghost":   (BG_CARD, TEXT_MAIN, BG_INPUT),
        "warning": (YELLOW, BG_DARK, "#ccaa00"),
        "orange":  (ORANGE, BG_DARK, "#cc7700"),
    }
    bg, fg, active_bg = colors.get(style, colors["primary"])
    font = kwargs.pop("font", FONT_UI)
    return tk.Button(
        parent, text=text, command=command,
        bg=bg, fg=fg, activebackground=active_bg, activeforeground=fg,
        font=font, relief=tk.FLAT, cursor="hand2",
        padx=12, pady=6, **kwargs
    )


def make_entry(parent, placeholder="", show="", **kwargs):
    entry = tk.Entry(
        parent, bg=BG_INPUT, fg=TEXT_MAIN,
        insertbackground=ACCENT, relief=tk.FLAT, font=FONT_UI,
        highlightthickness=1, highlightbackground=BORDER,
        highlightcolor=ACCENT, show=show, **kwargs
    )
    entry._placeholder = placeholder
    entry._show        = show
    if placeholder:
        entry.insert(0, placeholder)
        entry.config(fg=TEXT_DIM)

        def on_focus_in(e):
            if entry.get() == placeholder and not show:
                entry.delete(0, tk.END)
                entry.config(fg=TEXT_MAIN)
            elif show and entry.get() == placeholder:
                entry.delete(0, tk.END)
                entry.config(fg=TEXT_MAIN)

        def on_focus_out(e):
            if not entry.get():
                if show:
                    entry.config(show="")
                entry.insert(0, placeholder)
                entry.config(fg=TEXT_DIM)
                if show:
                    entry.config(show="")

        entry.bind("<FocusIn>",  on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)
    return entry


def get_clean(entry: tk.Entry) -> str:
    val         = entry.get().strip()
    placeholder = getattr(entry, "_placeholder", "")
    return "" if val == placeholder else val


def card(parent, **kwargs):
    return tk.Frame(parent, bg=BG_CARD, relief=tk.FLAT, **kwargs)


def run_in_thread(func, *args, callback=None):
    def runner():
        try:
            result = func(*args)
        except Exception as e:
            result = e
        if callback:
            try:
                callback(result)
            except Exception:
                pass
    threading.Thread(target=runner, daemon=True).start()


# ── Login Screen ──────────────────────────────────────────────────────────────

class LoginScreen(tk.Frame):
    def __init__(self, parent, on_login):
        super().__init__(parent, bg=BG_DARK)
        self.on_login = on_login
        self._build()

    def _build(self):
        outer = tk.Frame(self, bg=BG_DARK)
        outer.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(outer, text="⬡", font=("Helvetica", 48),
                 fg=ACCENT, bg=BG_DARK).pack(pady=(0, 4))
        tk.Label(outer, text="CHAIN PAY", font=("Courier", 28, "bold"),
                 fg=TEXT_MAIN, bg=BG_DARK).pack()
        tk.Label(outer, text="blockchain-powered mobile money",
                 font=FONT_UI_SM, fg=TEXT_DIM, bg=BG_DARK).pack(pady=(2, 4))

        # Show server URL
        server_url = CONFIG.get("api_base_url", "")
        tk.Label(outer, text=f"Server: {server_url}",
                 font=FONT_LABEL, fg=TEXT_DIM, bg=BG_DARK).pack(pady=(0, 18))

        frm = card(outer, padx=32, pady=32)
        frm.pack()

        tk.Label(frm, text="PHONE NUMBER", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w")
        self.phone = make_entry(frm, placeholder="+254700000000", width=30)
        self.phone.pack(fill="x", pady=(2, 12), ipady=6)

        tk.Label(frm, text="PIN", font=FONT_LABEL, fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w")
        self.pin = make_entry(frm, show="●", width=30)
        self.pin.pack(fill="x", pady=(2, 20), ipady=6)

        self.pin.bind("<Return>",   lambda e: self._login())
        self.phone.bind("<Return>", lambda e: self._login())

        self.login_btn = styled_button(frm, "  SIGN IN  ", self._login, "primary")
        self.login_btn.pack(fill="x", pady=(0, 8))

        tk.Frame(frm, bg=BORDER, height=1).pack(fill="x", pady=8)
        tk.Label(frm, text="New to ChainPay?", font=FONT_UI_SM,
                 fg=TEXT_DIM, bg=BG_CARD).pack()
        styled_button(frm, "CREATE ACCOUNT", self._show_register, "ghost").pack(fill="x", pady=4)

        self.status = tk.Label(outer, text="", font=FONT_UI_SM, fg=RED, bg=BG_DARK)
        self.status.pack(pady=8)
        tk.Label(outer, text="Demo: +254700000000 • PIN: 1234",
                 font=FONT_LABEL, fg=TEXT_DIM, bg=BG_DARK).pack()

    def _login(self):
        phone = get_clean(self.phone)
        pin   = get_clean(self.pin)

        if not phone:
            self.status.config(text="Please enter your phone number", fg=YELLOW)
            return
        if not pin:
            self.status.config(text="Please enter your PIN", fg=YELLOW)
            return

        self.login_btn.config(state="disabled", text="Signing in...")
        self.status.config(text="", fg=RED)
        self.update_idletasks()

        def do_login():
            return api_client.login(phone, pin)

        def on_result(result):
            try:
                self.login_btn.config(state="normal", text="  SIGN IN  ")
                if isinstance(result, Exception):
                    self.status.config(text=str(result), fg=RED)
                    return
                self.update_idletasks()
                self.on_login(result)
            except Exception:
                pass

        run_in_thread(do_login, callback=on_result)

    def _show_register(self):
        for w in self.winfo_children():
            w.destroy()
        RegisterScreen(self, self._back_to_login).pack(fill="both", expand=True)

    def _back_to_login(self):
        for w in self.winfo_children():
            w.destroy()
        self._build()


class RegisterScreen(tk.Frame):
    def __init__(self, parent, on_back):
        super().__init__(parent, bg=BG_DARK)
        self.on_back = on_back
        self._build()

    def _build(self):
        outer = tk.Frame(self, bg=BG_DARK)
        outer.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(outer, text="⬡ CHAIN PAY", font=("Courier", 20, "bold"),
                 fg=ACCENT, bg=BG_DARK).pack(pady=(0, 4))
        tk.Label(outer, text="Create Account", font=FONT_UI_LG,
                 fg=TEXT_MAIN, bg=BG_DARK).pack(pady=(0, 20))

        frm = card(outer, padx=32, pady=32)
        frm.pack()

        fields = [
            ("FULL NAME",        "name",    "",      ""),
            ("PHONE NUMBER",     "phone",   "+254",  ""),
            ("PIN (4+ digits)",  "pin",     "",      "●"),
            ("CONFIRM PIN",      "confirm", "",      "●"),
        ]
        self.entries = {}
        for label, key, placeholder, show in fields:
            tk.Label(frm, text=label, font=FONT_LABEL, fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w")
            e = make_entry(frm, placeholder=placeholder, show=show, width=30)
            e.pack(fill="x", pady=(2, 10), ipady=6)
            self.entries[key] = e

        styled_button(frm, "CREATE ACCOUNT", self._register, "success").pack(fill="x", pady=8)
        styled_button(frm, "← Back to Login", self.on_back, "ghost").pack(fill="x")

        self.status = tk.Label(outer, text="", font=FONT_UI_SM, fg=RED, bg=BG_DARK)
        self.status.pack(pady=8)

    def _register(self):
        name    = get_clean(self.entries["name"])
        phone   = get_clean(self.entries["phone"])
        pin     = get_clean(self.entries["pin"])
        confirm = get_clean(self.entries["confirm"])

        if not all([name, phone, pin]):
            self.status.config(text="All fields are required")
            return
        if len(pin) < 4:
            self.status.config(text="PIN must be at least 4 digits")
            return
        if not pin.isdigit():
            self.status.config(text="PIN must be numbers only")
            return
        if pin in WEAK_PINS:
            self.status.config(text="PIN too weak — avoid sequential or repeated digits")
            return
        if pin != confirm:
            self.status.config(text="PINs do not match")
            return

        try:
            api_client.register(phone, name, pin)
            messagebox.showinfo(
                "Account Created",
                f"Welcome, {name}!\n\nYour ChainPay account is ready.\n"
                f"Sign in with {phone} and your PIN."
            )
            self.on_back()
        except Exception as e:
            self.status.config(text=str(e))


# ── Main App ──────────────────────────────────────────────────────────────────

class ChainPayApp(tk.Frame):

    def __init__(self, parent, user: dict):
        super().__init__(parent, bg=BG_DARK)
        self.user      = user
        self._is_admin = user.get("role") in ("admin", "compliance")
        self._page_frames = {}
        self._build()

    def _build(self):
        # ── Sidebar ────────────────────────────────────────────────────────
        sidebar = tk.Frame(self, bg=BG_PANEL, width=180)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        tk.Label(sidebar, text="⬡", font=("Helvetica", 24),
                 fg=ACCENT, bg=BG_PANEL).pack(pady=(16, 0))
        tk.Label(sidebar, text="CHAIN PAY", font=("Courier", 11, "bold"),
                 fg=TEXT_MAIN, bg=BG_PANEL).pack()
        tk.Frame(sidebar, bg=BORDER, height=1).pack(fill="x", padx=16, pady=8)

        # User info
        uf = tk.Frame(sidebar, bg=BG_PANEL, pady=6, padx=16)
        uf.pack(fill="x")
        name_short  = self.user["name"][:18] + ("…" if len(self.user["name"]) > 18 else "")
        role_color  = YELLOW if self._is_admin else GREEN
        tk.Label(uf, text=name_short, font=("Helvetica", 9, "bold"),
                 fg=TEXT_MAIN, bg=BG_PANEL).pack(anchor="w")
        tk.Label(uf, text=self.user.get("phone", ""), font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_PANEL).pack(anchor="w")
        tk.Label(uf, text=f"● {self.user.get('role','user').upper()}",
                 font=FONT_LABEL, fg=role_color, bg=BG_PANEL).pack(anchor="w")
        tk.Frame(sidebar, bg=BORDER, height=1).pack(fill="x", padx=16, pady=8)

        # ── Nav buttons — Admin ONLY visible if role == admin/compliance ──
        icons = {
            "Dashboard":  "◈", "Send Money": "↗",
            "FX Exchange": "⇄", "History":   "≡",
            "Blockchain": "⛓", "Settings":   "⚙",
            "Admin":      "⚙",
        }

        # Pages visible to ALL users
        user_pages = ["Dashboard", "Send Money", "FX Exchange", "History",
                      "Blockchain", "Settings"]

        # Admin page ONLY added to nav if the user is admin/compliance
        # Normal users cannot see or navigate to Admin at all
        all_pages = user_pages + (["Admin"] if self._is_admin else [])

        self._nav_buttons = {}
        for page in all_pages:
            btn = tk.Button(
                sidebar,
                text=f"  {icons.get(page, '')}  {page}",
                command=lambda p=page: self._show_page(p),
                bg=BG_PANEL, fg=TEXT_DIM,
                activebackground=BG_CARD, activeforeground=ACCENT,
                font=FONT_UI, relief=tk.FLAT, anchor="w",
                cursor="hand2", padx=16, pady=10
            )
            btn.pack(fill="x")
            self._nav_buttons[page] = btn

        # Logout button at bottom of sidebar
        tk.Frame(sidebar, bg=BORDER, height=1).pack(fill="x", padx=16, pady=8, side="bottom")
        tk.Button(
            sidebar, text="  ⏻  Logout",
            command=self._logout,
            bg=BG_PANEL, fg=RED,
            activebackground=BG_CARD, activeforeground=RED,
            font=FONT_UI, relief=tk.FLAT, anchor="w",
            cursor="hand2", padx=16, pady=10
        ).pack(fill="x", side="bottom")

        # ── Content area ───────────────────────────────────────────────────
        self.content = tk.Frame(self, bg=BG_DARK)
        self.content.pack(side="left", fill="both", expand=True)

        header = tk.Frame(self.content, bg=BG_PANEL, height=48)
        header.pack(fill="x")
        header.pack_propagate(False)
        self.header_title = tk.Label(header, text="Dashboard",
                                      font=FONT_UI_LG, fg=TEXT_MAIN, bg=BG_PANEL)
        self.header_title.pack(side="left", padx=20, pady=12)
        self.chain_status = tk.Label(header, text="⛓ CHAIN OK",
                                      font=FONT_LABEL, fg=GREEN, bg=BG_PANEL)
        self.chain_status.pack(side="right", padx=12)
        self.conn_status = tk.Label(header, text="● Connected",
                                     font=FONT_LABEL, fg=GREEN, bg=BG_PANEL)
        self.conn_status.pack(side="right", padx=8)
        self.clock_label = tk.Label(header, text="", font=FONT_LABEL,
                                     fg=TEXT_DIM, bg=BG_PANEL)
        self.clock_label.pack(side="right", padx=20)
        self._update_clock()

        self.page_container = tk.Frame(self.content, bg=BG_DARK)
        self.page_container.pack(fill="both", expand=True, padx=16, pady=16)

        # ── Build pages ────────────────────────────────────────────────────
        self._page_frames["Dashboard"]   = DashboardPage(self.page_container,   self.user, self)
        self._page_frames["Send Money"]  = SendMoneyPage(self.page_container,   self.user, self)
        self._page_frames["FX Exchange"] = FXPage(self.page_container,          self.user, self)
        self._page_frames["History"]     = HistoryPage(self.page_container,     self.user, self)
        self._page_frames["Blockchain"]  = BlockchainPage(self.page_container,  self.user, self)
        self._page_frames["Settings"]    = SettingsPage(self.page_container,    self.user, self)

        # Admin page built ONLY if the user has the role
        if self._is_admin:
            self._page_frames["Admin"] = AdminPage(self.page_container, self.user, self)

        self._show_page("Dashboard")
        self._schedule_refresh()

    def _show_page(self, page_name: str):
        for frame in self._page_frames.values():
            frame.pack_forget()
        frame = self._page_frames.get(page_name)
        if frame is None:
            return
        frame.pack(fill="both", expand=True)
        if hasattr(frame, "on_show"):
            frame.on_show()
        self.header_title.config(text=page_name)
        for name, btn in self._nav_buttons.items():
            btn.config(bg=BG_CARD if name == page_name else BG_PANEL,
                       fg=ACCENT if name == page_name else TEXT_DIM)

    def _update_clock(self):
        self.clock_label.config(text=time.strftime("%H:%M:%S  %Y-%m-%d"))
        self.after(1000, self._update_clock)

    def _schedule_refresh(self):
        """Periodic blockchain + connection health check."""
        def do_check():
            try:
                stats = api_client.get_blockchain_stats()
                return ("ok", stats.get("chain_valid", True))
            except Exception as e:
                return ("err", str(e))

        def update_ui(result):
            if isinstance(result, Exception) or result is None:
                self.conn_status.config(text="● Disconnected", fg=RED)
                return
            status, val = result
            if status == "ok":
                self.conn_status.config(text="● Connected", fg=GREEN)
                self.chain_status.config(
                    text=f"⛓ {'CHAIN OK' if val else 'CHAIN ERR!'}",
                    fg=GREEN if val else RED
                )
            else:
                self.conn_status.config(text="● Server error", fg=ORANGE)

        run_in_thread(do_check, callback=update_ui)
        self.after(15000, self._schedule_refresh)

    def _logout(self):
        if messagebox.askyesno("Logout", "Are you sure you want to logout?"):
            api_client.logout()
            # Re-show login screen
            root = self.winfo_toplevel()
            for w in root.winfo_children():
                try:
                    w.destroy()
                except Exception:
                    pass
            root.update_idletasks()
            login = LoginScreen(root, lambda u: _launch_app(root, u))
            login.pack(fill="both", expand=True)


def _launch_app(root, user):
    for w in root.winfo_children():
        try:
            w.destroy()
        except Exception:
            pass
    root.update_idletasks()
    app_frame = ChainPayApp(root, user)
    app_frame.pack(fill="both", expand=True)
    root.update_idletasks()


# ── Dashboard ─────────────────────────────────────────────────────────────────

class DashboardPage(tk.Frame):
    def __init__(self, parent, user, app):
        super().__init__(parent, bg=BG_DARK)
        self.user = user
        self.app  = app
        self._build()

    def _build(self):
        top = tk.Frame(self, bg=BG_DARK)
        top.pack(fill="x", pady=(0, 12))
        tk.Label(top, text="MY WALLETS", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_DARK).pack(anchor="w", pady=(0, 8))
        self.wallet_frame = tk.Frame(top, bg=BG_DARK)
        self.wallet_frame.pack(fill="x")

        bottom = tk.Frame(self, bg=BG_DARK)
        bottom.pack(fill="both", expand=True)

        act = card(bottom, padx=16, pady=16)
        act.pack(side="left", fill="y", padx=(0, 12))
        tk.Label(act, text="QUICK ACTIONS", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w", pady=(0, 12))
        for label, cmd, style in [
            ("↗  Send Money",   lambda: self.app._show_page("Send Money"),  "primary"),
            ("↙  Deposit",      self._quick_deposit,                         "success"),
            ("↑  Withdraw",     self._quick_withdraw,                        "ghost"),
            ("⇄  FX Convert",   lambda: self.app._show_page("FX Exchange"),  "ghost"),
            ("⚙  Settings",     lambda: self.app._show_page("Settings"),     "ghost"),
        ]:
            styled_button(act, label, cmd, style, width=20).pack(fill="x", pady=3)

        txf = card(bottom, padx=16, pady=16)
        txf.pack(side="left", fill="both", expand=True)
        tk.Label(txf, text="RECENT TRANSACTIONS", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w", pady=(0, 8))
        self.tx_list = tk.Frame(txf, bg=BG_CARD)
        self.tx_list.pack(fill="both", expand=True)
        self.refresh()

    def on_show(self):
        self.refresh()

    def refresh(self):
        self._refresh_wallets()
        self._refresh_transactions()

    def _refresh_wallets(self):
        for w in self.wallet_frame.winfo_children():
            w.destroy()
        try:
            wallets = api_client.get_balances()
            for wallet in wallets:
                balance = wallet["balance"] / 100
                ccy     = wallet["currency"]
                c = card(self.wallet_frame, padx=16, pady=12)
                c.pack(side="left", padx=(0, 8), fill="y")
                tk.Label(c, text=ccy,              font=FONT_LABEL,   fg=TEXT_DIM,  bg=BG_CARD).pack(anchor="w")
                tk.Label(c, text=format_amount(balance, ccy),
                         font=FONT_MONO_LG,   fg=TEXT_MONO, bg=BG_CARD).pack(anchor="w")
                tk.Label(c, text=CURRENCY_NAMES.get(ccy, ccy),
                         font=FONT_LABEL,   fg=TEXT_DIM,  bg=BG_CARD).pack(anchor="w")
        except Exception as e:
            tk.Label(self.wallet_frame, text=f"Error loading wallets: {e}",
                     fg=RED, bg=BG_DARK).pack(anchor="w")

    def _refresh_transactions(self):
        for w in self.tx_list.winfo_children():
            w.destroy()
        try:
            txs = api_client.get_transactions(limit=8)
            if not txs:
                tk.Label(self.tx_list, text="No transactions yet",
                         font=FONT_UI_SM, fg=TEXT_DIM, bg=BG_CARD).pack(pady=20)
                return
            type_map = {"SEND": "Transfer", "DEPOSIT": "Deposit",
                        "WITHDRAW": "Withdrawal", "FX_CONVERT": "FX Convert"}
            for tx in txs:
                is_credit = tx["recipient"] == self.user["user_id"]
                row  = tk.Frame(self.tx_list, bg=BG_CARD)
                row.pack(fill="x", pady=1)
                tk.Frame(row, bg=BORDER, height=1).pack(fill="x")
                inner = tk.Frame(row, bg=BG_CARD, pady=6, padx=8)
                inner.pack(fill="x")
                icon_col = GREEN if is_credit else RED
                tk.Label(inner, text="↙" if is_credit else "↗",
                         font=("Helvetica", 14), fg=icon_col, bg=BG_CARD).pack(side="left", padx=(0, 8))
                info = tk.Frame(inner, bg=BG_CARD)
                info.pack(side="left", fill="x", expand=True)
                tk.Label(info, text=type_map.get(tx["tx_type"], tx["tx_type"]),
                         font=("Helvetica", 9, "bold"), fg=TEXT_MAIN, bg=BG_CARD).pack(anchor="w")
                ts = time.strftime("%b %d %H:%M", time.localtime(tx["timestamp"]))
                tk.Label(info, text=ts, font=FONT_LABEL, fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w")
                amt = tx["amount"]
                amt_text = (f"+{format_amount(amt, tx['currency'])}"
                            if is_credit else f"-{format_amount(amt, tx['currency'])}")
                tk.Label(inner, text=amt_text, font=FONT_MONO,
                         fg=icon_col, bg=BG_CARD).pack(side="right")
        except Exception as e:
            tk.Label(self.tx_list, text=f"Error: {e}", fg=RED, bg=BG_CARD).pack(pady=8)

    def _quick_deposit(self):
        dlg = DepositDialog(self.winfo_toplevel(), self.user)
        self.winfo_toplevel().wait_window(dlg)
        self.refresh()

    def _quick_withdraw(self):
        dlg = WithdrawDialog(self.winfo_toplevel(), self.user)
        self.winfo_toplevel().wait_window(dlg)
        self.refresh()


# ── Send Money ────────────────────────────────────────────────────────────────

class SendMoneyPage(tk.Frame):
    def __init__(self, parent, user, app):
        super().__init__(parent, bg=BG_DARK)
        self.user = user
        self.app  = app
        self._build()

    def _build(self):
        main = card(self, padx=32, pady=32)
        main.pack(fill="both", expand=True)

        tk.Label(main, text="↗  SEND MONEY", font=FONT_UI_LG,
                 fg=TEXT_MAIN, bg=BG_CARD).pack(anchor="w", pady=(0, 20))

        form = tk.Frame(main, bg=BG_CARD)
        form.pack(fill="x")

        tk.Label(form, text="RECIPIENT PHONE NUMBER", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_CARD).grid(row=0, column=0, sticky="w", pady=(0, 2))
        self.recipient = make_entry(form, placeholder="+254700000001", width=36)
        self.recipient.grid(row=1, column=0, columnspan=2, sticky="ew", ipady=8, pady=(0, 12))

        tk.Label(form, text="AMOUNT", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_CARD).grid(row=2, column=0, sticky="w", pady=(0, 2))
        self.amount = make_entry(form, placeholder="0.00", width=24)
        self.amount.grid(row=3, column=0, sticky="ew", ipady=8, pady=(0, 12), padx=(0, 8))

        tk.Label(form, text="CURRENCY", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_CARD).grid(row=2, column=1, sticky="w", pady=(0, 2))
        self.currency = ttk.Combobox(form, values=SUPPORTED_CURRENCIES, width=10, state="readonly")
        self.currency.set("KES")
        self.currency.grid(row=3, column=1, sticky="ew", ipady=6, pady=(0, 12))

        tk.Label(form, text="NOTE (OPTIONAL)", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_CARD).grid(row=4, column=0, sticky="w", pady=(0, 2))
        self.note = make_entry(form, placeholder="Payment for...", width=36)
        self.note.grid(row=5, column=0, columnspan=2, sticky="ew", ipady=8, pady=(0, 20))
        form.columnconfigure(0, weight=3)
        form.columnconfigure(1, weight=1)

        self.fee_label = tk.Label(main, text="", font=FONT_LABEL, fg=TEXT_DIM, bg=BG_CARD)
        self.fee_label.pack(anchor="w", pady=(0, 4))
        self.amount.bind("<KeyRelease>", self._update_fee_preview)
        self.currency.bind("<<ComboboxSelected>>", self._update_fee_preview)

        styled_button(main, "  SEND NOW  →", self._send, "primary").pack(pady=8)
        self.status = tk.Label(main, text="", font=FONT_UI, fg=RED, bg=BG_CARD)
        self.status.pack(pady=4)
        self.receipt_frame = tk.Frame(main, bg=BG_CARD)
        self.receipt_frame.pack(fill="x", pady=(16, 0))

    def on_show(self):
        pass

    def _update_fee_preview(self, event=None):
        try:
            amt  = float(get_clean(self.amount))
            if amt <= 0:
                raise ValueError
            ccy = self.currency.get()
            # Get fee estimate from server
            def fetch_quote():
                return api_client.get_fx_quote("USD", "USD", amt)
            def update(result):
                if isinstance(result, Exception):
                    self.fee_label.config(text="")
                    return
                # Simplified estimate using tier table
                if amt < 10:
                    fee_pct = 0.005
                elif amt < 100:
                    fee_pct = 0.010
                elif amt < 1000:
                    fee_pct = 0.015
                else:
                    fee_pct = 0.020
                fee = max(amt * fee_pct, 0.01)
                self.fee_label.config(
                    text=f"Estimated fee: {format_amount(fee, ccy)}  |  Total: {format_amount(amt + fee, ccy)}",
                    fg=YELLOW
                )
            run_in_thread(fetch_quote, callback=update)
        except (ValueError, ZeroDivisionError):
            self.fee_label.config(text="")

    def _send(self):
        try:
            phone   = get_clean(self.recipient)
            ccy     = self.currency.get()
            note    = get_clean(self.note)
            amt_str = get_clean(self.amount)

            if not amt_str:
                self.status.config(text="Please enter an amount", fg=YELLOW)
                return
            try:
                amount = float(amt_str)
            except ValueError:
                self.status.config(text="Invalid amount — enter a number", fg=RED)
                return
            if amount <= 0:
                self.status.config(text="Amount must be positive", fg=RED)
                return
            if not phone:
                self.status.config(text="Please enter recipient phone", fg=YELLOW)
                return

            if not messagebox.askyesno("Confirm Transfer",
                                        f"Send {format_amount(amount, ccy)} to {phone}?"):
                return

            self.status.config(text="Processing…", fg=YELLOW)
            self.update_idletasks()

            result = api_client.send_money(phone, amount, ccy, note)
            receipt = result.get("transaction", {})
            self.status.config(text=f"✓ {result.get('message', 'Transfer successful')}", fg=GREEN)
            self._show_receipt(receipt)
            dashboard = self.app._page_frames.get("Dashboard")
            if dashboard and hasattr(dashboard, "refresh"):
                dashboard.refresh()
        except Exception as e:
            self.status.config(text=str(e), fg=RED)

    def _show_receipt(self, receipt: dict):
        for w in self.receipt_frame.winfo_children():
            w.destroy()
        if not receipt:
            return
        rc = card(self.receipt_frame, padx=16, pady=12)
        rc.pack(fill="x")
        tk.Label(rc, text="✓ TRANSFER RECEIPT", font=("Courier", 10, "bold"),
                 fg=GREEN, bg=BG_CARD).pack(anchor="w")
        tk.Frame(rc, bg=BORDER, height=1).pack(fill="x", pady=6)
        fields = [
            ("Transaction ID", str(receipt.get("tx_id", ""))[:16] + "…"),
            ("Recipient",      str(receipt.get("recipient_name", ""))),
            ("Phone",          str(receipt.get("recipient_phone", ""))),
            ("Amount",         format_amount(receipt.get("amount", 0),
                                             receipt.get("currency", "USD"))),
            ("Fee",            format_amount(receipt.get("fee", 0),
                                             receipt.get("currency", "USD"))),
            ("Status",         str(receipt.get("status", "CONFIRMED"))),
            ("Time",           time.strftime("%Y-%m-%d %H:%M:%S")),
        ]
        for label, value in fields:
            row = tk.Frame(rc, bg=BG_CARD)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=label + ":", font=FONT_LABEL, fg=TEXT_DIM,
                     bg=BG_CARD, width=16, anchor="w").pack(side="left")
            tk.Label(row, text=value, font=FONT_LABEL, fg=TEXT_MAIN,
                     bg=BG_CARD).pack(side="left")


# ── FX Exchange ───────────────────────────────────────────────────────────────

class FXPage(tk.Frame):
    def __init__(self, parent, user, app):
        super().__init__(parent, bg=BG_DARK)
        self.user = user
        self.app  = app
        self._current_quote = None
        self._build()

    def _build(self):
        left  = tk.Frame(self, bg=BG_DARK)
        left.pack(side="left", fill="both", expand=True, padx=(0, 12))
        right = tk.Frame(self, bg=BG_DARK)
        right.pack(side="left", fill="both", expand=True)

        conv = card(left, padx=24, pady=24)
        conv.pack(fill="x", pady=(0, 12))
        tk.Label(conv, text="⇄  CURRENCY CONVERTER", font=FONT_UI_LG,
                 fg=TEXT_MAIN, bg=BG_CARD).pack(anchor="w", pady=(0, 16))

        row1 = tk.Frame(conv, bg=BG_CARD)
        row1.pack(fill="x", pady=(0, 8))
        tk.Label(row1, text="FROM", font=FONT_LABEL, fg=TEXT_DIM, bg=BG_CARD).grid(row=0, column=0, sticky="w")
        tk.Label(row1, text="AMOUNT", font=FONT_LABEL, fg=TEXT_DIM, bg=BG_CARD).grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.from_ccy = ttk.Combobox(row1, values=SUPPORTED_CURRENCIES, width=8, state="readonly")
        self.from_ccy.set("USD")
        self.from_ccy.grid(row=1, column=0, sticky="ew", ipady=6, padx=(0, 8))
        self.from_amt = make_entry(row1, placeholder="0.00", width=18)
        self.from_amt.grid(row=1, column=1, sticky="ew", ipady=8)
        row1.columnconfigure(1, weight=1)

        tk.Label(conv, text="TO", font=FONT_LABEL, fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w", pady=(8, 2))
        self.to_ccy = ttk.Combobox(conv, values=SUPPORTED_CURRENCIES, width=8, state="readonly")
        self.to_ccy.set("KES")
        self.to_ccy.pack(anchor="w", ipady=6)

        self.quote_label = tk.Label(conv, text="", font=FONT_MONO, fg=ACCENT, bg=BG_CARD)
        self.quote_label.pack(anchor="w", pady=8)

        bf = tk.Frame(conv, bg=BG_CARD)
        bf.pack(anchor="w", pady=(0, 8))
        styled_button(bf, "GET QUOTE", self._get_quote, "ghost").pack(side="left", padx=(0, 8))
        self.convert_btn = styled_button(bf, "⇄  CONVERT NOW", self._convert, "primary")
        self.convert_btn.pack(side="left")
        self.convert_btn.config(state="disabled")

        self.conv_status = tk.Label(conv, text="", font=FONT_UI_SM, fg=RED, bg=BG_CARD)
        self.conv_status.pack(anchor="w", pady=4)

        self.from_amt.bind("<KeyRelease>",           lambda e: self._get_quote())
        self.from_ccy.bind("<<ComboboxSelected>>",   lambda e: self._get_quote())
        self.to_ccy.bind("<<ComboboxSelected>>",     lambda e: self._get_quote())

        rates_card = card(right, padx=16, pady=16)
        rates_card.pack(fill="both", expand=True)
        tk.Label(rates_card, text="LIVE RATES  (mid-market)", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w", pady=(0, 8))
        self.rate_text = scrolledtext.ScrolledText(
            rates_card, bg=BG_INPUT, fg=TEXT_MAIN,
            font=("Courier", 9), height=20, relief=tk.FLAT, wrap=tk.NONE
        )
        self.rate_text.pack(fill="both", expand=True)
        self._refresh_rates()

    def _get_quote(self, *args):
        try:
            amt = float(get_clean(self.from_amt))
            if amt <= 0:
                raise ValueError
        except ValueError:
            self.quote_label.config(text="")
            self.convert_btn.config(state="disabled")
            return
        from_ccy = self.from_ccy.get()
        to_ccy   = self.to_ccy.get()

        def fetch():
            return api_client.get_fx_quote(from_ccy, to_ccy, amt)

        def on_result(result):
            if isinstance(result, Exception):
                self.quote_label.config(text=f"Rate unavailable: {result}")
                self.convert_btn.config(state="disabled")
                return
            self._current_quote = result
            self.quote_label.config(
                text=(f"{format_amount(amt, from_ccy)} = {format_amount(result['to_amount'], to_ccy)}\n"
                      f"Rate: {result['effective_rate']:.6f}  |  Spread: {result['spread_pct']}%  |  "
                      f"Fee: {result['fx_fee']:.4f} {from_ccy}")
            )
            self.convert_btn.config(state="normal")

        run_in_thread(fetch, callback=on_result)

    def _convert(self):
        try:
            if not self._current_quote:
                return
            q = self._current_quote
            if not messagebox.askyesno("Confirm Conversion",
                                        f"Convert {format_amount(q['from_amount'], q['from_currency'])}"
                                        f" → {format_amount(q['to_amount'], q['to_currency'])}?\n\n"
                                        f"Rate: {q['effective_rate']:.6f}\n"
                                        f"FX Fee: {q['fx_fee']:.4f} {q['from_currency']}"):
                return
            self.conv_status.config(text="Converting…", fg=YELLOW)
            self.update_idletasks()

            result  = api_client.convert_currency(
                q["from_currency"], q["to_currency"], q["from_amount"]
            )
            conv    = result.get("conversion", {})
            self.conv_status.config(
                text=(f"✓ Converted {format_amount(conv.get('from_amount', 0), conv.get('from_currency', ''))} "
                      f"→ {format_amount(conv.get('to_amount', 0), conv.get('to_currency', ''))}"),
                fg=GREEN
            )
            dashboard = self.app._page_frames.get("Dashboard")
            if dashboard:
                dashboard.refresh()
            self._refresh_rates()
            self._current_quote = None
            self.convert_btn.config(state="disabled")
        except Exception as e:
            self.conv_status.config(text=str(e), fg=RED)

    def _refresh_rates(self):
        def fetch():
            return api_client.get_fx_rates()

        def on_result(rates):
            if isinstance(rates, Exception):
                return
            self.rate_text.config(state="normal")
            self.rate_text.delete("1.0", tk.END)
            self.rate_text.insert(tk.END, f"{'PAIR':<12} {'RATE':<16} UPDATED\n")
            self.rate_text.insert(tk.END, "─" * 42 + "\n")
            for r in rates:
                self.rate_text.insert(
                    tk.END, f"{r['pair']:<12} {r['rate']:<16.6f} {time.strftime('%H:%M:%S')}\n"
                )
            self.rate_text.config(state="disabled")

        run_in_thread(fetch, callback=on_result)

    def refresh(self):
        self._refresh_rates()

    def on_show(self):
        self._refresh_rates()


# ── History ───────────────────────────────────────────────────────────────────

class HistoryPage(tk.Frame):
    def __init__(self, parent, user, app):
        super().__init__(parent, bg=BG_DARK)
        self.user = user
        self.app  = app
        self._build()

    def _build(self):
        hr = tk.Frame(self, bg=BG_DARK)
        hr.pack(fill="x", pady=(0, 8))
        tk.Label(hr, text="TRANSACTION HISTORY", font=FONT_UI_LG,
                 fg=TEXT_MAIN, bg=BG_DARK).pack(side="left")
        styled_button(hr, "⟳ Refresh", self.refresh, "ghost").pack(side="right")

        table_frame = card(self, padx=0, pady=0)
        table_frame.pack(fill="both", expand=True)

        style = ttk.Style()
        style.configure("Treeview", background=BG_INPUT, fieldbackground=BG_INPUT,
                        foreground=TEXT_MAIN, rowheight=26, font=FONT_UI_SM)
        style.configure("Treeview.Heading", background=BG_CARD,
                        foreground=ACCENT, font=("Helvetica", 9, "bold"))
        style.map("Treeview", background=[("selected", ACCENT2)])

        columns = ("date", "type", "amount", "currency", "counterparty", "status", "fee")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=22)
        headers   = {
            "date": ("Date/Time", 140), "type": ("Type", 100),
            "amount": ("Amount", 100),  "currency": ("CCY", 55),
            "counterparty": ("Counterparty", 160), "status": ("Status", 90),
            "fee": ("Fee", 80),
        }
        for col, (label, width) in headers.items():
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="w")

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.refresh()

    def refresh(self):
        for row in self.tree.get_children():
            self.tree.delete(row)

        def fetch():
            return api_client.get_transactions(limit=100)

        def on_result(txs):
            if isinstance(txs, Exception):
                return
            type_map = {"SEND": "Transfer Out", "DEPOSIT": "Deposit",
                        "WITHDRAW": "Withdrawal", "FX_CONVERT": "FX Convert"}
            for tx in txs:
                is_credit    = tx["recipient"] == self.user["user_id"]
                date_str     = time.strftime("%m/%d %H:%M", time.localtime(tx["timestamp"]))
                amt          = tx["amount"]
                amt_str      = f"{'+'if is_credit else '-'}{amt:.4f}"
                counterparty = tx["recipient"] if not is_credit else tx["sender"]
                if counterparty in ("SYSTEM", self.user["user_id"]):
                    counterparty = "System" if counterparty == "SYSTEM" else "Self (FX)"
                tag = "credit" if is_credit else "debit"
                self.tree.insert("", "end", values=(
                    date_str, type_map.get(tx["tx_type"], tx["tx_type"]),
                    amt_str, tx["currency"], counterparty[:22],
                    tx["status"], f"{tx['fee']:.4f}"
                ), tags=(tag,))
            self.tree.tag_configure("credit", foreground=GREEN)
            self.tree.tag_configure("debit",  foreground=RED)

        run_in_thread(fetch, callback=on_result)

    def on_show(self):
        self.refresh()


# ── Blockchain Explorer ───────────────────────────────────────────────────────

class BlockchainPage(tk.Frame):
    def __init__(self, parent, user, app):
        super().__init__(parent, bg=BG_DARK)
        self.user = user
        self.app  = app
        self._build()

    def _build(self):
        hr = tk.Frame(self, bg=BG_DARK)
        hr.pack(fill="x", pady=(0, 8))
        tk.Label(hr, text="⛓  BLOCKCHAIN EXPLORER", font=FONT_UI_LG,
                 fg=TEXT_MAIN, bg=BG_DARK).pack(side="left")
        styled_button(hr, "⟳ Refresh",      self.refresh,   "ghost").pack(side="right")
        styled_button(hr, "Mine Block",      self._mine,     "warning").pack(side="right", padx=8)
        styled_button(hr, "Validate Chain",  self._validate, "success").pack(side="right", padx=(0, 8))

        self.stats_frame = tk.Frame(self, bg=BG_DARK)
        self.stats_frame.pack(fill="x", pady=(0, 8))

        split = tk.Frame(self, bg=BG_DARK)
        split.pack(fill="both", expand=True)

        left = card(split, padx=0, pady=0)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        tk.Label(left, text="BLOCKS", font=FONT_LABEL, fg=TEXT_DIM,
                 bg=BG_CARD, padx=8, pady=4).pack(anchor="w")
        self.block_text = scrolledtext.ScrolledText(
            left, bg=BG_INPUT, fg=ACCENT, font=("Courier", 9),
            height=20, relief=tk.FLAT, wrap=tk.NONE
        )
        self.block_text.pack(fill="both", expand=True, padx=4, pady=4)

        right = card(split, padx=0, pady=0)
        right.pack(side="left", fill="both", expand=True)
        tk.Label(right, text="TRANSACTIONS IN CHAIN", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_CARD, padx=8, pady=4).pack(anchor="w")
        self.tx_text = scrolledtext.ScrolledText(
            right, bg=BG_INPUT, fg=TEXT_MAIN, font=("Courier", 9),
            height=20, relief=tk.FLAT, wrap=tk.NONE
        )
        self.tx_text.pack(fill="both", expand=True, padx=4, pady=4)
        self.refresh()

    def refresh(self):
        def fetch():
            stats  = api_client.get_blockchain_stats()
            blocks = api_client.get_blockchain_blocks(20)
            # Admin blockchain txs only if admin
            try:
                txs = api_client.admin_get_blockchain_txs(30)
            except Exception:
                txs = []
            return stats, blocks, txs

        def on_result(result):
            if isinstance(result, Exception):
                return
            stats, blocks, txs = result
            self._show_stats(stats)

            self.block_text.config(state="normal")
            self.block_text.delete("1.0", tk.END)
            for block in blocks:
                ts = time.strftime("%H:%M:%S", time.localtime(block["timestamp"]))
                self.block_text.insert(tk.END,
                    f"Block #{block['index']}\n"
                    f"  Hash:      {block['block_hash'][:32]}...\n"
                    f"  Prev:      {block['previous_hash'][:32]}...\n"
                    f"  Merkle:    {block['merkle_root'][:32]}...\n"
                    f"  TXs:       {len(block['transactions'])}\n"
                    f"  Time:      {ts}\n"
                    f"  Validator: {block['validator']}\n"
                    f"{'─'*50}\n"
                )
            self.block_text.config(state="disabled")

            self.tx_text.config(state="normal")
            self.tx_text.delete("1.0", tk.END)
            for tx in txs:
                ts = time.strftime("%H:%M:%S", time.localtime(tx["timestamp"]))
                self.tx_text.insert(tk.END,
                    f"{tx['tx_id'][:16]}... | {tx['tx_type']:<12} | "
                    f"{tx['amount']:>10.4f} {tx['currency']} | {ts}\n"
                )
            self.tx_text.config(state="disabled")

        run_in_thread(fetch, callback=on_result)

    def _show_stats(self, stats):
        for w in self.stats_frame.winfo_children():
            w.destroy()
        items = [
            ("Blocks",       stats.get("total_blocks", 0)),
            ("Transactions", stats.get("total_transactions", 0)),
            ("Pending",      stats.get("pending_transactions", 0)),
            ("Chain Valid",  "✓ YES" if stats.get("chain_valid") else "✗ NO"),
        ]
        for label, value in items:
            c = card(self.stats_frame, padx=12, pady=8)
            c.pack(side="left", padx=(0, 8))
            color = GREEN if "✓" in str(value) else (RED if "✗" in str(value) else ACCENT)
            tk.Label(c, text=str(value), font=FONT_MONO_LG,
                     fg=color, bg=BG_CARD).pack()
            tk.Label(c, text=label, font=FONT_LABEL, fg=TEXT_DIM, bg=BG_CARD).pack()

    def _validate(self):
        try:
            result = api_client.validate_chain()
            if result.get("valid"):
                messagebox.showinfo("Chain Validation",
                                    f"✓ {result['message']}\n\nAll blocks intact and untampered.")
            else:
                messagebox.showerror("Chain Validation", f"✗ {result['message']}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _mine(self):
        try:
            result = api_client.mine_block()
            messagebox.showinfo("Block Mined", result.get("message", "Done"))
            self.refresh()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def on_show(self):
        self.refresh()


# ── Settings (PIN Management + Server Config) ─────────────────────────────────

class SettingsPage(tk.Frame):
    """
    New page for all user-facing settings.
    Includes: Change PIN (with old PIN confirmation, weak PIN check, rate limiting).
    """

    def __init__(self, parent, user, app):
        super().__init__(parent, bg=BG_DARK)
        self.user = user
        self.app  = app
        self._build()

    def _build(self):
        tk.Label(self, text="⚙  SETTINGS", font=FONT_UI_LG,
                 fg=TEXT_MAIN, bg=BG_DARK).pack(anchor="w", pady=(0, 20))

        # ── Account info card ─────────────────────────────────────────────
        info_card = card(self, padx=24, pady=24)
        info_card.pack(fill="x", pady=(0, 16))
        tk.Label(info_card, text="ACCOUNT INFORMATION", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w", pady=(0, 12))
        for label, value in [
            ("Name",     self.user.get("name", "")),
            ("Phone",    self.user.get("phone", "")),
            ("Role",     self.user.get("role", "user").upper()),
            ("KYC",      self.user.get("kyc_status", "VERIFIED")),
        ]:
            row = tk.Frame(info_card, bg=BG_CARD)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label + ":", font=FONT_LABEL, fg=TEXT_DIM,
                     bg=BG_CARD, width=10, anchor="w").pack(side="left")
            tk.Label(row, text=value, font=("Helvetica", 10, "bold"), fg=TEXT_MAIN,
                     bg=BG_CARD).pack(side="left")

        # ── Change PIN card ───────────────────────────────────────────────
        pin_card = card(self, padx=24, pady=24)
        pin_card.pack(fill="x", pady=(0, 16))
        tk.Label(pin_card, text="CHANGE PIN", font=FONT_UI_LG,
                 fg=ACCENT, bg=BG_CARD).pack(anchor="w", pady=(0, 4))
        tk.Label(pin_card,
                 text="For your security, confirm your current PIN before setting a new one.",
                 font=FONT_LABEL, fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w", pady=(0, 16))

        form = tk.Frame(pin_card, bg=BG_CARD)
        form.pack(fill="x")

        tk.Label(form, text="CURRENT PIN", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_CARD).grid(row=0, column=0, sticky="w", pady=(0, 2))
        self.old_pin = make_entry(form, show="●", width=20)
        self.old_pin.grid(row=1, column=0, sticky="ew", ipady=8, pady=(0, 12), padx=(0, 16))

        tk.Label(form, text="NEW PIN (4+ digits, no weak PINs)", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_CARD).grid(row=0, column=1, sticky="w", pady=(0, 2))
        self.new_pin = make_entry(form, show="●", width=20)
        self.new_pin.grid(row=1, column=1, sticky="ew", ipady=8, pady=(0, 12), padx=(0, 16))

        tk.Label(form, text="CONFIRM NEW PIN", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_CARD).grid(row=2, column=0, sticky="w", pady=(0, 2))
        self.confirm_pin = make_entry(form, show="●", width=20)
        self.confirm_pin.grid(row=3, column=0, sticky="ew", ipady=8, pady=(0, 12), padx=(0, 16))
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)

        # PIN strength indicator
        self.pin_strength_label = tk.Label(pin_card, text="", font=FONT_LABEL,
                                            fg=TEXT_DIM, bg=BG_CARD)
        self.pin_strength_label.pack(anchor="w", pady=(0, 4))
        self.new_pin.bind("<KeyRelease>", self._check_pin_strength)

        self.pin_status = tk.Label(pin_card, text="", font=FONT_UI_SM, fg=RED, bg=BG_CARD)
        self.pin_status.pack(anchor="w", pady=4)

        styled_button(pin_card, "  CHANGE PIN  ", self._change_pin, "primary").pack(anchor="w", pady=8)

        # ── Server config card ────────────────────────────────────────────
        srv_card = card(self, padx=24, pady=24)
        srv_card.pack(fill="x")
        tk.Label(srv_card, text="SERVER CONFIGURATION", font=FONT_UI_LG,
                 fg=ACCENT, bg=BG_CARD).pack(anchor="w", pady=(0, 12))

        srow = tk.Frame(srv_card, bg=BG_CARD)
        srow.pack(fill="x")
        tk.Label(srow, text="API Server URL:", font=FONT_LABEL, fg=TEXT_DIM,
                 bg=BG_CARD, width=16, anchor="w").pack(side="left")
        self.server_url_entry = make_entry(srow, width=40)
        self.server_url_entry.insert(0, CONFIG.get("api_base_url", ""))
        self.server_url_entry.pack(side="left", ipady=6, padx=8)
        styled_button(srow, "Save & Reconnect", self._save_server_url, "ghost").pack(side="left")

        self.srv_status = tk.Label(srv_card, text="", font=FONT_UI_SM,
                                    fg=TEXT_DIM, bg=BG_CARD)
        self.srv_status.pack(anchor="w", pady=4)

    def _check_pin_strength(self, event=None):
        pin = get_clean(self.new_pin)
        if not pin:
            self.pin_strength_label.config(text="")
            return
        if not pin.isdigit():
            self.pin_strength_label.config(text="⚠ PIN must be digits only", fg=RED)
        elif pin in WEAK_PINS:
            self.pin_strength_label.config(text="⚠ PIN too weak — avoid sequential/repeated digits", fg=RED)
        elif len(pin) < 4:
            self.pin_strength_label.config(text="⚠ PIN must be at least 4 digits", fg=YELLOW)
        elif len(pin) >= 6:
            self.pin_strength_label.config(text="✓ Strong PIN", fg=GREEN)
        else:
            self.pin_strength_label.config(text="● Acceptable PIN (6+ digits is stronger)", fg=YELLOW)

    def _change_pin(self):
        old     = get_clean(self.old_pin)
        new     = get_clean(self.new_pin)
        confirm = get_clean(self.confirm_pin)

        # Client-side validation
        if not old:
            self.pin_status.config(text="Enter your current PIN", fg=YELLOW)
            return
        if not new:
            self.pin_status.config(text="Enter a new PIN", fg=YELLOW)
            return
        if not new.isdigit():
            self.pin_status.config(text="PIN must be digits only", fg=RED)
            return
        if len(new) < 4:
            self.pin_status.config(text="New PIN must be at least 4 digits", fg=RED)
            return
        if new in WEAK_PINS:
            self.pin_status.config(text="New PIN is too weak", fg=RED)
            return
        if new != confirm:
            self.pin_status.config(text="New PINs do not match", fg=RED)
            return
        if old == new:
            self.pin_status.config(text="New PIN must be different from current PIN", fg=RED)
            return

        self.pin_status.config(text="Changing PIN…", fg=YELLOW)
        self.update_idletasks()

        def do_change():
            return api_client.change_pin(old, new)

        def on_result(result):
            if isinstance(result, Exception):
                self.pin_status.config(text=str(result), fg=RED)
                return
            self.pin_status.config(text="✓ PIN changed successfully", fg=GREEN)
            # Clear all PIN fields
            for e in [self.old_pin, self.new_pin, self.confirm_pin]:
                e.delete(0, tk.END)
            self.pin_strength_label.config(text="")

        run_in_thread(do_change, callback=on_result)

    def _save_server_url(self):
        new_url = self.server_url_entry.get().strip()
        if not new_url:
            self.srv_status.config(text="URL cannot be empty", fg=RED)
            return

        CONFIG["api_base_url"] = new_url
        from config import save_config
        save_config(CONFIG)

        self.srv_status.config(text="Saved. Testing connection…", fg=YELLOW)
        self.update_idletasks()

        def test():
            return api_client.get_fx_rates()

        def on_result(result):
            if isinstance(result, Exception):
                self.srv_status.config(text=f"⚠ Cannot connect: {result}", fg=RED)
            else:
                self.srv_status.config(text="✓ Connected to new server URL", fg=GREEN)

        run_in_thread(test, callback=on_result)

    def on_show(self):
        pass


# ── Admin Dashboard ───────────────────────────────────────────────────────────

class AdminPage(tk.Frame):
    """
    Only built and reachable if user has admin or compliance role.
    The nav button for this page is NEVER added for non-admin users.
    Server additionally enforces 403 on all /api/v1/admin/* routes.
    """

    def __init__(self, parent, user, app):
        super().__init__(parent, bg=BG_DARK)
        self.user = user
        self.app  = app
        self._build()

    def _build(self):
        header = tk.Frame(self, bg=BG_DARK)
        header.pack(fill="x", pady=(0, 8))
        tk.Label(header, text="⚙  ADMIN & COMPLIANCE DASHBOARD",
                 font=FONT_UI_LG, fg=YELLOW, bg=BG_DARK).pack(side="left")
        styled_button(header, "⟳ Refresh All", self.refresh, "ghost").pack(side="right")

        style = ttk.Style()
        style.configure("TNotebook",        background=BG_DARK, borderwidth=0)
        style.configure("TNotebook.Tab",    background=BG_PANEL, foreground=TEXT_DIM,
                        padding=[12, 6], font=FONT_UI)
        style.map("TNotebook.Tab",
                  background=[("selected", BG_CARD)],
                  foreground=[("selected", ACCENT)])

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        self._tab_overview    = tk.Frame(nb, bg=BG_DARK)
        self._tab_users       = tk.Frame(nb, bg=BG_DARK)
        self._tab_suspicious  = tk.Frame(nb, bg=BG_DARK)
        self._tab_logins      = tk.Frame(nb, bg=BG_DARK)
        self._tab_audit       = tk.Frame(nb, bg=BG_DARK)
        self._tab_config      = tk.Frame(nb, bg=BG_DARK)

        nb.add(self._tab_overview,   text="Overview")
        nb.add(self._tab_users,      text="Users")
        nb.add(self._tab_suspicious, text="Alerts")
        nb.add(self._tab_logins,     text="Login Log")
        nb.add(self._tab_audit,      text="Audit Log")
        nb.add(self._tab_config,     text="Config")

        self._build_overview_tab()
        self._build_users_tab()
        self._build_suspicious_tab()
        self._build_logins_tab()
        self._build_audit_tab()
        self._build_config_tab()

        self.refresh()

    def _build_overview_tab(self):
        top = tk.Frame(self._tab_overview, bg=BG_DARK)
        top.pack(fill="x", padx=8, pady=8)
        self.metric_frame = tk.Frame(top, bg=BG_DARK)
        self.metric_frame.pack(fill="x", pady=(0, 8))

        bottom = tk.Frame(self._tab_overview, bg=BG_DARK)
        bottom.pack(fill="both", expand=True, padx=8)

        lf = card(bottom, padx=12, pady=12)
        lf.pack(side="left", fill="both", expand=True, padx=(0, 6))
        tk.Label(lf, text="VOLUME BY CURRENCY", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w", pady=(0, 6))
        self.volume_text = tk.Text(lf, bg=BG_INPUT, fg=ACCENT,
                                    font=("Courier", 10), height=10, relief=tk.FLAT)
        self.volume_text.pack(fill="both", expand=True)

        rf = card(bottom, padx=12, pady=12)
        rf.pack(side="left", fill="both", expand=True, padx=(6, 0))
        tk.Label(rf, text="SYSTEM BALANCES", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w", pady=(0, 6))
        self.balances_text = tk.Text(rf, bg=BG_INPUT, fg=GREEN,
                                      font=("Courier", 10), height=10, relief=tk.FLAT)
        self.balances_text.pack(fill="both", expand=True)

    def _refresh_overview(self):
        def fetch():
            stats    = api_client.admin_get_stats()
            tx_stats = api_client.admin_get_tx_stats()
            balances = api_client.admin_get_system_balances()
            bc_stats = api_client.get_blockchain_stats()
            return stats, tx_stats, balances, bc_stats

        def on_result(result):
            if isinstance(result, Exception):
                return
            stats, tx_stats, balances, bc_stats = result

            for w in self.metric_frame.winfo_children():
                w.destroy()

            metrics = [
                ("Total Users",    stats.get("total_users", 0),       ACCENT),
                ("Active Users",   stats.get("active_users", 0),      GREEN),
                ("Suspended",      stats.get("suspended_users", 0),   RED if stats.get("suspended_users") else TEXT_DIM),
                ("Transactions",   stats.get("total_transactions", 0),ACCENT),
                ("Revenue",        f"${stats.get('total_revenue',0):,.2f}", GREEN),
                ("Failed TXs",     stats.get("failed_transactions", 0), RED if stats.get("failed_transactions") else TEXT_DIM),
                ("Alerts",         stats.get("suspicious_flags", 0),  ORANGE if stats.get("suspicious_flags") else TEXT_DIM),
                ("DB Size",        f"{stats.get('db_size_kb',0)} KB", TEXT_DIM),
                ("Chain Blocks",   bc_stats.get("total_blocks", 0),   ACCENT),
                ("Chain Valid",    "✓" if bc_stats.get("chain_valid") else "✗",
                 GREEN if bc_stats.get("chain_valid") else RED),
            ]
            for label, value, color in metrics:
                c = card(self.metric_frame, padx=10, pady=6)
                c.pack(side="left", padx=(0, 6))
                tk.Label(c, text=str(value), font=("Courier", 14, "bold"),
                         fg=color, bg=BG_CARD).pack()
                tk.Label(c, text=label, font=FONT_LABEL, fg=TEXT_DIM, bg=BG_CARD).pack()

            self.volume_text.config(state="normal")
            self.volume_text.delete("1.0", tk.END)
            self.volume_text.insert(tk.END, f"{'CURRENCY':<10} {'COUNT':>8} {'VOLUME':>16}\n")
            self.volume_text.insert(tk.END, "─" * 36 + "\n")
            for row in tx_stats.get("by_currency", []):
                self.volume_text.insert(tk.END,
                    f"{row['currency']:<10} {row['cnt']:>8} {row.get('vol',0):>16,.2f}\n")
            self.volume_text.config(state="disabled")

            self.balances_text.config(state="normal")
            self.balances_text.delete("1.0", tk.END)
            self.balances_text.insert(tk.END, f"{'CURRENCY':<10} {'TOTAL BALANCE':>16}\n")
            self.balances_text.insert(tk.END, "─" * 28 + "\n")
            for ccy, bal in balances.items():
                self.balances_text.insert(tk.END, f"{ccy:<10} {bal:>16,.2f}\n")
            self.balances_text.config(state="disabled")

        run_in_thread(fetch, callback=on_result)

    def _build_users_tab(self):
        hr = tk.Frame(self._tab_users, bg=BG_DARK)
        hr.pack(fill="x", padx=8, pady=8)
        tk.Label(hr, text="ALL USERS", font=FONT_UI_LG, fg=TEXT_MAIN, bg=BG_DARK).pack(side="left")
        styled_button(hr, "⟳ Refresh", self._refresh_users, "ghost").pack(side="right")

        cols    = ("phone", "name", "role", "kyc", "status", "last_login")
        headers = {
            "phone": ("Phone", 140), "name": ("Name", 160),
            "role": ("Role", 90),    "kyc": ("KYC", 90),
            "status": ("Status", 90), "last_login": ("Last Login", 130),
        }
        self.users_tree = ttk.Treeview(self._tab_users, columns=cols,
                                        show="headings", height=16)
        for col, (label, width) in headers.items():
            self.users_tree.heading(col, text=label)
            self.users_tree.column(col, width=width, anchor="w")

        vsb = ttk.Scrollbar(self._tab_users, orient="vertical", command=self.users_tree.yview)
        self.users_tree.configure(yscrollcommand=vsb.set)
        self.users_tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        vsb.pack(side="right", fill="y", pady=(0, 8), padx=(0, 8))

        if self.user.get("role") == "admin":
            act = card(self._tab_users, padx=12, pady=12)
            act.pack(fill="x", padx=8, pady=(0, 8))
            tk.Label(act, text="SELECTED USER ACTIONS", font=FONT_LABEL,
                     fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w", pady=(0, 8))
            btns = tk.Frame(act, bg=BG_CARD)
            btns.pack(fill="x")
            styled_button(btns, "Suspend",    self._suspend_selected,   "danger").pack(side="left", padx=4)
            styled_button(btns, "Unsuspend",  self._unsuspend_selected, "success").pack(side="left", padx=4)
            styled_button(btns, "Make Admin", self._make_admin,         "warning").pack(side="left", padx=4)
            styled_button(btns, "Make User",  self._make_user,          "ghost").pack(side="left", padx=4)

    def _refresh_users(self):
        def fetch():
            return api_client.admin_get_users()

        def on_result(users):
            if isinstance(users, Exception):
                return
            for row in self.users_tree.get_children():
                self.users_tree.delete(row)
            for u in users:
                status = "SUSPENDED" if u.get("is_suspended") else ("ACTIVE" if u.get("is_active") else "INACTIVE")
                ll     = time.strftime("%m/%d %H:%M", time.localtime(u["last_login"])) if u.get("last_login") else "Never"
                tag    = "suspended" if u.get("is_suspended") else ("admin" if u.get("role_id") == "admin" else "normal")
                self.users_tree.insert("", "end", iid=u["user_id"], values=(
                    u["phone"], u["name"], u.get("role_id", "user").upper(),
                    u.get("kyc_status", ""), status, ll
                ), tags=(tag,))
            self.users_tree.tag_configure("suspended", foreground=RED)
            self.users_tree.tag_configure("admin",     foreground=YELLOW)
            self.users_tree.tag_configure("normal",    foreground=TEXT_MAIN)

        run_in_thread(fetch, callback=on_result)

    def _get_selected_uid(self):
        sel = self.users_tree.selection()
        return sel[0] if sel else None

    def _suspend_selected(self):
        uid = self._get_selected_uid()
        if not uid:
            messagebox.showwarning("No Selection", "Select a user first.")
            return
        if uid == self.user["user_id"]:
            messagebox.showerror("Error", "Cannot suspend yourself.")
            return
        try:
            api_client.admin_suspend_user(uid)
            self._refresh_users()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _unsuspend_selected(self):
        uid = self._get_selected_uid()
        if uid:
            try:
                api_client.admin_unsuspend_user(uid)
                self._refresh_users()
            except Exception as e:
                messagebox.showerror("Error", str(e))

    def _make_admin(self):
        uid = self._get_selected_uid()
        if uid and messagebox.askyesno("Confirm", "Grant admin role to this user?"):
            try:
                api_client.admin_set_role(uid, "admin")
                self._refresh_users()
            except Exception as e:
                messagebox.showerror("Error", str(e))

    def _make_user(self):
        uid = self._get_selected_uid()
        if uid:
            try:
                api_client.admin_set_role(uid, "user")
                self._refresh_users()
            except Exception as e:
                messagebox.showerror("Error", str(e))

    def _build_suspicious_tab(self):
        hr = tk.Frame(self._tab_suspicious, bg=BG_DARK)
        hr.pack(fill="x", padx=8, pady=8)
        tk.Label(hr, text="UNRESOLVED ALERTS", font=FONT_UI_LG,
                 fg=ORANGE, bg=BG_DARK).pack(side="left")
        styled_button(hr, "⟳ Refresh",      self._refresh_suspicious, "ghost").pack(side="right")
        styled_button(hr, "Mark Resolved",   self._resolve_flag,       "success").pack(side="right", padx=8)

        cols = ("created", "phone", "type", "severity", "details")
        self.suspicious_tree = ttk.Treeview(self._tab_suspicious, columns=cols,
                                             show="headings", height=18)
        headers = {
            "created": ("Time", 130), "phone": ("Phone", 130),
            "type": ("Flag Type", 130), "severity": ("Severity", 90),
            "details": ("Details", 280),
        }
        for col, (label, width) in headers.items():
            self.suspicious_tree.heading(col, text=label)
            self.suspicious_tree.column(col, width=width, anchor="w")
        vsb = ttk.Scrollbar(self._tab_suspicious, orient="vertical",
                             command=self.suspicious_tree.yview)
        self.suspicious_tree.configure(yscrollcommand=vsb.set)
        self.suspicious_tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        vsb.pack(side="right", fill="y", pady=(0, 8), padx=(0, 8))

    def _refresh_suspicious(self):
        def fetch():
            return api_client.admin_get_suspicious()

        def on_result(flags):
            if isinstance(flags, Exception):
                return
            for row in self.suspicious_tree.get_children():
                self.suspicious_tree.delete(row)
            for f in flags:
                ts  = time.strftime("%m/%d %H:%M", time.localtime(f["created_at"]))
                det = str(f.get("details", ""))[:60]
                sev = f.get("severity", "")
                tag = {"CRITICAL": "crit", "HIGH": "high"}.get(sev, "med")
                self.suspicious_tree.insert("", "end", iid=f["flag_id"], values=(
                    ts, f.get("phone", f["user_id"][:8]),
                    f["flag_type"], sev, det
                ), tags=(tag,))
            self.suspicious_tree.tag_configure("crit", foreground=RED)
            self.suspicious_tree.tag_configure("high", foreground=ORANGE)
            self.suspicious_tree.tag_configure("med",  foreground=YELLOW)

        run_in_thread(fetch, callback=on_result)

    def _resolve_flag(self):
        sel = self.suspicious_tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Select a flag to resolve.")
            return
        try:
            api_client.admin_resolve_flag(sel[0])
            self._refresh_suspicious()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _build_logins_tab(self):
        hr = tk.Frame(self._tab_logins, bg=BG_DARK)
        hr.pack(fill="x", padx=8, pady=8)
        tk.Label(hr, text="LOGIN ATTEMPTS (latest 100)", font=FONT_UI_LG,
                 fg=TEXT_MAIN, bg=BG_DARK).pack(side="left")
        styled_button(hr, "⟳ Refresh", self._refresh_logins, "ghost").pack(side="right")

        cols = ("time", "phone", "result")
        self.logins_tree = ttk.Treeview(self._tab_logins, columns=cols,
                                         show="headings", height=20)
        for col, (label, width) in [("time", ("Time", 140)),
                                     ("phone", ("Phone", 180)),
                                     ("result", ("Result", 100))]:
            self.logins_tree.heading(col, text=label)
            self.logins_tree.column(col, width=width, anchor="w")
        vsb = ttk.Scrollbar(self._tab_logins, orient="vertical",
                             command=self.logins_tree.yview)
        self.logins_tree.configure(yscrollcommand=vsb.set)
        self.logins_tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        vsb.pack(side="right", fill="y", pady=(0, 8), padx=(0, 8))

    def _refresh_logins(self):
        def fetch():
            return api_client.admin_get_login_attempts()

        def on_result(attempts):
            if isinstance(attempts, Exception):
                return
            for row in self.logins_tree.get_children():
                self.logins_tree.delete(row)
            for a in attempts:
                ts  = time.strftime("%m/%d %H:%M:%S", time.localtime(a["timestamp"]))
                res = "SUCCESS" if a["success"] else "FAILED"
                tag = "success" if a["success"] else "failed"
                self.logins_tree.insert("", "end", values=(ts, a["phone"], res), tags=(tag,))
            self.logins_tree.tag_configure("success", foreground=GREEN)
            self.logins_tree.tag_configure("failed",  foreground=RED)

        run_in_thread(fetch, callback=on_result)

    def _build_audit_tab(self):
        hr = tk.Frame(self._tab_audit, bg=BG_DARK)
        hr.pack(fill="x", padx=8, pady=8)
        tk.Label(hr, text="COMPLIANCE AUDIT LOG", font=FONT_UI_LG,
                 fg=TEXT_MAIN, bg=BG_DARK).pack(side="left")
        styled_button(hr, "⟳ Refresh", self._refresh_audit, "ghost").pack(side="right")

        self.audit_text = scrolledtext.ScrolledText(
            self._tab_audit, bg=BG_INPUT, fg=TEXT_MAIN,
            font=("Courier", 9), relief=tk.FLAT, wrap=tk.NONE
        )
        self.audit_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _refresh_audit(self):
        def fetch():
            return api_client.admin_get_audit_log()

        def on_result(logs):
            if isinstance(logs, Exception):
                return
            self.audit_text.config(state="normal")
            self.audit_text.delete("1.0", tk.END)
            self.audit_text.insert(tk.END,
                f"{'TIMESTAMP':<20} {'USER':<12} {'ACTION':<28} DETAILS\n"
                f"{'─'*80}\n")
            for log in logs:
                ts      = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(log["timestamp"]))
                user_id = (log.get("user_id") or "SYSTEM")[:10]
                details = str(log.get("details", ""))[:50]
                self.audit_text.insert(tk.END,
                    f"{ts:<20} {user_id:<12} {log['action']:<28} {details}\n")
            self.audit_text.config(state="disabled")

        run_in_thread(fetch, callback=on_result)

    def _build_config_tab(self):
        hr = tk.Frame(self._tab_config, bg=BG_DARK)
        hr.pack(fill="x", padx=8, pady=8)
        tk.Label(hr, text="SYSTEM CONFIGURATION", font=FONT_UI_LG,
                 fg=TEXT_MAIN, bg=BG_DARK).pack(side="left")
        styled_button(hr, "⟳ Refresh", self._refresh_config, "ghost").pack(side="right")

        self.config_text = scrolledtext.ScrolledText(
            self._tab_config, bg=BG_INPUT, fg=ACCENT,
            font=("Courier", 10), height=20, relief=tk.FLAT, wrap=tk.NONE
        )
        self.config_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _refresh_config(self):
        def fetch():
            return api_client.admin_get_config()

        def on_result(configs):
            if isinstance(configs, Exception):
                return
            self.config_text.config(state="normal")
            self.config_text.delete("1.0", tk.END)
            self.config_text.insert(tk.END, f"{'KEY':<30} VALUE\n{'─'*50}\n")
            for cfg in configs:
                self.config_text.insert(tk.END,
                    f"{cfg['config_key']:<30} {cfg['config_value']}\n")
            self.config_text.config(state="disabled")

        run_in_thread(fetch, callback=on_result)

    def refresh(self):
        self._refresh_overview()
        self._refresh_users()
        self._refresh_suspicious()
        self._refresh_logins()
        self._refresh_audit()
        self._refresh_config()

    def on_show(self):
        self.refresh()


# ── Dialogs ───────────────────────────────────────────────────────────────────

class DepositDialog(tk.Toplevel):
    def __init__(self, parent, user):
        super().__init__(parent)
        self.user = user
        self.title("Deposit Funds")
        self.configure(bg=BG_DARK)
        self.geometry("380x280")
        self.resizable(False, False)
        self.grab_set()
        self._build()

    def _build(self):
        f = card(self, padx=24, pady=24)
        f.pack(fill="both", expand=True, padx=16, pady=16)
        tk.Label(f, text="↙  DEPOSIT FUNDS", font=FONT_UI_LG,
                 fg=GREEN, bg=BG_CARD).pack(pady=(0, 16))
        tk.Label(f, text="AMOUNT", font=FONT_LABEL, fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w")
        self.amount = make_entry(f, placeholder="0.00", width=28)
        self.amount.pack(fill="x", ipady=8, pady=(2, 8))
        tk.Label(f, text="CURRENCY", font=FONT_LABEL, fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w")
        self.ccy = ttk.Combobox(f, values=SUPPORTED_CURRENCIES, state="readonly", width=16)
        self.ccy.set("KES")
        self.ccy.pack(anchor="w", ipady=6, pady=(2, 16))
        styled_button(f, "DEPOSIT", self._do_deposit, "success", width=24).pack(fill="x")
        self.status = tk.Label(f, text="", font=FONT_UI_SM, fg=RED, bg=BG_CARD)
        self.status.pack(pady=4)

    def _do_deposit(self):
        try:
            amt_str = get_clean(self.amount)
            if not amt_str:
                self.status.config(text="Enter an amount")
                return
            amt = float(amt_str)
            ccy = self.ccy.get()
            result = api_client.deposit(amt, ccy)
            messagebox.showinfo("Deposit", f"✓ {result.get('message','Deposit successful')}")
            self.destroy()
        except ValueError:
            self.status.config(text="Invalid amount")
        except Exception as e:
            self.status.config(text=str(e))


class WithdrawDialog(tk.Toplevel):
    def __init__(self, parent, user):
        super().__init__(parent)
        self.user = user
        self.title("Withdraw Funds")
        self.configure(bg=BG_DARK)
        self.geometry("380x280")
        self.resizable(False, False)
        self.grab_set()
        self._build()

    def _build(self):
        f = card(self, padx=24, pady=24)
        f.pack(fill="both", expand=True, padx=16, pady=16)
        tk.Label(f, text="↑  WITHDRAW FUNDS", font=FONT_UI_LG,
                 fg=YELLOW, bg=BG_CARD).pack(pady=(0, 16))
        tk.Label(f, text="AMOUNT", font=FONT_LABEL, fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w")
        self.amount = make_entry(f, placeholder="0.00", width=28)
        self.amount.pack(fill="x", ipady=8, pady=(2, 8))
        tk.Label(f, text="CURRENCY", font=FONT_LABEL, fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w")
        self.ccy = ttk.Combobox(f, values=SUPPORTED_CURRENCIES, state="readonly", width=16)
        self.ccy.set("KES")
        self.ccy.pack(anchor="w", ipady=6, pady=(2, 16))
        styled_button(f, "WITHDRAW", self._do_withdraw, "warning", width=24).pack(fill="x")
        self.status = tk.Label(f, text="", font=FONT_UI_SM, fg=RED, bg=BG_CARD)
        self.status.pack(pady=4)

    def _do_withdraw(self):
        try:
            amt_str = get_clean(self.amount)
            if not amt_str:
                self.status.config(text="Enter an amount")
                return
            amt = float(amt_str)
            ccy = self.ccy.get()
            result = api_client.withdraw(amt, ccy)
            messagebox.showinfo("Withdrawal", f"✓ {result.get('message','Withdrawal successful')}")
            self.destroy()
        except ValueError:
            self.status.config(text="Invalid amount")
        except Exception as e:
            self.status.config(text=str(e))


# ── Entry Point ───────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    root.title("ChainPay — Blockchain Mobile Money")
    root.geometry("1100x720")
    root.minsize(900, 600)
    root.configure(bg=BG_DARK)

    def on_login(user):
        _launch_app(root, user)

    # Test server connectivity on startup
    def startup_check():
        try:
            api_client.get_fx_rates()
        except Exception as e:
            root.after(500, lambda: messagebox.showwarning(
                "Server Offline",
                f"Cannot connect to ChainPay server at:\n{CONFIG.get('api_base_url')}\n\n"
                f"Error: {e}\n\n"
                f"Make sure the server is running.\n"
                f"You can update the server URL in Settings after login."
            ))

    threading.Thread(target=startup_check, daemon=True).start()

    login = LoginScreen(root, on_login)
    login.pack(fill="both", expand=True)

    root.mainloop()


if __name__ == "__main__":
    main()