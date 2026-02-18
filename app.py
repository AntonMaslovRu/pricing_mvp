"""
Ticket Resale MVP — Event-Centric Workspace
============================================
Streamlit + SQLite. Notion-style aesthetic.

Запуск:  streamlit run app.py
БД:      tickets_vibe.db (автоматически)
"""

from __future__ import annotations

import io
import sqlite3
import datetime
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from contextlib import contextmanager
from typing import Iterator
from urllib.request import urlopen, Request
from urllib.error import URLError

import streamlit as st
import pandas as pd

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────
DB_PATH = Path(__file__).parent / "tickets_vibe.db"

MARKET_RU = "РФ"
MARKET_KZ = "РК"

TICKET_AVAILABLE = "Доступен"
TICKET_SOLD = "Продан"

FALLBACK_RATE_RUB_AED = 25.0
FALLBACK_RATE_KZT_AED = 125.0

CBR_DAILY_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
NBK_RATES_URL = "https://www.nationalbank.kz/rss/get_rates.xml"

# Виртуальная наценка для рынка РФ: x1.111 позволяет давать скидку 10%
# не теряя итоговой маржи (1.111 × 0.9 ≈ 1.0)
VIRTUAL_MARKUP_COEFF = 1.111


# ──────────────────────────────────────────────
# Notion CSS
# ──────────────────────────────────────────────
NOTION_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

/* ═══════════════════════════════════════════
   FORCE LIGHT MODE
   ═══════════════════════════════════════════ */
:root { color-scheme: light !important; }

/* ── Global ───────────────────────────── */
.stApp,
.stApp > header,
.stApp [data-testid="stAppViewContainer"],
.stApp [data-testid="stAppViewBlockContainer"] {
    background-color: #FFFFFF !important;
    color: #37352F !important;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
}

