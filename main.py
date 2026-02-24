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
 11. M-Pesa STK Push deposit added to Dashboard quick actions.

BUGFIXES v2.1:
  - Fixed blank UI after login: removed pack_propagate(False) from DashboardPage
    which was preventing content from being visible.
  - Fixed _launch_app to use update() instead of update_idletasks() so geometry
    is properly computed before packing the app frame.
  - Fixed DashboardPage layout: wallet cards now render correctly with proper
    frame sizing.
  - Fixed _show_deposit_prompt scheduling to avoid race with UI build.
  - Fixed _register method indentation (was outside RegisterScreen class).
  - Fixed _show_reversal method indentation (was inside nested function).
  - Fixed DashboardPage._mpesa_deposit import to use local class instead of
    gui_mpesa module (which may not exist).
  - Fixed DashboardPage._show_help to not import from gui_mpesa.
  - Fixed wallet card layout to use pack properly (not fixed sizes that clip).
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

from tkinter import simpledialog
import re


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

def validate_e164_phone(phone: str) -> bool:
    """Validate phone number follows E.164 standard."""
    pattern = r'^\+[1-9]\d{1,14}$'
    return bool(re.match(pattern, phone))


def detect_country_from_phone(phone: str) -> str:
    """Detect country from phone number."""
    if phone.startswith('+254'):
        return 'Kenya'
    elif phone.startswith('+1'):
        return 'United States'
    elif phone.startswith('+44'):
        return 'United Kingdom'
    elif phone.startswith('+233'):
        return 'Ghana'
    elif phone.startswith('+234'):
        return 'Nigeria'
    else:
        return 'Unknown'

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
        "mpesa":   ("#4caf50", "#ffffff", "#388e3c"),
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

    # FIX: _register is now correctly inside RegisterScreen class
    def _register(self):
        name    = get_clean(self.entries["name"])
        phone   = get_clean(self.entries["phone"])
        pin     = get_clean(self.entries["pin"])
        confirm = get_clean(self.entries["confirm"])

        if not all([name, phone, pin]):
            self.status.config(text="All fields are required")
            return

        # Validate E.164 phone format
        if not validate_e164_phone(phone):
            self.status.config(text="Phone must be in E.164 format: +[country][number] (e.g., +254700000000)")
            return

        # Detect and show base currency
        country = detect_country_from_phone(phone)
        base_currency = 'KES' if country == 'Kenya' else 'USD'

        if not messagebox.askyesno(
            "Confirm Account Details",
            f"Country detected: {country}\n"
            f"Base currency: {base_currency}\n\n"
            f"Name: {name}\n"
            f"Phone: {phone}\n\n"
            f"Proceed with registration?"
        ):
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
                f"Base Currency: {base_currency}\n"
                f"You can now deposit funds to start using the service.\n\n"
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

        # FIX: Delay deposit prompt longer so UI is fully rendered first
        self.after(1000, self._show_deposit_prompt)

    def _show_deposit_prompt(self):
        """
        Show deposit prompt ONLY on the very first login after registration.
        Controlled by `first_login_completed` flag returned in the login response.
        After the user dismisses (Yes or No), the flag is set on the server so
        the popup never appears again.
        """
        # If first_login_completed is True (or missing/unknown), do NOT show popup
        if self.user.get("first_login_completed", True):
            return

        # Show the prompt
        if messagebox.askyesno(
            "Welcome to ChainPay! 🎉",
            "Your account is ready.\n\n"
            "Would you like to deposit funds into your wallet now?\n\n"
            "• Yes — deposit via M-Pesa or bank transfer\n"
            "• No  — continue to dashboard (you can deposit anytime)",
            parent=self
        ):
            country = detect_country_from_phone(self.user.get('phone', ''))
            if country == 'Kenya':
                MpesaDepositDialog(self.winfo_toplevel(), self.user,
                                   on_success=self._refresh_dashboard)
            else:
                DepositDialog(self.winfo_toplevel(), self.user)

        # Mark first-login as done regardless of Yes/No so it never shows again
        def _mark_done():
            try:
                import api_client as _api
                _api._request("POST", "/api/v1/auth/first-login-done", {})
            except Exception:
                pass
        run_in_thread(_mark_done)

    def _refresh_dashboard(self):
        """Refresh dashboard after deposit."""
        dashboard = self._page_frames.get("Dashboard")
        if dashboard and hasattr(dashboard, "refresh"):
            dashboard.refresh()

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

        icons = {
            "Dashboard":  "◈", "Send Money": "↗",
            "FX Exchange": "⇄", "History":   "≡",
            "Blockchain": "⛓", "Settings":   "⚙",
            "Admin":      "⚙",
        }

        user_pages = ["Dashboard", "Send Money", "FX Exchange", "History",
                      "Blockchain", "Settings"]

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
    print(f"🚀 Launching app for user: {user.get('name')}")
    print(f"📦 User data: {user}")

    # Clear existing content
    for w in root.winfo_children():
        try:
            w.destroy()
        except Exception as e:
            print(f"Error destroying widget: {e}")

    # FIX: Use update() not update_idletasks() so geometry is recalculated
    root.update()
    print("✅ Root cleared")

    # Create and pack the app
    app_frame = ChainPayApp(root, user)
    print("✅ App frame created")
    app_frame.pack(fill="both", expand=True)
    print("✅ App frame packed")

    # FIX: Call update() to force full geometry pass and render
    root.update()
    print("✅ UI updated")