/* ── Sidebar ──────────────────────────── */
section[data-testid="stSidebar"],
section[data-testid="stSidebar"] > div {
    background-color: #F7F6F3 !important;
    border-right: 1px solid #EDEFEF !important;
}
section[data-testid="stSidebar"] * { color: #37352F !important; }
section[data-testid="stSidebar"] .stMarkdown p,
section[data-testid="stSidebar"] .stCaption,
section[data-testid="stSidebar"] small { color: #9B9A97 !important; }

/* ── Text ─────────────────────────────── */
.stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6 {
    color: #37352F !important;
    font-weight: 600;
}
.stApp p, .stApp span, .stApp label, .stApp div { color: #37352F; }
.stApp .stCaption, .stApp small, .stApp .stCaption p { color: #9B9A97 !important; }
.stMarkdown, .stMarkdown p { color: #37352F !important; }

/* ── Inputs ───────────────────────────── */
.stApp input, .stApp textarea {
    background-color: #FFFFFF !important;
    color: #37352F !important;
    border: 1px solid #EDEFEF !important;
    border-radius: 4px !important;
    caret-color: #37352F !important;
}
.stApp input:focus, .stApp textarea:focus {
    border-color: #37352F !important;
    box-shadow: 0 0 0 2px rgba(55,53,47,0.1) !important;
}
.stApp input::placeholder, .stApp textarea::placeholder { color: #C4C2BF !important; }
.stApp label,
.stApp .stTextInput label,
.stApp .stNumberInput label,
.stApp .stSelectbox label,
.stApp .stFileUploader label {
    color: #37352F !important;
    font-weight: 500 !important;
    font-size: 13px !important;
}

/* ── Selectbox ───────────────────────── */
.stApp [data-baseweb="select"],
.stApp [data-baseweb="select"] > div {
    background-color: #FFFFFF !important;
    color: #37352F !important;
    border-color: #EDEFEF !important;
    border-radius: 4px !important;
}
.stApp [data-baseweb="select"] span { color: #37352F !important; }
.stApp [data-baseweb="popover"],
.stApp [data-baseweb="menu"],
.stApp [role="listbox"] {
    background-color: #FFFFFF !important;
    border: 1px solid #EDEFEF !important;
    border-radius: 6px !important;
    box-shadow: 0 4px 16px rgba(0,0,0,0.08) !important;
}
.stApp [data-baseweb="menu"] [role="option"],
.stApp [role="listbox"] li { color: #37352F !important; }
.stApp [data-baseweb="menu"] [role="option"]:hover,
.stApp [role="listbox"] li:hover { background-color: #F7F6F3 !important; }

/* ── Number input ─────────────────────── */
.stApp [data-testid="stNumberInput"] button,
.stApp .step-up, .stApp .step-down {
    background-color: #F7F6F3 !important;
    color: #37352F !important;
    border-color: #EDEFEF !important;
}

/* ── Buttons ──────────────────────────── */
.stApp .stButton > button {
    background-color: #37352F !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: 4px !important;
    font-weight: 500 !important;
    font-size: 14px !important;
    padding: 6px 16px !important;
    transition: opacity 0.15s ease !important;
}
.stApp .stButton > button:hover { opacity: 0.82 !important; }
.stApp .stFormSubmitButton > button {
    background-color: #37352F !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: 4px !important;
    font-weight: 500 !important;
}
.stApp .stFormSubmitButton > button:hover { opacity: 0.82 !important; }

/* Secondary / sidebar buttons */
section[data-testid="stSidebar"] .stButton > button {
    background-color: #FFFFFF !important;
    color: #37352F !important;
    border: 1px solid #EDEFEF !important;
}
section[data-testid="stSidebar"] .stButton > button:hover {
    background-color: #F7F6F3 !important;
}

/* ── Radio ────────────────────────────── */
.stApp [data-testid="stRadio"] label { color: #37352F !important; }
.stApp [role="radiogroup"] label span { color: #37352F !important; }

/* ── Expander ─────────────────────────── */
.stApp [data-testid="stExpander"] {
    background-color: #FFFFFF !important;
    border: 1px solid #EDEFEF !important;
    border-radius: 6px !important;
}
.stApp [data-testid="stExpander"] summary,
.stApp [data-testid="stExpander"] summary span {
    color: #37352F !important;
    font-weight: 500 !important;
}
section[data-testid="stSidebar"] [data-testid="stExpander"] {
    background-color: #F7F6F3 !important;
    border: 1px solid #EDEFEF !important;
}

/* ── Dataframe ────────────────────────── */
.stApp [data-testid="stDataFrame"],
.stApp .stDataFrame {
    background-color: #FFFFFF !important;
    border-radius: 6px !important;
    border: 1px solid #EDEFEF !important;
}

/* ── Alerts ───────────────────────────── */
.stApp .stAlert { border-radius: 6px !important; }

/* ── File uploader ────────────────────── */
.stApp [data-testid="stFileUploader"],
.stApp [data-testid="stFileUploader"] section {
    background-color: #FFFFFF !important;
    border-color: #EDEFEF !important;
    border-radius: 6px !important;
}
.stApp [data-testid="stFileUploader"] span,
.stApp [data-testid="stFileUploader"] small { color: #9B9A97 !important; }

/* ── Divider ──────────────────────────── */
.stApp hr { border-color: #EDEFEF !important; }


/* ═══════════════════════════════════════════
   CUSTOM COMPONENTS — Notion aesthetic
   ═══════════════════════════════════════════ */

/* ── Metric card ──────────────────────── */
.notion-card {
    background: #FFFFFF;
    border-radius: 6px;
    padding: 20px 24px;
    border: 1px solid #EDEFEF;
    margin-bottom: 12px;
}
.notion-card h3 {
    margin: 0 0 8px 0;
    font-size: 12px;
    font-weight: 500;
    color: #9B9A97 !important;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.notion-card .big-value {
    font-size: 28px;
    font-weight: 700;
    color: #37352F !important;
    line-height: 1.2;
    letter-spacing: -0.5px;
}
.notion-card .sub-value {
    font-size: 13px;
    color: #9B9A97 !important;
    margin-top: 4px;
}

/* ── Market pricing card (flat, no gradient) ── */
.card-ru, .card-kz {
    background: #FFFFFF;
    border-radius: 6px;
    border: 1px solid #EDEFEF;
    border-left: 3px solid #37352F;
    padding: 20px 24px;
    margin-bottom: 10px;
}
.card-kz { border-left-color: #9B9A97; }
.card-ru *, .card-kz * { color: #37352F !important; }
.card-ru h3, .card-kz h3 {
    margin: 0 0 6px 0;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #9B9A97 !important;
}
.card-ru .big-value, .card-kz .big-value {
    font-size: 26px;
    font-weight: 700;
    line-height: 1.2;
    color: #37352F !important;
}
.card-ru .sub-value, .card-kz .sub-value {
    font-size: 13px;
    color: #9B9A97 !important;
    margin-top: 4px;
}

/* ── Breakdown list ───────────────────── */
.breakdown-list {
    background: #F7F6F3;
    border-radius: 6px;
    padding: 14px 18px;
    border: 1px solid #EDEFEF;
    margin-top: 8px;
}
.breakdown-list .row-item {
    display: flex;
    justify-content: space-between;
    padding: 5px 0;
    font-size: 13px;
    border-bottom: 1px solid #EDEFEF;
}
.breakdown-list .row-item:last-child { border-bottom: none; }
.breakdown-list .row-item .label { color: #9B9A97 !important; }
.breakdown-list .row-item .value { font-weight: 600; color: #37352F !important; }
.breakdown-list .row-item.total {
    border-top: 1px solid #D3D0CB;
    padding-top: 8px;
    margin-top: 4px;
}
.breakdown-list .row-item.total .label,
.breakdown-list .row-item.total .value { font-weight: 700; color: #37352F !important; }
.breakdown-list .row-item.profit .value { color: #0F7B0F !important; font-weight: 700; }
.breakdown-list .row-item.virtual { background: #F0FBF0; border-radius: 3px; padding: 5px 4px; }
.breakdown-list .row-item.virtual .value { color: #0F7B0F !important; font-style: italic; }

/* ── Category card (inventory) ────────── */
.cat-card {
    background: #FFFFFF;
    border-radius: 6px;
    padding: 16px 20px;
    border: 1px solid #EDEFEF;
    margin-bottom: 12px;
}
.cat-card .cat-title {
    font-size: 15px;
    font-weight: 600;
    color: #37352F !important;
    margin-bottom: 10px;
}
.cat-card .cat-row {
    display: flex;
    justify-content: space-between;
    padding: 5px 0;
    font-size: 13px;
}
.cat-card .cat-row .cl { color: #9B9A97 !important; }
.cat-card .cat-row .cv { font-weight: 500; color: #37352F !important; }

/* ── Progress bar ─────────────────────── */
.progress-wrap {
    background: #EDEFEF;
    border-radius: 4px;
    height: 5px;
    overflow: hidden;
    margin-top: 8px;
}
.progress-fill {
    height: 100%;
    border-radius: 4px;
    background: #0F7B0F;
    transition: width 0.4s ease;
}

/* ── Live badge ───────────────────────── */
.live-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: #FFFFFF;
    border: 1px solid #EDEFEF;
    border-radius: 4px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 500;
    color: #9B9A97 !important;
}
.live-badge b { color: #37352F !important; font-weight: 600; }
.live-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: #0F7B0F;
    display: inline-block;
}
.live-dot.offline { background: #E03E3E; }

/* ── Streamlit metrics ───────────────── */
[data-testid="stMetric"] {
    background: #FFFFFF !important;
    border-radius: 6px !important;
    padding: 16px !important;
    border: 1px solid #EDEFEF !important;
}
[data-testid="stMetric"] label { color: #9B9A97 !important; }
[data-testid="stMetric"] [data-testid="stMetricValue"] { color: #37352F !important; }

/* ── Tabs ─────────────────────────────── */
.stTabs [data-baseweb="tab-list"] {
    gap: 0;
    background: transparent;
    border-bottom: 1px solid #EDEFEF;
    border-radius: 0;
    padding: 0;
    box-shadow: none;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 0 !important;
    font-weight: 500 !important;
    font-size: 14px !important;
    color: #9B9A97 !important;
    background-color: transparent !important;
    border-bottom: 2px solid transparent !important;
    padding: 8px 16px !important;
}
.stTabs [data-baseweb="tab"][aria-selected="true"] {
    background-color: transparent !important;
    color: #37352F !important;
    border-bottom: 2px solid #37352F !important;
}
.stTabs [data-baseweb="tab-highlight"] { display: none !important; }
.stTabs [data-baseweb="tab-border"] { display: none !important; }

/* ── Scrollbar ────────────────────────── */
.stApp ::-webkit-scrollbar { width: 6px; height: 6px; }
.stApp ::-webkit-scrollbar-track { background: #F7F6F3; }
.stApp ::-webkit-scrollbar-thumb { background: #D3D0CB; border-radius: 3px; }
.stApp ::-webkit-scrollbar-thumb:hover { background: #9B9A97; }
</style>
"""


# ──────────────────────────────────────────────
# Exchange Rates
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class ExchangeRates:
    rub_per_aed: float
    kzt_per_aed: float
    rub_source: str
    kzt_source: str
    fetched_at: str  # ISO timestamp


def _fetch_url(url: str, timeout: int = 10) -> bytes:
    req = Request(url, headers={"User-Agent": "TicketResaleMVP/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _parse_cbr_rate() -> float | None:
    """Parse CBR XML: RUB per 1 AED."""
    try:
        data = _fetch_url(CBR_DAILY_URL)
        root = ET.fromstring(data)
        for valute in root.findall("Valute"):
            if valute.findtext("CharCode", "") == "AED":
                nominal = int(valute.findtext("Nominal", "1"))
                value_str = valute.findtext("Value", "0").replace(",", ".")
                return float(value_str) / nominal
    except (URLError, ET.ParseError, ValueError, OSError):
        pass
    return None


def _parse_nbk_rate() -> float | None:
    """Parse NBK XML: KZT per 1 AED."""
    try:
        data = _fetch_url(NBK_RATES_URL)
        root = ET.fromstring(data)
        for item in root.iter("item"):
            title = item.findtext("title", "").strip()
            if title == "AED":
                desc = item.findtext("description", "0").replace(",", ".")
                quant_str = item.findtext("quant", "1").replace(",", ".")
                quant = float(quant_str) if quant_str else 1.0
                return float(desc) / quant
    except (URLError, ET.ParseError, ValueError, OSError):
        pass
    return None


def fetch_exchange_rates() -> ExchangeRates:
    rub = _parse_cbr_rate()
    kzt = _parse_nbk_rate()
    return ExchangeRates(
        rub_per_aed=rub if rub is not None else FALLBACK_RATE_RUB_AED,
        kzt_per_aed=kzt if kzt is not None else FALLBACK_RATE_KZT_AED,
        rub_source="CBR" if rub is not None else "fallback",
        kzt_source="NBK" if kzt is not None else "fallback",
        fetched_at=datetime.datetime.now().isoformat(timespec="seconds"),
    )


def _get_cached_rates() -> ExchangeRates:
    if "exchange_rates" not in st.session_state:
        st.session_state["exchange_rates"] = fetch_exchange_rates()
    else:
        fetched = datetime.datetime.fromisoformat(st.session_state["exchange_rates"].fetched_at)
        if (datetime.datetime.now() - fetched).total_seconds() > 300:
            st.session_state["exchange_rates"] = fetch_exchange_rates()
    return st.session_state["exchange_rates"]


# ──────────────────────────────────────────────
# Pricing Engine
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class PricingBreakdown:
    market: str
    cost_aed: float
    rate_cb: float
    cost_local: float
    selling_price: float
    tax_amount: float
    platform_fee: float
    partner_fee: float
    net_profit: float
    currency: str


class PricingEngine:
    """
    Reverse Pricing — 50% net profit target.

    РФ:  C_rub = C_aed * (Rate + 3)           P = C_rub / 0.405
    РК:  C_kzt = C_aed * (Rate * 1.10)        P = C_kzt / 0.32
    """

    RU_TAX = 0.13
    RU_PLATFORM = 0.06
    RU_RATE_MARKUP = 3.0
    RU_DENOM = 0.405

    KZ_VAT = 0.20
    KZ_PARTNER = 0.10
    KZ_PLATFORM = 0.06
    KZ_RATE_COEFF = 1.10
    KZ_DENOM = 0.32

    @classmethod
    def calculate_ru(cls, cost_aed: float, rate_cb: float) -> PricingBreakdown:
        cost_rub = cost_aed * (rate_cb + cls.RU_RATE_MARKUP)
        price = round(cost_rub / cls.RU_DENOM, 2)
        tax = round(price * cls.RU_TAX, 2)
        platform = round(price * cls.RU_PLATFORM, 2)
        profit = round(price - cost_rub - tax - platform, 2)
        return PricingBreakdown(
            market=MARKET_RU, cost_aed=cost_aed, rate_cb=rate_cb,
            cost_local=round(cost_rub, 2), selling_price=price,
            tax_amount=tax, platform_fee=platform, partner_fee=0.0,
            net_profit=profit, currency="RUB",
        )

    @classmethod
    def calculate_kz(cls, cost_aed: float, rate_cb: float) -> PricingBreakdown:
        cost_kzt = cost_aed * (rate_cb * cls.KZ_RATE_COEFF)
        price = round(cost_kzt / cls.KZ_DENOM, 2)
        vat = round(price * cls.KZ_VAT, 2)
        partner = round(price * cls.KZ_PARTNER, 2)
        platform = round(price * cls.KZ_PLATFORM, 2)
        profit = round(price - cost_kzt - vat - partner - platform, 2)
        return PricingBreakdown(
            market=MARKET_KZ, cost_aed=cost_aed, rate_cb=rate_cb,
            cost_local=round(cost_kzt, 2), selling_price=price,
            tax_amount=vat, platform_fee=platform, partner_fee=partner,
            net_profit=profit, currency="KZT",
        )

    @classmethod
    def calculate_both(cls, cost_aed: float, rate_rub: float, rate_kzt: float) -> tuple[PricingBreakdown, PricingBreakdown]:
        return cls.calculate_ru(cost_aed, rate_rub), cls.calculate_kz(cost_aed, rate_kzt)


# ──────────────────────────────────────────────
# Database layer
# ──────────────────────────────────────────────
@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        # Migrations: add columns if missing
        for migration in [
            "ALTER TABLE events ADD COLUMN rate_kzt REAL NOT NULL DEFAULT 0.0",
            "ALTER TABLE inventory ADD COLUMN platform TEXT NOT NULL DEFAULT ''",
        ]:
            try:
                conn.execute(migration)
            except sqlite3.OperationalError:
                pass  # column already exists

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                date TEXT NOT NULL,
                venue TEXT NOT NULL DEFAULT '',
                rate_cb REAL NOT NULL DEFAULT 0.0,
                rate_kzt REAL NOT NULL DEFAULT 0.0,
                keywords TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL REFERENCES events(id),
                sector TEXT NOT NULL DEFAULT '',
                row TEXT NOT NULL DEFAULT '',
                seat TEXT NOT NULL DEFAULT '',
                cost_aed REAL NOT NULL DEFAULT 0.0,
                platform TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Доступен',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL REFERENCES inventory(id),
                buyer_name TEXT NOT NULL DEFAULT '',
                buyer_contact TEXT NOT NULL DEFAULT '',
                market TEXT NOT NULL DEFAULT 'РФ',
                selling_price REAL NOT NULL DEFAULT 0.0,
                sold_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)


# ── Events ───────────────────────────────────
def create_event(name: str, date: str, venue: str, rate_cb: float, rate_kzt: float, keywords: str) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO events (name, date, venue, rate_cb, rate_kzt, keywords) VALUES (?,?,?,?,?,?)",
            (name, date, venue, rate_cb, rate_kzt, keywords),
        )
        return cur.lastrowid  # type: ignore[return-value]


def list_events() -> list[dict]:
    with get_db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM events ORDER BY date DESC").fetchall()]


def get_event(event_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return dict(row) if row else None


def update_event_rate(event_id: int, rate_cb: float) -> None:
    with get_db() as conn:
        conn.execute("UPDATE events SET rate_cb=? WHERE id=?", (rate_cb, event_id))


def update_event_rate_kzt(event_id: int, rate_kzt: float) -> None:
    with get_db() as conn:
        conn.execute("UPDATE events SET rate_kzt=? WHERE id=?", (rate_kzt, event_id))


def update_event_keywords(event_id: int, keywords: str) -> None:
    with get_db() as conn:
        conn.execute("UPDATE events SET keywords=? WHERE id=?", (keywords, event_id))


# ── Inventory ────────────────────────────────
def add_ticket(event_id: int, sector: str, row: str, seat: str, cost_aed: float, platform: str = "") -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO inventory (event_id, sector, row, seat, cost_aed, platform) VALUES (?,?,?,?,?,?)",
            (event_id, sector, row, seat, cost_aed, platform),
        )
        return cur.lastrowid  # type: ignore[return-value]


def list_tickets(event_id: int, status: str | None = None) -> list[dict]:
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM inventory WHERE event_id=? AND status=? ORDER BY sector, row, seat",
                (event_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM inventory WHERE event_id=? ORDER BY sector, row, seat",
                (event_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def get_ticket(ticket_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM inventory WHERE id=?", (ticket_id,)).fetchone()
        return dict(row) if row else None


def find_ticket_by_seat(event_id: int, sector: str, row: str, seat: str) -> dict | None:
    with get_db() as conn:
        r = conn.execute(
            "SELECT * FROM inventory WHERE event_id=? "
            "AND LOWER(TRIM(sector))=LOWER(TRIM(?)) "
            "AND LOWER(TRIM(row))=LOWER(TRIM(?)) "
            "AND LOWER(TRIM(seat))=LOWER(TRIM(?))",
            (event_id, sector, row, seat),
        ).fetchone()
        return dict(r) if r else None


# ── Sales ────────────────────────────────────
def create_sale(
    ticket_id: int, buyer_name: str, buyer_contact: str,
    market: str, selling_price: float, sold_at: str | None = None,
) -> int:
    with get_db() as conn:
        conn.execute("UPDATE inventory SET status=? WHERE id=?", (TICKET_SOLD, ticket_id))
        if sold_at:
            cur = conn.execute(
                "INSERT INTO sales (ticket_id, buyer_name, buyer_contact, market, selling_price, sold_at) "
                "VALUES (?,?,?,?,?,?)",
                (ticket_id, buyer_name, buyer_contact, market, selling_price, sold_at),
            )
        else:
            cur = conn.execute(
                "INSERT INTO sales (ticket_id, buyer_name, buyer_contact, market, selling_price) "
                "VALUES (?,?,?,?,?)",
                (ticket_id, buyer_name, buyer_contact, market, selling_price),
            )
        return cur.lastrowid  # type: ignore[return-value]


def list_sales(event_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("""
            SELECT s.*, i.sector, i.row, i.seat, i.cost_aed
            FROM sales s JOIN inventory i ON s.ticket_id = i.id
            WHERE i.event_id=? ORDER BY s.sold_at DESC
        """, (event_id,)).fetchall()
        return [dict(r) for r in rows]


# ── Aggregation ──────────────────────────────
def get_inventory_summary(event_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                sector AS category,
                COUNT(*) AS total,
                SUM(CASE WHEN status='Доступен' THEN 1 ELSE 0 END) AS available,
                SUM(CASE WHEN status='Продан' THEN 1 ELSE 0 END) AS sold,
                ROUND(AVG(cost_aed), 2) AS avg_cost_aed,
                ROUND(SUM(cost_aed), 2) AS total_cost_aed,
                ROUND(SUM(CASE WHEN status='Доступен' THEN cost_aed ELSE 0 END), 2) AS avail_cost_aed
            FROM inventory WHERE event_id=?
            GROUP BY sector ORDER BY sector
        """, (event_id,)).fetchall()
        return [dict(r) for r in rows]


def get_event_metrics(event_id: int) -> dict:
    with get_db() as conn:
        inv = conn.execute("""
            SELECT
                COUNT(*) AS total_tickets,
                COALESCE(SUM(cost_aed), 0) AS total_invested_aed,
                SUM(CASE WHEN status='Продан' THEN 1 ELSE 0 END) AS sold_count,
                SUM(CASE WHEN status='Доступен' THEN 1 ELSE 0 END) AS available_count,
                COALESCE(SUM(CASE WHEN status='Продан' THEN cost_aed ELSE 0 END), 0) AS sold_cost_aed
            FROM inventory WHERE event_id=?
        """, (event_id,)).fetchone()
        sales_row = conn.execute("""
            SELECT COALESCE(SUM(s.selling_price), 0) AS total_revenue
            FROM sales s JOIN inventory i ON s.ticket_id=i.id
            WHERE i.event_id=?
        """, (event_id,)).fetchone()
        return {
            "total_tickets": inv["total_tickets"],
            "total_invested_aed": inv["total_invested_aed"],
            "sold_count": inv["sold_count"],
            "available_count": inv["available_count"],
            "sold_cost_aed": inv["sold_cost_aed"],
            "total_revenue": sales_row["total_revenue"],
        }


# ──────────────────────────────────────────────
# CSV / XLSX importer — Яндекс Афиша + universal
# ──────────────────────────────────────────────
def parse_sales_file(uploaded_file) -> pd.DataFrame:
    name = uploaded_file.name.lower()
    if name.endswith(".xlsx") or name.endswith(".xls"):
        df = pd.read_excel(uploaded_file)
    else:
        content = uploaded_file.read()
        uploaded_file.seek(0)
        # Try common separators and encodings (incl. Russian Windows and Yandex Afisha BOM)
        parsed = None
        for sep in [";", ","]:
            for enc in ["utf-8-sig", "utf-8", "cp1251", "windows-1251"]:
                try:
                    candidate = pd.read_csv(io.BytesIO(content), sep=sep, encoding=enc)
                    if len(candidate.columns) > 1:
                        parsed = candidate
                        break
                except Exception:
                    continue
            if parsed is not None:
                break
        if parsed is None:
            parsed = pd.read_csv(io.BytesIO(content), sep=";", encoding="utf-8", errors="replace")
        df = parsed
    df.columns = [c.strip().lower() for c in df.columns]
    return df


def import_sales_from_df(event_id: int, df: pd.DataFrame) -> dict:
    result = {"imported": 0, "skipped": 0, "not_found": 0, "revenue": 0.0, "errors": []}

    # Extended aliases: universal + Яндекс Афиша column names
    col_map = {
        "section": [
            "section", "sector", "сектор", "секция", "category",
            "зона", "трибуна", "место проведения",
        ],
        "row": ["row", "ряд"],
        "seat": [
            "seat", "место", "seat_number", "номер места", "кресло",
        ],
        "buyer": [
            "buyer", "buyer_name", "покупатель", "имя", "customer",
            "имя покупателя", "фио покупателя",
        ],
        "contact": [
            "contact", "buyer_contact", "контакт", "телефон", "tg",
            "телефон покупателя", "email покупателя", "email",
        ],
        "market": ["market", "рынок"],
        "price": [
            "price", "selling_price", "цена", "сумма", "сумма продажи",
            "итого", "стоимость", "итоговая сумма",
        ],
        "face_value": ["номинал", "face value", "face_value", "номинальная цена"],
        "sale_date": [
            "дата/время создания заказа", "дата создания заказа",
            "дата продажи", "date", "sold_at", "дата заказа",
            "дата оформления", "дата и время заказа",
        ],
    }

    def find_col(key: str) -> str | None:
        for alias in col_map[key]:
            if alias in df.columns:
                return alias
        return None

    sec_col = find_col("section")
    row_col = find_col("row")
    seat_col = find_col("seat")
    buyer_col = find_col("buyer")
    contact_col = find_col("contact")
    market_col = find_col("market")
    price_col = find_col("price")
    date_col = find_col("sale_date")

    if not all([sec_col, row_col, seat_col]):
        result["errors"].append(
            f"Не найдены обязательные колонки Сектор/Ряд/Место. "
            f"Колонки файла: {list(df.columns)}. "
            f"Для Яндекс Афиши убедитесь, что экспорт содержит колонки 'Сектор', 'Ряд', 'Место'."
        )
        return result

    for idx, row_data in df.iterrows():
        sector = str(row_data.get(sec_col, "")).strip()
        row_val = str(row_data.get(row_col, "")).strip()
        seat_val = str(row_data.get(seat_col, "")).strip()
        if not sector and not row_val and not seat_val:
            continue
        ticket = find_ticket_by_seat(event_id, sector, row_val, seat_val)
        if ticket is None:
            result["not_found"] += 1
            result["errors"].append(f"Строка {idx + 1}: {sector}/{row_val}/{seat_val} не найден в БД")
            continue
        if ticket["status"] == TICKET_SOLD:
            result["skipped"] += 1
            continue
        buyer = str(row_data.get(buyer_col, "")).strip() if buyer_col else ""
        contact = str(row_data.get(contact_col, "")).strip() if contact_col else ""
        market = str(row_data.get(market_col, MARKET_RU)).strip() if market_col else MARKET_RU
        try:
            price = float(row_data.get(price_col, 0)) if price_col else 0.0
        except (ValueError, TypeError):
            price = 0.0
        # Extract sale date from Yandex Afisha if available
        sold_at = None
        if date_col:
            date_str = str(row_data.get(date_col, "")).strip()
            if date_str and date_str.lower() not in ("nan", "", "none"):
                sold_at = date_str
        create_sale(ticket["id"], buyer, contact, market, price, sold_at=sold_at)
        result["imported"] += 1
        result["revenue"] += price
    return result


# ──────────────────────────────────────────────
# UI helpers
# ──────────────────────────────────────────────
def fmt(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ")


def _html_card(title: str, big: str, sub: str = "", css_class: str = "notion-card") -> str:
    sub_html = f'<div class="sub-value">{sub}</div>' if sub else ""
    return f'<div class="{css_class}"><h3>{title}</h3><div class="big-value">{big}</div>{sub_html}</div>'


def _html_breakdown_row(label: str, value: str, cls: str = "") -> str:
    extra = f" {cls}" if cls else ""
    return f'<div class="row-item{extra}"><span class="label">{label}</span><span class="value">{value}</span></div>'


def render_pricing_card(bd: PricingBreakdown) -> None:
    css_cls = "card-ru" if bd.market == MARKET_RU else "card-kz"
    flag = "\U0001f1f7\U0001f1fa" if bd.market == MARKET_RU else "\U0001f1f0\U0001f1ff"
    margin_pct = (bd.net_profit / bd.selling_price * 100) if bd.selling_price else 0

    # Market card header (flat Notion style)
    st.markdown(
        f'<div class="{css_cls}">'
        f'<h3>{flag} {bd.market}</h3>'
        f'<div class="big-value">{fmt(bd.selling_price)} {bd.currency}</div>'
        f'<div class="sub-value">Прибыль: {fmt(bd.net_profit)} {bd.currency} ({margin_pct:.1f}%)</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Detailed breakdown
    rows = ""
    rows += _html_breakdown_row("Закуп (AED)", fmt(bd.cost_aed))
    if bd.market == MARKET_RU:
        rows += _html_breakdown_row("Курс (ЦБ + 3 руб)", f"{fmt(bd.rate_cb)} + 3 = {fmt(bd.rate_cb + 3)}")
    else:
        rows += _html_breakdown_row("Курс (ЦБ x 1.10)", f"{fmt(bd.rate_cb)} x 1.10 = {fmt(bd.rate_cb * 1.10)}")
    rows += _html_breakdown_row(f"Себестоимость ({bd.currency})", fmt(bd.cost_local))
    rows += _html_breakdown_row(f"Цена продажи ({bd.currency})", fmt(bd.selling_price), cls="total")
    if bd.market == MARKET_RU:
        rows += _html_breakdown_row("Налог 13%", f"\u2212{fmt(bd.tax_amount)} {bd.currency}")
        rows += _html_breakdown_row("Платформа 6%", f"\u2212{fmt(bd.platform_fee)} {bd.currency}")
    else:
        rows += _html_breakdown_row("НДС 20%", f"\u2212{fmt(bd.tax_amount)} {bd.currency}")
        rows += _html_breakdown_row("Партнёр 10%", f"\u2212{fmt(bd.partner_fee)} {bd.currency}")
        rows += _html_breakdown_row("Платформа 6%", f"\u2212{fmt(bd.platform_fee)} {bd.currency}")
    rows += _html_breakdown_row("Чистая прибыль", f"{fmt(bd.net_profit)} {bd.currency}", cls="profit")

    # Виртуальная наценка только для рынка РФ
    if bd.market == MARKET_RU:
        virtual_price = round(bd.selling_price * VIRTUAL_MARKUP_COEFF, 2)
        rows += _html_breakdown_row(
            "Виртуальная наценка (Non-refundable)",
            f"{fmt(virtual_price)} {bd.currency}",
            cls="virtual",
        )

    st.markdown(f'<div class="breakdown-list">{rows}</div>', unsafe_allow_html=True)


def render_live_badge(rates: ExchangeRates) -> None:
    fetched = datetime.datetime.fromisoformat(rates.fetched_at)
    time_str = fetched.strftime("%H:%M:%S")
    is_live = rates.rub_source != "fallback" or rates.kzt_source != "fallback"
    dot_cls = "live-dot" if is_live else "live-dot offline"
    status = "Live" if is_live else "Offline"
    st.markdown(
        f'<div class="live-badge"><span class="{dot_cls}"></span>{status} &middot; {time_str}</div>',
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────
# Page: dashboard
# ──────────────────────────────────────────────
def page_dashboard(event: dict) -> None:
    st.markdown(
        f"<h1 style='font-size:32px;font-weight:700;letter-spacing:-0.5px;margin-bottom:0;color:#37352F'>"
        f"{event['name']}</h1>",
        unsafe_allow_html=True,
    )
    st.caption(f"{event['date']}  \u00b7  {event['venue']}")

    # ── Top metrics ───────────────────────────
    metrics = get_event_metrics(event["id"])
    rate_for_m = event["rate_cb"] if event["rate_cb"] > 0 else FALLBACK_RATE_RUB_AED
    sold_cost_local = metrics["sold_cost_aed"] * (rate_for_m + PricingEngine.RU_RATE_MARKUP) if rate_for_m > 0 else 0
    net_profit_est = metrics["total_revenue"] - sold_cost_local

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(_html_card("💼 Вложено", f'{fmt(metrics["total_invested_aed"])} AED'), unsafe_allow_html=True)
    with c2:
        pct = metrics["sold_count"] / metrics["total_tickets"] * 100 if metrics["total_tickets"] > 0 else 0
        st.markdown(_html_card("🎫 Продано", f'{metrics["sold_count"]} / {metrics["total_tickets"]}', f'{pct:.0f}% реализовано'), unsafe_allow_html=True)
    with c3:
        st.markdown(_html_card("💰 Выручка", fmt(metrics["total_revenue"])), unsafe_allow_html=True)
    with c4:
        profit_str = fmt(net_profit_est) if sold_cost_local > 0 else "\u2014"
        st.markdown(_html_card("📈 Чистая прибыль", profit_str, "оценка по РФ"), unsafe_allow_html=True)

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    tab_eco, tab_inv, tab_sales, tab_mon = st.tabs(
        ["\U0001f4ca Экономика", "\U0001f3ab Инвентарь", "\U0001f4b0 Продажи", "\U0001f50d Мониторинг"]
    )

    # ── Tab 1: Экономика ─────────────────────
    with tab_eco:
        rates = _get_cached_rates()

        col_badge, col_rub, col_kzt = st.columns([1, 1, 1])
        with col_badge:
            render_live_badge(rates)
        with col_rub:
            st.markdown(
                f'<div class="live-badge">RUB/AED: <b>{fmt(rates.rub_per_aed)}</b> ({rates.rub_source})</div>',
                unsafe_allow_html=True,
            )
        with col_kzt:
            st.markdown(
                f'<div class="live-badge">KZT/AED: <b>{fmt(rates.kzt_per_aed)}</b> ({rates.kzt_source})</div>',
                unsafe_allow_html=True,
            )
        st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

        # Shared rate inputs
        cr, ck = st.columns(2)
        default_rate_rub = event["rate_cb"] if event["rate_cb"] > 0 else rates.rub_per_aed
        rate_rub = cr.number_input(
            "Курс ЦБ РФ (RUB/AED)", min_value=0.0, value=default_rate_rub, step=0.5, key=f"rate_{event['id']}",
        )
        default_rate_kzt = event.get("rate_kzt", 0.0)
        if default_rate_kzt <= 0:
            default_rate_kzt = rates.kzt_per_aed
        rate_kzt = ck.number_input(
            "Курс НБ РК (KZT/AED)", min_value=0.0, value=default_rate_kzt, step=0.5, key=f"rate_kzt_{event['id']}",
        )
        if rate_rub != event["rate_cb"]:
            update_event_rate(event["id"], rate_rub)
        if rate_kzt != event.get("rate_kzt", 0.0):
            update_event_rate_kzt(event["id"], rate_kzt)

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        st.markdown(
            "<hr style='border:none;border-top:1px solid #EDEFEF;margin:0 0 16px 0'>",
            unsafe_allow_html=True,
        )

        # ── Multiple ticket types ─────────────
        count_key = f"eco_count_{event['id']}"
        if count_key not in st.session_state:
            st.session_state[count_key] = 1

        hdr_col, add_col, rm_col = st.columns([5, 1, 1])
        hdr_col.markdown("**🎫 Сравнение типов билетов**")
        if add_col.button("＋ Тип", key=f"eco_add_{event['id']}"):
            if st.session_state[count_key] < 4:
                st.session_state[count_key] += 1
                st.rerun()
        if rm_col.button("− Убрать", key=f"eco_rm_{event['id']}"):
            if st.session_state[count_key] > 1:
                st.session_state[count_key] -= 1
                st.rerun()

        count = st.session_state[count_key]
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        # Input row — one column per ticket type
        input_cols = st.columns(count)
        costs_all: list[float] = []
        labels_all: list[str] = []
        for i in range(count):
            with input_cols[i]:
                lbl = st.text_input(
                    "Название типа",
                    value=f"Тип {i + 1}",
                    key=f"eco_lbl_{event['id']}_{i}",
                )
                cst = st.number_input(
                    "Цена закупа (AED)",
                    min_value=0.0,
                    value=100.0,
                    step=5.0,
                    key=f"eco_cost_{event['id']}_{i}",
                )
                labels_all.append(lbl)
                costs_all.append(cst)

        # Results row
        if rate_rub > 0 or rate_kzt > 0:
            res_cols = st.columns(count)
            for i in range(count):
                with res_cols[i]:
                    cst = costs_all[i]
                    lbl = labels_all[i]
                    if count > 1:
                        st.markdown(
                            f"<div style='font-size:13px;font-weight:600;color:#9B9A97;"
                            f"text-transform:uppercase;letter-spacing:0.5px;"
                            f"margin-bottom:8px'>{lbl}</div>",
                            unsafe_allow_html=True,
                        )
                    if cst <= 0:
                        st.caption("Укажите цену закупа")
                        continue
                    if rate_rub > 0:
                        bd_ru = PricingEngine.calculate_ru(cst, rate_rub)
                        render_pricing_card(bd_ru)
                    if rate_kzt > 0:
                        bd_kz = PricingEngine.calculate_kz(cst, rate_kzt)
                        render_pricing_card(bd_kz)
        else:
            st.info("Укажите курсы ЦБ для расчёта.")

    # ── Tab 2: Инвентарь ─────────────────────
    with tab_inv:
        st.subheader("Добавить билеты")
        with st.form(f"add_ticket_{event['id']}", clear_on_submit=True):
            f1, f2, f3, f4, f5, f6 = st.columns([2, 1.2, 1.5, 1.5, 1, 2])
            t_sec = f1.text_input("Сектор")
            t_row = f2.text_input("Ряд")
            t_seat = f3.text_input("Место (начало)")
            t_cost = f4.number_input("Цена (AED)", min_value=0.0, step=10.0)
            t_qty = f5.number_input("Кол-во", min_value=1, value=1, step=1)
            t_platform = f6.text_input("Платформа/Приложение")
            if st.form_submit_button("➕ Добавить билеты"):
                if t_cost <= 0:
                    st.warning("Укажите цену закупа.")
                else:
                    qty = int(t_qty)
                    for j in range(qty):
                        if qty > 1 and t_seat:
                            try:
                                seat_label = str(int(t_seat) + j)
                            except ValueError:
                                seat_label = f"{t_seat}-{j + 1}"
                        else:
                            seat_label = t_seat
                        add_ticket(event["id"], t_sec, t_row, seat_label, t_cost, t_platform)
                    st.success(f"{'Билет добавлен' if qty == 1 else f'{qty} билетов добавлено'}!")
                    st.rerun()

        # ── Category cards ───────────────────
        summary = get_inventory_summary(event["id"])
        if summary:
            rate_rub_val = event["rate_cb"] if event["rate_cb"] > 0 else FALLBACK_RATE_RUB_AED
            rate_kzt_val = event.get("rate_kzt", 0.0)
            if rate_kzt_val <= 0:
                rate_kzt_val = FALLBACK_RATE_KZT_AED

            total_all = sum(s["total"] for s in summary)
            sold_all = sum(s["sold"] for s in summary)
            pct = sold_all / total_all if total_all > 0 else 0
            st.markdown(
                f"<div class='notion-card'>"
                f"<h3>📊 Прогресс продаж</h3>"
                f"<div class='big-value'>{sold_all} / {total_all}</div>"
                f"<div class='sub-value'>{pct:.0%} реализовано</div>"
                f"<div class='progress-wrap'><div class='progress-fill' style='width:{pct*100:.1f}%'></div></div>"
                f"</div>",
                unsafe_allow_html=True,
            )

            cols = st.columns(min(len(summary), 3))
            for i, s in enumerate(summary):
                cat_name = s["category"] or "(без сектора)"
                potential_ru = 0.0
                potential_kz = 0.0
                if s["available"] > 0:
                    if rate_rub_val > 0:
                        bd_ru = PricingEngine.calculate_ru(s["avg_cost_aed"], rate_rub_val)
                        potential_ru = round(bd_ru.selling_price * s["available"], 2)
                    if rate_kzt_val > 0:
                        bd_kz = PricingEngine.calculate_kz(s["avg_cost_aed"], rate_kzt_val)
                        potential_kz = round(bd_kz.selling_price * s["available"], 2)
                cat_pct = s["sold"] / s["total"] if s["total"] > 0 else 0

                html = (
                    f"<div class='cat-card'>"
                    f"<div class='cat-title'>{cat_name}</div>"
                    f"<div class='cat-row'><span class='cl'>Остаток</span><span class='cv'>{s['available']} шт.</span></div>"
                    f"<div class='cat-row'><span class='cl'>Продано</span><span class='cv'>{s['sold']} шт.</span></div>"
                    f"<div class='cat-row'><span class='cl'>Потрачено</span><span class='cv'>{fmt(s['avail_cost_aed'])} AED</span></div>"
                    f"<div class='cat-row'><span class='cl'>Ср. закуп</span><span class='cv'>{fmt(s['avg_cost_aed'])} AED</span></div>"
                    f"<div class='cat-row'><span class='cl'>Потенц. РФ</span><span class='cv'>{fmt(potential_ru)} RUB</span></div>"
                    f"<div class='cat-row'><span class='cl'>Потенц. РК</span><span class='cv'>{fmt(potential_kz)} KZT</span></div>"
                    f"<div class='progress-wrap' style='margin-top:12px'><div class='progress-fill' style='width:{cat_pct*100:.1f}%'></div></div>"
                    f"</div>"
                )
                with cols[i % len(cols)]:
                    st.markdown(html, unsafe_allow_html=True)
        else:
            st.info("Пока нет билетов. Добавьте первый выше.")

        # ── Full list ────────────────────────
        tickets = list_tickets(event["id"])
        if tickets:
            with st.expander("Полный список билетов"):
                df_t = pd.DataFrame(tickets)
                display_cols = ["id", "sector", "row", "seat", "cost_aed", "status"]
                display_names = ["ID", "Сектор", "Ряд", "Место", "Цена AED", "Статус"]
                if "platform" in df_t.columns:
                    display_cols.insert(-1, "platform")
                    display_names.insert(-1, "Платформа")
                df_t = df_t[display_cols]
                df_t.columns = display_names
                st.dataframe(df_t, use_container_width=True, hide_index=True)

    # ── Tab 3: Продажи ───────────────────────
    with tab_sales:
        st.subheader("Загрузка продаж из файла")
        st.caption(
            "Поддерживается экспорт **Яндекс Афиши** (CSV `;`) и любой XLSX/CSV. "
            "Обязательные колонки: **Сектор**, **Ряд**, **Место** (или аналоги). "
            "Дополнительно: Покупатель, Телефон покупателя, Email покупателя, Сумма продажи, "
            "Дата/время создания заказа."
        )
        uploaded = st.file_uploader("Выберите файл", type=["csv", "xlsx", "xls"], key=f"upload_{event['id']}")
        if uploaded is not None:
            try:
                df_upload = parse_sales_file(uploaded)
                st.markdown(f"Прочитано **{len(df_upload)}** строк. Колонки: `{list(df_upload.columns)}`")
                st.dataframe(df_upload.head(10), use_container_width=True, hide_index=True)
                if st.button("Импортировать продажи", key=f"import_{event['id']}"):
                    result = import_sales_from_df(event["id"], df_upload)
                    if result["errors"] and result["imported"] == 0:
                        for err in result["errors"]:
                            st.error(err)
                    else:
                        st.success(
                            f"Успешно загружено **{result['imported']}** продаж, "
                            f"выручка **{fmt(result['revenue'])}**"
                        )
                        if result["skipped"]:
                            st.info(f"Пропущено (уже продано): {result['skipped']}")
                        if result["not_found"]:
                            st.warning(f"Не найдено в БД: {result['not_found']}")
                        if result["errors"]:
                            with st.expander("Детали"):
                                for e in result["errors"]:
                                    st.text(e)
                        st.rerun()
            except Exception as e:
                st.error(f"Ошибка чтения файла: {e}")

        st.markdown("---")

        # Manual sale
        st.subheader("Оформить продажу вручную")
        available = list_tickets(event["id"], status=TICKET_AVAILABLE)
        if not available:
            st.info("Нет доступных билетов.")
        else:
            rate_rub_val = event["rate_cb"] if event["rate_cb"] > 0 else FALLBACK_RATE_RUB_AED
            rate_kzt_val = event.get("rate_kzt", 0.0)
            if rate_kzt_val <= 0:
                rate_kzt_val = FALLBACK_RATE_KZT_AED
            opts = {f"#{t['id']}  {t['sector']}/{t['row']}/{t['seat']} \u2014 {t['cost_aed']} AED": t["id"] for t in available}
            with st.form(f"sell_{event['id']}", clear_on_submit=True):
                chosen = st.selectbox("Билет", list(opts.keys()))
                s_name = st.text_input("Имя покупателя")
                s_contact = st.text_input("Контакт (TG / Телефон)")
                s_market = st.selectbox("Рынок", [MARKET_RU, MARKET_KZ])
                auto_price = 0.0
                if chosen:
                    tid = opts[chosen]
                    tk = get_ticket(tid)
                    if tk:
                        if s_market == MARKET_RU and rate_rub_val > 0:
                            bd = PricingEngine.calculate_ru(tk["cost_aed"], rate_rub_val)
                        elif s_market == MARKET_KZ and rate_kzt_val > 0:
                            bd = PricingEngine.calculate_kz(tk["cost_aed"], rate_kzt_val)
                        else:
                            bd = None
                        if bd:
                            auto_price = bd.selling_price
                            st.markdown(f"**Рекомендованная цена:** {fmt(auto_price)} {bd.currency}")
                s_price = st.number_input("Финальная цена продажи", min_value=0.0, value=auto_price, step=100.0)
                if st.form_submit_button("Оформить продажу"):
                    if not s_name.strip():
                        st.warning("Укажите имя покупателя.")
                    elif s_price <= 0:
                        st.warning("Укажите цену.")
                    else:
                        create_sale(opts[chosen], s_name.strip(), s_contact.strip(), s_market, s_price)
                        st.success("Продажа оформлена!")
                        st.rerun()

        # Sales history
        st.subheader("История продаж")
        sales = list_sales(event["id"])
        if sales:
            df_s = pd.DataFrame(sales)[
                ["id", "sector", "row", "seat", "buyer_name", "buyer_contact", "market", "selling_price", "cost_aed", "sold_at"]
            ]
            df_s.columns = ["ID", "Сектор", "Ряд", "Место", "Покупатель", "Контакт", "Рынок", "Цена продажи", "Закуп AED", "Дата"]
            st.dataframe(df_s, use_container_width=True, hide_index=True)
            total = sum(s["selling_price"] for s in sales)
            total_cost = sum(s["cost_aed"] for s in sales)
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("Общая выручка", fmt(total))
            mc2.metric("Закуп (AED)", fmt(total_cost))
            mc3.metric("Кол-во продаж", len(sales))
        else:
            st.info("Продаж пока нет.")

    # ── Tab 4: Мониторинг ────────────────────
    with tab_mon:
        st.subheader("Мониторинг рынка")
        st.caption("Поиск предложений в TG / Avito (заглушка)")
        kw_current = event.get("keywords", "")
        keywords = st.text_input("Ключевые слова", value=kw_current, key=f"kw_{event['id']}")
        if keywords != kw_current:
            update_event_keywords(event["id"], keywords)
        if st.button("\U0001f50d Поиск", key=f"search_{event['id']}"):
            if not keywords.strip():
                st.warning("Введите ключевые слова.")
            else:
                st.info(f"Поиск: **{keywords}**")
                mock = [
                    {"source": "Telegram", "text": f"Продаю билеты на {event['name']}", "date": "2025-01-15"},
                    {"source": "Avito", "text": f"{event['name']} \u2014 2 билета, сектор A", "date": "2025-01-14"},
                    {"source": "Telegram", "text": f"Ищу билеты на {event['name']}, куплю дорого", "date": "2025-01-13"},
                ]
                for i, r in enumerate(mock):
                    cols = st.columns([1, 5])
                    cols[0].markdown(f"**{r['source']}**")
                    cols[1].markdown(r["text"])
                    cols[1].caption(r["date"])
                    if i < len(mock) - 1:
                        st.divider()
        st.markdown("---")
        st.caption("В будущем \u2014 подключение к Telegram API и парсинг Avito.")


# ──────────────────────────────────────────────
# Sidebar
# ──────────────────────────────────────────────
def sidebar() -> int | None:
    st.sidebar.title("\U0001f3ab Ticket Resale")
    rates = _get_cached_rates()
    render_sidebar_rates(rates)
    st.sidebar.markdown("---")

    with st.sidebar.expander("\u2795 Создать событие", expanded=False):
        with st.form("new_event", clear_on_submit=True):
            e_name = st.text_input("Название")
            e_date = st.date_input("Дата", value=datetime.date.today())
            e_venue = st.text_input("Место проведения")
            e_rate = st.number_input("Курс ЦБ РФ (RUB/AED)", min_value=0.0, value=rates.rub_per_aed, step=0.5)
            e_rate_kzt = st.number_input("Курс НБ РК (KZT/AED)", min_value=0.0, value=rates.kzt_per_aed, step=0.5)
            e_kw = st.text_input("Ключевые слова")
            if st.form_submit_button("Создать"):
                if not e_name.strip():
                    st.warning("Укажите название.")
                else:
                    new_id = create_event(e_name.strip(), str(e_date), e_venue.strip(), e_rate, e_rate_kzt, e_kw.strip())
                    st.success(f"Событие #{new_id} создано!")
                    st.rerun()

    events = list_events()
    if not events:
        return None
    st.sidebar.markdown("### Активные события")
    emap = {f"{e['name']} ({e['date']})": e["id"] for e in events}
    chosen = st.sidebar.radio("Выберите событие", list(emap.keys()), label_visibility="collapsed")
    return emap[chosen] if chosen else None


def render_sidebar_rates(rates: ExchangeRates) -> None:
    is_live = rates.rub_source != "fallback" or rates.kzt_source != "fallback"
    dot = "\U0001f7e2" if is_live else "\U0001f7e0"
    fetched = datetime.datetime.fromisoformat(rates.fetched_at)
    st.sidebar.caption(
        f"{dot} {'Live' if is_live else 'Offline'} \u00b7 {fetched.strftime('%H:%M:%S')}\n\n"
        f"RUB/AED: **{fmt(rates.rub_per_aed)}** ({rates.rub_source})\n\n"
        f"KZT/AED: **{fmt(rates.kzt_per_aed)}** ({rates.kzt_source})"
    )
    if st.sidebar.button("Обновить курсы", key="refresh_rates"):
        st.session_state.pop("exchange_rates", None)
        st.rerun()


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
def main() -> None:
    st.set_page_config(page_title="Ticket Resale MVP", page_icon="\U0001f3ab", layout="wide")
    st.markdown(NOTION_CSS, unsafe_allow_html=True)
    init_db()

    selected_id = sidebar()
    if selected_id is None:
        st.markdown(
            "<div style='text-align:center;padding:80px 0'>"
            "<h1 style='font-size:44px;font-weight:700;letter-spacing:-1px;color:#37352F'>🎫 Ticket Resale</h1>"
            "<p style='font-size:17px;color:#9B9A97'>Создайте первое событие через боковую панель</p>"
            "</div>",
            unsafe_allow_html=True,
        )
        return

    event = get_event(selected_id)
    if event:
        page_dashboard(event)


if __name__ == "__main__":
    main()