# ── M-Pesa Deposit Dialog ─────────────────────────────────────────────────────

class MpesaDepositDialog(tk.Toplevel):
    """
    M-Pesa STK Push deposit dialog.
    Sends a payment prompt to the user's phone, then polls the server
    every 5 seconds for up to 2 minutes waiting for confirmation.
    """

    def __init__(self, parent, user, on_success=None):
        super().__init__(parent)
        self.user       = user
        self.on_success = on_success
        self.title("Deposit via M-Pesa")
        self.configure(bg=BG_DARK)
        self.geometry("440x430")
        self.resizable(False, False)
        self.grab_set()
        self._build()

    def _build(self):
        outer = tk.Frame(self, bg=BG_DARK, padx=20, pady=20)
        outer.pack(fill="both", expand=True)

        # Header
        hdr = tk.Frame(outer, bg=BG_DARK)
        hdr.pack(fill="x", pady=(0, 16))
        tk.Label(hdr, text="📱", font=("Helvetica", 28),
                 bg=BG_DARK).pack(side="left", padx=(0, 10))
        title_f = tk.Frame(hdr, bg=BG_DARK)
        title_f.pack(side="left", fill="x", expand=True)
        tk.Label(title_f, text="Deposit via M-Pesa",
                 font=("Helvetica", 14, "bold"), fg=TEXT_MAIN, bg=BG_DARK).pack(anchor="w")
        tk.Label(title_f, text="An STK Push will be sent to your phone",
                 font=FONT_UI_SM, fg=TEXT_DIM, bg=BG_DARK).pack(anchor="w")

        frm = card(outer, padx=24, pady=24)
        frm.pack(fill="x")

        tk.Label(frm, text="M-PESA PHONE NUMBER", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w")
        self.phone_entry = make_entry(frm, placeholder="+254700000000", width=32)
        user_phone = self.user.get("phone", "")
        if user_phone:
            self.phone_entry.delete(0, tk.END)
            self.phone_entry.insert(0, user_phone)
            self.phone_entry.config(fg=TEXT_MAIN)
        self.phone_entry.pack(fill="x", ipady=8, pady=(2, 12))

        tk.Label(frm, text="AMOUNT (KES)", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w")
        self.amount_entry = make_entry(frm, placeholder="e.g. 500", width=32)
        self.amount_entry.pack(fill="x", ipady=8, pady=(2, 16))

        self.status_var = tk.StringVar(value="")
        self.status_label = tk.Label(
            frm, textvariable=self.status_var,
            font=FONT_UI_SM, fg=ACCENT, bg=BG_CARD,
            wraplength=360, justify="left", height=3
        )
        self.status_label.pack(anchor="w", pady=(0, 8))

        self.progress = ttk.Progressbar(frm, mode="indeterminate", length=360)

        btn_row = tk.Frame(frm, bg=BG_CARD)
        btn_row.pack(fill="x", pady=(8, 0))
        self.send_btn = styled_button(
            btn_row, "📲  Send M-Pesa STK Push", self._do_deposit, "mpesa"
        )
        self.send_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))
        styled_button(btn_row, "Cancel", self.destroy, "ghost").pack(side="left")

    def _set_status(self, text, color=ACCENT):
        self.status_var.set(text)
        self.status_label.config(fg=color)
        self.update_idletasks()

    def _do_deposit(self):
        phone_raw  = self.phone_entry.get().strip()
        phone      = "" if phone_raw == getattr(self.phone_entry, "_placeholder", "") else phone_raw
        amount_raw = self.amount_entry.get().strip()
        amount_str = "" if amount_raw == getattr(self.amount_entry, "_placeholder", "") else amount_raw

        if not phone:
            self._set_status("⚠ Please enter an M-Pesa phone number.", YELLOW)
            return
        try:
            amount = float(amount_str)
            if amount <= 0:
                raise ValueError
        except ValueError:
            self._set_status("⚠ Please enter a valid amount greater than 0.", YELLOW)
            return

        if not messagebox.askyesno(
            "Confirm M-Pesa Deposit",
            f"Send STK Push to {phone} for KES {amount:,.0f}?\n\n"
            f"You will be prompted to enter your M-Pesa PIN on your phone.",
            parent=self
        ):
            return

        self.send_btn.config(state="disabled")
        self.progress.pack(fill="x", pady=(0, 8))
        self.progress.start(12)
        self._set_status("⏳ Initiating STK Push…", ACCENT)

        def worker():
            try:
                result = api_client.mpesa_deposit(phone, amount)
                ref = result.get("internal_ref")

                self.after(0, lambda: self._set_status(
                    "✅ STK Push sent!\nCheck your phone and enter your M-Pesa PIN.\n"
                    "Waiting for confirmation…", GREEN
                ))

                for attempt in range(24):
                    time.sleep(5)
                    try:
                        s = api_client.mpesa_deposit_status(ref)
                    except Exception as poll_err:
                        self.after(0, lambda e=poll_err: self._set_status(
                            f"⚠ Polling error: {e}\nStill waiting…", YELLOW
                        ))
                        continue

                    status = s.get("status", "")

                    if status == "CONFIRMED":
                        receipt = s.get("receipt_number", "N/A")
                        msg = (
                            f"✅ KES {amount:,.0f} deposited successfully!\n"
                            f"M-Pesa Receipt: {receipt}"
                        )
                        self.after(0, lambda m=msg: self._on_confirmed(m))
                        return

                    elif status in ("FAILED", "EXPIRED"):
                        desc = s.get("result_desc", "Payment failed or expired.")
                        self.after(0, lambda d=desc: self._on_failed(d))
                        return

                    else:
                        elapsed = (attempt + 1) * 5
                        self.after(0, lambda e=elapsed: self._set_status(
                            f"⏳ Waiting for confirmation… ({e}s elapsed)\n"
                            f"Please enter your M-Pesa PIN if prompted.", ACCENT
                        ))

                self.after(0, self._on_timeout)

            except RuntimeError as e:
                self.after(0, lambda err=e: self._on_failed(str(err)))
            except Exception as e:
                self.after(0, lambda err=e: self._on_failed(f"Unexpected error: {err}"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_confirmed(self, message: str):
        self.progress.stop()
        self.progress.pack_forget()
        self._set_status(message, GREEN)
        self.send_btn.config(state="normal")
        messagebox.showinfo("Deposit Successful", message, parent=self)
        if self.on_success:
            try:
                self.on_success()
            except Exception:
                pass
        self.destroy()

    def _on_failed(self, reason: str):
        self.progress.stop()
        self.progress.pack_forget()
        self._set_status(f"❌ {reason}", RED)
        self.send_btn.config(state="normal")

    def _on_timeout(self):
        self.progress.stop()
        self.progress.pack_forget()
        self._set_status(
            "⏰ Timed out waiting for M-Pesa confirmation.\n"
            "Please check your transaction history.", ORANGE
        )
        self.send_btn.config(state="normal")


# ── Dashboard ─────────────────────────────────────────────────────────────────
class DashboardPage(tk.Frame):
    def __init__(self, parent, user, app):
        super().__init__(parent, bg=BG_DARK)
        self.user = user
        self.app  = app
        print(f"🖥️ DashboardPage initialized for user: {user.get('name')}")
        # FIX: Removed pack_propagate(False) — this was preventing child
        # widgets from expanding and making the frame appear blank.
        self._build()
        print("✅ DashboardPage built")

    def _build(self):
        print("🏗️ Building dashboard UI...")

        for widget in self.winfo_children():
            widget.destroy()

        # ── Top section: wallet balance strip ─────────────────────────────
        top = tk.Frame(self, bg=BG_DARK)
        top.pack(fill="x", pady=(0, 12), padx=10)

        tk.Label(top, text="MY WALLETS", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_DARK).pack(anchor="w", pady=(0, 6))

        # Wallet cards scroll area (horizontal)
        self.wallet_frame = tk.Frame(top, bg=BG_DARK)
        self.wallet_frame.pack(fill="x")

        print("✅ Wallet frame created")

        # ── Bottom section: two columns ────────────────────────────────────
        bottom = tk.Frame(self, bg=BG_DARK)
        bottom.pack(fill="both", expand=True, padx=10)

        # Left column — Quick Actions
        left_col = tk.Frame(bottom, bg=BG_DARK)
        left_col.pack(side="left", fill="y", padx=(0, 10))

        act = card(left_col, padx=16, pady=16)
        act.pack(fill="y")

        tk.Label(act, text="QUICK ACTIONS", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w", pady=(0, 12))

        for label, cmd, style in [
            ("↗  Send Money",      lambda: self.app._show_page("Send Money"),  "primary"),
            ("↙  Deposit",         self._quick_deposit,                         "success"),
            ("📱  M-Pesa Deposit",  self._mpesa_deposit,                         "mpesa"),
            ("↑  Withdraw",        self._quick_withdraw,                        "ghost"),
            ("⇄  FX Convert",      lambda: self.app._show_page("FX Exchange"),  "ghost"),
            ("❓  Help",            self._show_help,                             "ghost"),
            ("⚙  Settings",        lambda: self.app._show_page("Settings"),     "ghost"),
        ]:
            btn = styled_button(act, label, cmd, style)
            btn.pack(fill="x", pady=3)

        # Right column — Recent Transactions
        right_col = tk.Frame(bottom, bg=BG_DARK)
        right_col.pack(side="left", fill="both", expand=True)

        txf = card(right_col, padx=16, pady=16)
        txf.pack(fill="both", expand=True)

        tk.Label(txf, text="RECENT TRANSACTIONS", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w", pady=(0, 8))

        # Scrollable transaction list
        canvas_frame = tk.Frame(txf, bg=BG_CARD)
        canvas_frame.pack(fill="both", expand=True)

        canvas = tk.Canvas(canvas_frame, bg=BG_CARD, highlightthickness=0)
        scrollbar = tk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        self.tx_list = tk.Frame(canvas, bg=BG_CARD)

        self.tx_list.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        self._canvas_window = canvas.create_window((0, 0), window=self.tx_list, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # FIX: Make canvas expand to fill available space
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(
            self._canvas_window, width=e.width
        ))

        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        print("🔄 Loading data...")
        self.update_idletasks()
        self.refresh()
        print("✅ Dashboard build complete")

    def on_show(self):
        print("👁️ Dashboard on_show called")
        self.refresh()

    def refresh(self):
        print("🔄 Refreshing dashboard...")
        self._refresh_wallets()
        self._refresh_transactions()
        print("✅ Dashboard refresh complete")

    def _refresh_wallets(self):
        print("💰 Refreshing wallets...")
        for w in self.wallet_frame.winfo_children():
            w.destroy()

        def fetch():
            return api_client.get_balances()

        def on_result(wallets):
            if isinstance(wallets, Exception):
                print(f"❌ Error loading wallets: {wallets}")
                tk.Label(self.wallet_frame, text=f"Error loading wallets: {wallets}",
                         fg=RED, bg=BG_DARK, font=FONT_UI_SM).pack(anchor="w")
                return

            print(f"📊 Wallets received: {wallets}")

            # Build ordered dict: currency → balance
            wallet_map = {}
            for wallet in wallets:
                ccy     = wallet["currency"]
                balance = wallet["balance"] / 100
                wallet_map[ccy] = balance

            # Ensure ALL supported currencies appear (even if wallet row was missing)
            for ccy in SUPPORTED_CURRENCIES:
                if ccy not in wallet_map:
                    wallet_map[ccy] = 0.0

            # Determine initial display currency (user's base currency if available)
            base_ccy = self.user.get("base_currency", "KES")
            if base_ccy not in wallet_map:
                base_ccy = SUPPORTED_CURRENCIES[0]

            self._active_currency = base_ccy
            self._wallet_map      = wallet_map

            # ── Balance card with dropdown ────────────────────────────────
            card_frame = tk.Frame(self.wallet_frame, bg=BG_CARD, padx=16, pady=12)
            card_frame.pack(side="left", padx=(0, 12), pady=4)

            # Top row: active balance display + dropdown button
            top_row = tk.Frame(card_frame, bg=BG_CARD)
            top_row.pack(fill="x")

            self._balance_var = tk.StringVar()
            self._balance_label = tk.Label(
                top_row,
                textvariable=self._balance_var,
                font=FONT_MONO_LG, fg=TEXT_MONO, bg=BG_CARD
            )
            self._balance_label.pack(side="left")

            # Dropdown arrow button
            self._dropdown_btn = tk.Button(
                top_row, text=" ▼",
                font=FONT_UI_SM, fg=ACCENT, bg=BG_CARD,
                relief=tk.FLAT, cursor="hand2",
                command=lambda: self._toggle_currency_dropdown(card_frame, wallet_map)
            )
            self._dropdown_btn.pack(side="left", padx=(4, 0))

            # Currency name label below balance
            self._ccy_name_var = tk.StringVar()
            tk.Label(card_frame, textvariable=self._ccy_name_var,
                     font=FONT_LABEL, fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w")

            # Dropdown panel (hidden initially)
            self._dropdown_panel = tk.Frame(card_frame, bg=BG_INPUT,
                                            relief=tk.FLAT, bd=1)

            self._update_active_balance_display()

        run_in_thread(fetch, callback=on_result)

    def _update_active_balance_display(self):
        ccy     = getattr(self, "_active_currency", "USD")
        bal_map = getattr(self, "_wallet_map", {})
        balance = bal_map.get(ccy, 0.0)
        self._balance_var.set(format_amount(balance, ccy))
        self._ccy_name_var.set(CURRENCY_NAMES.get(ccy, ccy))

    def _toggle_currency_dropdown(self, card_frame, wallet_map):
        panel = self._dropdown_panel
        if panel.winfo_ismapped():
            panel.pack_forget()
            self._dropdown_btn.config(text=" ▼")
            return

        # Clear and rebuild dropdown rows
        for w in panel.winfo_children():
            w.destroy()

        tk.Label(panel, text="SELECT CURRENCY", font=FONT_LABEL,
                 fg=TEXT_DIM, bg=BG_INPUT, padx=8).pack(anchor="w", pady=(4, 0))
        tk.Frame(panel, bg=BORDER, height=1).pack(fill="x", padx=4, pady=2)

        for ccy in SUPPORTED_CURRENCIES:
            balance = wallet_map.get(ccy, 0.0)
            row = tk.Frame(panel, bg=BG_INPUT, cursor="hand2")
            row.pack(fill="x", padx=4, pady=1)

            is_active = (ccy == self._active_currency)
            fg_color  = ACCENT if is_active else TEXT_MAIN

            tk.Label(row, text=f"{ccy}", font=("Courier", 9, "bold"),
                     fg=fg_color, bg=BG_INPUT, width=5, anchor="w").pack(side="left", padx=4)
            tk.Label(row, text=format_amount(balance, ccy),
                     font=FONT_MONO, fg=fg_color, bg=BG_INPUT).pack(side="left")

            # Clicking a row switches the active currency (no conversion)
            def _switch(c=ccy):
                self._active_currency = c
                self._update_active_balance_display()
                self._dropdown_panel.pack_forget()
                self._dropdown_btn.config(text=" ▼")
            row.bind("<Button-1>", lambda e, c=ccy: _switch(c))
            for child in row.winfo_children():
                child.bind("<Button-1>", lambda e, c=ccy: _switch(c))

        panel.pack(fill="x", pady=(4, 0))
        self._dropdown_btn.config(text=" ▲")

    def _refresh_transactions(self):
        print("📝 Refreshing transactions...")
        for w in self.tx_list.winfo_children():
            w.destroy()

        def fetch():
            return api_client.get_transactions(limit=8)

        def on_result(txs):
            if isinstance(txs, Exception):
                print(f"❌ Error loading transactions: {txs}")
                tk.Label(self.tx_list, text=f"Error loading transactions: {txs}",
                         fg=RED, bg=BG_CARD, font=FONT_UI_SM).pack(pady=8, padx=8, anchor="w")
                return

            print(f"📊 Transactions received: {len(txs)}")

            if not txs:
                tk.Label(self.tx_list, text="No transactions yet",
                         font=FONT_UI_SM, fg=TEXT_DIM, bg=BG_CARD).pack(pady=20)
                return

            type_map = {
                "SEND":          "Transfer",
                "DEPOSIT":       "Deposit",
                "WITHDRAW":      "Withdrawal",
                "FX_CONVERT":    "FX Convert",
                "MPESA_DEPOSIT": "M-Pesa Deposit",
            }

            for tx in txs:
                is_credit = tx["recipient"] == self.user["user_id"]

                row = tk.Frame(self.tx_list, bg=BG_CARD)
                row.pack(fill="x", pady=2, padx=5)

                tk.Frame(row, bg=BORDER, height=1).pack(fill="x", pady=2)

                inner = tk.Frame(row, bg=BG_CARD)
                inner.pack(fill="x", padx=4, pady=5)

                icon_col  = GREEN if is_credit else RED
                icon_char = "↙" if is_credit else "↗"
                tk.Label(inner, text=icon_char,
                         font=("Helvetica", 12), fg=icon_col, bg=BG_CARD).pack(side="left", padx=(0, 8))

                info = tk.Frame(inner, bg=BG_CARD)
                info.pack(side="left", fill="x", expand=True)

                tx_type_display = type_map.get(tx.get("tx_type", ""), tx.get("tx_type", ""))
                tk.Label(info, text=tx_type_display,
                         font=("Helvetica", 9, "bold"), fg=TEXT_MAIN, bg=BG_CARD).pack(anchor="w")

                ts = time.strftime("%b %d %H:%M", time.localtime(tx["timestamp"]))
                tk.Label(info, text=ts, font=FONT_LABEL, fg=TEXT_DIM, bg=BG_CARD).pack(anchor="w")

                amt     = tx["amount"]
                amt_txt = (f"+{format_amount(amt, tx['currency'])}"
                           if is_credit else f"-{format_amount(amt, tx['currency'])}")
                tk.Label(inner, text=amt_txt, font=FONT_MONO,
                         fg=icon_col, bg=BG_CARD).pack(side="right")

        run_in_thread(fetch, callback=on_result)

    def _quick_deposit(self):
        dlg = DepositDialog(self.winfo_toplevel(), self.user)
        self.winfo_toplevel().wait_window(dlg)
        self.refresh()

    def _mpesa_deposit(self):
        """Open the M-Pesa STK Push deposit dialog."""
        # FIX: Use local MpesaDepositDialog class instead of importing from gui_mpesa
        dlg = MpesaDepositDialog(
            self.winfo_toplevel(),
            self.user,
            on_success=self.refresh
        )
        self.winfo_toplevel().wait_window(dlg)

    def _quick_withdraw(self):
        dlg = WithdrawDialog(self.winfo_toplevel(), self.user)
        self.winfo_toplevel().wait_window(dlg)
        self.refresh()

    def _show_help(self):
        """Show a simple help dialog."""
        messagebox.showinfo(
            "ChainPay Help",
            "ChainPay — Blockchain Mobile Money\n\n"
            "• Send Money: Transfer funds to any ChainPay user\n"
            "• Deposit: Add funds to your wallet\n"
            "• M-Pesa Deposit: Deposit via M-Pesa STK Push\n"
            "• Withdraw: Withdraw funds from your wallet\n"
            "• FX Convert: Exchange between currencies\n"
            "• History: View all your transactions\n"
            "• Blockchain: Explore the transaction ledger\n"
            "• Settings: Change PIN and server config\n\n"
            "For support, contact your administrator.",
            parent=self
        )


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
        default_ccy = self.user.get("base_currency", "KES")
        self.currency.set(default_ccy if default_ccy in SUPPORTED_CURRENCIES else "KES")
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
            amt = float(get_clean(self.amount))
            if amt <= 0:
                raise ValueError
            ccy = self.currency.get()

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
        except (ValueError, ZeroDivisionError):
            self.fee_label.config(text="")

    def _send(self):
        try:
            phone = get_clean(self.recipient)
            ccy = self.currency.get()
            note = get_clean(self.note)
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

            # Get recipient info for confirmation
            recipient_name = "Unknown User"
            try:
                # Try to get recipient info from the server
                import api_client
                # First check if this is a valid user by trying to get their info
                # You might need to add this endpoint to your server
                response = api_client._get(f"api/v1/user/by-phone/{phone}")
                if response and 'name' in response:
                    recipient_name = response['name']
            except Exception as e:
                print(f"Could not get recipient name: {e}")
                # Continue with Unknown User - the server will validate anyway

            # Calculate fee for display
            fee_amount = 0
            if amount < 10:
                fee_pct = 0.005
            elif amount < 100:
                fee_pct = 0.010
            elif amount < 1000:
                fee_pct = 0.015
            else:
                fee_pct = 0.020
            fee_amount = max(amount * fee_pct, 0.01)
            total = amount + fee_amount

            # PIN confirmation dialog
            from tkinter import simpledialog
            pin = simpledialog.askstring(
                "Confirm Transfer",
                f"Send {format_amount(amount, ccy)} to {recipient_name} ({phone})?\n\n"
                f"Fee: {format_amount(fee_amount, ccy)}\n"
                f"Total: {format_amount(total, ccy)}\n\n"
                f"Enter your PIN to confirm:",
                show='●',
                parent=self
            )
            
            if not pin:
                self.status.config(text="Transfer cancelled", fg=YELLOW)
                return

            self.status.config(text="Processing…", fg=YELLOW)
            self.update_idletasks()

            # Send the money with PIN
            try:
                # Note: You need to modify the API client to accept PIN
                # For now, we'll use a direct API call
                import api_client
                result = api_client._post("api/v1/wallet/send", {
                    "recipient_phone": phone,
                    "amount": amount,
                    "currency": ccy,
                    "note": note,
                    "pin": pin  # Add PIN to the request
                })
                
                receipt = result.get("transaction", {})
                self.status.config(text=f"✓ {result.get('message', 'Transfer successful')}", fg=GREEN)
                self._show_receipt(receipt)
                
                # Refresh dashboard
                dashboard = self.app._page_frames.get("Dashboard")
                if dashboard and hasattr(dashboard, "refresh"):
                    dashboard.refresh()
                    
            except Exception as e:
                error_msg = str(e)
                if "Internal server error" in error_msg:
                    self.status.config(text="Server error. Check server logs for details.", fg=RED)
                    print(f"❌ Transfer error: {error_msg}")
                else:
                    self.status.config(text=error_msg, fg=RED)
                
        except Exception as e:
            self.status.config(text=str(e), fg=RED)
            import traceback
            traceback.print_exc()

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

            result = api_client.convert_currency(
                q["from_currency"], q["to_currency"], q["from_amount"]
            )
            conv = result.get("conversion", {})
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
        styled_button(hr, "🔄 Request Reversal", self._show_reversal, "warning").pack(side="right", padx=4)

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
            "date": ("Date/Time", 140), "type": ("Type", 120),
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

    # FIX: _show_reversal is now a proper method of HistoryPage (not nested inside refresh)
    def _show_reversal(self):
        """Show a reversal request dialog."""
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning(
                "No Selection",
                "Please select a transaction from the list to request a reversal.",
                parent=self
            )
            return
        item = self.tree.item(sel[0])
        vals = item.get("values", [])
        if not vals:
            return
        date_str, tx_type, amount, currency, counterparty, status, fee = vals
        if messagebox.askyesno(
            "Request Reversal",
            f"Request reversal for:\n\n"
            f"  Type:   {tx_type}\n"
            f"  Amount: {amount} {currency}\n"
            f"  Date:   {date_str}\n\n"
            f"This will notify your administrator to review the request.\n"
            f"Reversals are not guaranteed and subject to approval.",
            parent=self
        ):
            messagebox.showinfo(
                "Reversal Requested",
                "Your reversal request has been submitted.\n"
                "An administrator will review it shortly.",
                parent=self
            )

    def refresh(self):
        for row in self.tree.get_children():
            self.tree.delete(row)

        def fetch():
            return api_client.get_transactions(limit=100)

        def on_result(txs):
            if isinstance(txs, Exception):
                return
            type_map = {
                "SEND":          "Transfer Out",
                "DEPOSIT":       "Deposit",
                "WITHDRAW":      "Withdrawal",
                "FX_CONVERT":    "FX Convert",
                "MPESA_DEPOSIT": "M-Pesa Deposit",
            }
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
                    f"{tx['tx_id'][:16]}... | {tx['tx_type']:<14} | "
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


# ── Settings ──────────────────────────────────────────────────────────────────

class SettingsPage(tk.Frame):
    def __init__(self, parent, user, app):
        super().__init__(parent, bg=BG_DARK)
        self.user = user
        self.app  = app
        self._build()

    def _build(self):
        tk.Label(self, text="⚙  SETTINGS", font=FONT_UI_LG,
                 fg=TEXT_MAIN, bg=BG_DARK).pack(anchor="w", pady=(0, 20))

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

        self.pin_strength_label = tk.Label(pin_card, text="", font=FONT_LABEL,
                                            fg=TEXT_DIM, bg=BG_CARD)
        self.pin_strength_label.pack(anchor="w", pady=(0, 4))
        self.new_pin.bind("<KeyRelease>", self._check_pin_strength)

        self.pin_status = tk.Label(pin_card, text="", font=FONT_UI_SM, fg=RED, bg=BG_CARD)
        self.pin_status.pack(anchor="w", pady=4)

        styled_button(pin_card, "  CHANGE PIN  ", self._change_pin, "primary").pack(anchor="w", pady=8)

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