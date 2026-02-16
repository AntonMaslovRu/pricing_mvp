"""
Ticket Resale MVP - Event-Centric Workspace
=============================================
Streamlit + SQLite приложение для управления перепродажей билетов.
Все действия (расчёты, продажи, мониторинг) происходят внутри конкретного события.

Запуск:  streamlit run app.py
БД:      tickets_vibe.db (создаётся автоматически)
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

# Fallback rates when APIs are unavailable
FALLBACK_RATE_RUB_AED = 25.0
FALLBACK_RATE_KZT_AED = 125.0

# Central bank API URLs
CBR_DAILY_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
NBK_RATES_URL = "https://nationalbank.kz/rss/rates_all.xml"


# ──────────────────────────────────────────────
# Exchange Rates
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class ExchangeRates:
    """Курсы AED для обоих рынков."""
    rub_per_aed: float
    kzt_per_aed: float
    rub_source: str  # "CBR" or "fallback"
    kzt_source: str  # "NBK" or "fallback"


def _fetch_url(url: str, timeout: int = 10) -> bytes:
    """Fetch URL content with timeout."""
    req = Request(url, headers={"User-Agent": "TicketResaleMVP/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _parse_cbr_rate() -> float | None:
    """
    Parse CBR XML to get RUB/AED rate.
    CBR returns rates per Nominal units, e.g. AED Nominal=1 Value=XX.XXXX
    """
    try:
        data = _fetch_url(CBR_DAILY_URL)
        root = ET.fromstring(data)
        for valute in root.findall("Valute"):
            char_code = valute.findtext("CharCode", "")
            if char_code == "AED":
                nominal = int(valute.findtext("Nominal", "1"))
                value_str = valute.findtext("Value", "0").replace(",", ".")
                return float(value_str) / nominal
    except (URLError, ET.ParseError, ValueError, OSError):
        pass
    return None


def _parse_nbk_rate() -> float | None:
    """
    Parse National Bank of Kazakhstan RSS/XML to get KZT/AED rate.
    NBK returns XML with <item><title>AED</title><description>XXX.XX</description></item>
    """
    try:
        data = _fetch_url(NBK_RATES_URL)
        root = ET.fromstring(data)
        # NBK uses RSS-like format: channel > item
        for item in root.iter("item"):
            title = item.findtext("title", "")
            if title.strip() == "AED":
                desc = item.findtext("description", "0").replace(",", ".")
                quant_str = item.findtext("quant", "1").replace(",", ".")
                quant = float(quant_str) if quant_str else 1.0
                return float(desc) / quant
    except (URLError, ET.ParseError, ValueError, OSError):
        pass
    return None


def fetch_exchange_rates() -> ExchangeRates:
    """
    Fetch current AED exchange rates from CBR (Russia) and NBK (Kazakhstan).
    Falls back to hardcoded defaults if APIs are unavailable.
    """
    rub = _parse_cbr_rate()
    kzt = _parse_nbk_rate()
    return ExchangeRates(
        rub_per_aed=rub if rub is not None else FALLBACK_RATE_RUB_AED,
        kzt_per_aed=kzt if kzt is not None else FALLBACK_RATE_KZT_AED,
        rub_source="CBR" if rub is not None else "fallback",
        kzt_source="NBK" if kzt is not None else "fallback",
    )


# ──────────────────────────────────────────────
# Pricing Engine
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class PricingBreakdown:
    """Результат расчёта цены для одного рынка."""

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
    Reverse Pricing Engine.

    Рассчитывает цену продажи (P) так, чтобы прибыль составляла 50 %
    от суммы, оставшейся после всех налогов и сборов.

    Рынок РФ
    --------
    C_rub = C_aed * (Rate + 3)
    Сборы: 13 % налог + 6 % платформа = 19 % от P
    P = C_rub / 0.405

    Рынок РК
    --------
    C_kzt = C_aed * (Rate * 1.10)
    Сборы: 20 % НДС + 10 % партнёр + 6 % платформа = 36 % от P
    P = C_kzt / 0.32
    """

    # РФ
    RU_TAX = 0.13
    RU_PLATFORM = 0.06
    RU_RATE_MARKUP = 3.0
    RU_DENOM = 0.405

    # РК
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
    def calculate_both(
        cls, cost_aed: float, rate_cb: float,
    ) -> tuple[PricingBreakdown, PricingBreakdown]:
        return cls.calculate_ru(cost_aed, rate_cb), cls.calculate_kz(cost_aed, rate_cb)


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
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                date        TEXT    NOT NULL,
                venue       TEXT    NOT NULL DEFAULT '',
                rate_cb     REAL    NOT NULL DEFAULT 0.0,
                keywords    TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS inventory (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id    INTEGER NOT NULL REFERENCES events(id),
                sector      TEXT    NOT NULL DEFAULT '',
                row         TEXT    NOT NULL DEFAULT '',
                seat        TEXT    NOT NULL DEFAULT '',
                cost_aed    REAL    NOT NULL DEFAULT 0.0,
                status      TEXT    NOT NULL DEFAULT 'Доступен',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS sales (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id       INTEGER NOT NULL REFERENCES inventory(id),
                buyer_name      TEXT    NOT NULL DEFAULT '',
                buyer_contact   TEXT    NOT NULL DEFAULT '',
                market          TEXT    NOT NULL DEFAULT 'РФ',
                selling_price   REAL    NOT NULL DEFAULT 0.0,
                sold_at         TEXT    NOT NULL DEFAULT (datetime('now'))
            );
        """)


# ── Events ───────────────────────────────────
def create_event(name: str, date: str, venue: str, rate_cb: float, keywords: str) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO events (name, date, venue, rate_cb, keywords) VALUES (?,?,?,?,?)",
            (name, date, venue, rate_cb, keywords),
        )
        return cur.lastrowid  # type: ignore[return-value]


def list_events() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY date DESC").fetchall()
        return [dict(r) for r in rows]


def get_event(event_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return dict(row) if row else None


def update_event_rate(event_id: int, rate_cb: float) -> None:
    with get_db() as conn:
        conn.execute("UPDATE events SET rate_cb=? WHERE id=?", (rate_cb, event_id))


def update_event_keywords(event_id: int, keywords: str) -> None:
    with get_db() as conn:
        conn.execute("UPDATE events SET keywords=? WHERE id=?", (keywords, event_id))


# ── Inventory ────────────────────────────────
def add_ticket(event_id: int, sector: str, row: str, seat: str, cost_aed: float) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO inventory (event_id, sector, row, seat, cost_aed) VALUES (?,?,?,?,?)",
            (event_id, sector, row, seat, cost_aed),
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
    """Find a ticket by its sector/row/seat within an event (case-insensitive)."""
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
    market: str, selling_price: float,
) -> int:
    with get_db() as conn:
        conn.execute("UPDATE inventory SET status=? WHERE id=?", (TICKET_SOLD, ticket_id))
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


# ── Aggregation queries ─────────────────────
def get_inventory_summary(event_id: int) -> list[dict]:
    """Aggregate inventory by sector: total, available, sold, avg cost, potential revenue."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                sector                                          AS category,
                COUNT(*)                                        AS total,
                SUM(CASE WHEN status = 'Доступен' THEN 1 ELSE 0 END) AS available,
                SUM(CASE WHEN status = 'Продан'   THEN 1 ELSE 0 END) AS sold,
                ROUND(AVG(cost_aed), 2)                         AS avg_cost_aed,
                ROUND(SUM(cost_aed), 2)                         AS total_cost_aed
            FROM inventory
            WHERE event_id = ?
            GROUP BY sector
            ORDER BY sector
        """, (event_id,)).fetchall()
        return [dict(r) for r in rows]


def get_event_metrics(event_id: int) -> dict:
    """Top-level metrics for an event."""
    with get_db() as conn:
        inv = conn.execute("""
            SELECT
                COUNT(*)                                                AS total_tickets,
                COALESCE(SUM(cost_aed), 0)                              AS total_invested_aed,
                SUM(CASE WHEN status = 'Продан'   THEN 1 ELSE 0 END)   AS sold_count,
                SUM(CASE WHEN status = 'Доступен' THEN 1 ELSE 0 END)   AS available_count,
                COALESCE(SUM(CASE WHEN status = 'Продан' THEN cost_aed ELSE 0 END), 0) AS sold_cost_aed
            FROM inventory WHERE event_id = ?
        """, (event_id,)).fetchone()

        sales_row = conn.execute("""
            SELECT COALESCE(SUM(s.selling_price), 0) AS total_revenue
            FROM sales s JOIN inventory i ON s.ticket_id = i.id
            WHERE i.event_id = ?
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
# Sales CSV/XLSX importer
# ──────────────────────────────────────────────
def parse_sales_file(uploaded_file) -> pd.DataFrame:
    """
    Read a sales report file (CSV with ; separator, or XLSX).
    Expected columns (case-insensitive): Section, Row, Seat, Buyer, Contact, Market, Price
    """
    name = uploaded_file.name.lower()
    if name.endswith(".xlsx") or name.endswith(".xls"):
        df = pd.read_excel(uploaded_file)
    else:
        # Try semicolon-separated CSV first (total_report.csv format)
        content = uploaded_file.read()
        uploaded_file.seek(0)
        try:
            df = pd.read_csv(io.BytesIO(content), sep=";", encoding="utf-8")
        except Exception:
            df = pd.read_csv(io.BytesIO(content), sep=",", encoding="utf-8")

    # Normalize column names to lowercase stripped
    df.columns = [c.strip().lower() for c in df.columns]
    return df


def import_sales_from_df(event_id: int, df: pd.DataFrame) -> dict:
    """
    Match rows from sales report to inventory and create sales.
    Returns {"imported": N, "skipped": N, "not_found": N, "revenue": float, "errors": [str]}
    """
    result = {"imported": 0, "skipped": 0, "not_found": 0, "revenue": 0.0, "errors": []}

    # Map possible column names
    col_map = {
        "section": ["section", "sector", "сектор", "секция"],
        "row": ["row", "ряд"],
        "seat": ["seat", "место", "seat_number"],
        "buyer": ["buyer", "buyer_name", "покупатель", "имя"],
        "contact": ["contact", "buyer_contact", "контакт", "телефон", "tg"],
        "market": ["market", "рынок"],
        "price": ["price", "selling_price", "цена", "сумма"],
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

    if not all([sec_col, row_col, seat_col]):
        result["errors"].append(
            f"Не найдены обязательные колонки Section/Row/Seat. "
            f"Колонки файла: {list(df.columns)}"
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
            result["errors"].append(f"Строка {idx + 1}: билет {sector}/{row_val}/{seat_val} не найден в БД")
            continue

        if ticket["status"] == TICKET_SOLD:
            result["skipped"] += 1
            continue

        buyer = str(row_data.get(buyer_col, "")) if buyer_col else ""
        contact = str(row_data.get(contact_col, "")) if contact_col else ""
        market = str(row_data.get(market_col, MARKET_RU)) if market_col else MARKET_RU
        try:
            price = float(row_data.get(price_col, 0)) if price_col else 0.0
        except (ValueError, TypeError):
            price = 0.0

        create_sale(ticket["id"], buyer.strip(), contact.strip(), market.strip(), price)
        result["imported"] += 1
        result["revenue"] += price

    return result


# ──────────────────────────────────────────────
# UI helpers
# ──────────────────────────────────────────────
def fmt(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ")


def render_pricing_card(bd: PricingBreakdown) -> None:
    flag = "\U0001f1f7\U0001f1fa" if bd.market == MARKET_RU else "\U0001f1f0\U0001f1ff"
    st.subheader(f"{flag} {bd.market}")

    c1, c2 = st.columns(2)
    c1.metric("Цена продажи", f"{fmt(bd.selling_price)} {bd.currency}")
    c2.metric("Чистая прибыль", f"{fmt(bd.net_profit)} {bd.currency}")

    with st.expander("Детальный breakdown"):
        st.markdown(f"**Закуп (AED):** {fmt(bd.cost_aed)}")
        st.markdown(f"**Курс ЦБ:** {fmt(bd.rate_cb)}")
        st.markdown(f"**Себестоимость ({bd.currency}):** {fmt(bd.cost_local)}")
        st.markdown("---")
        st.markdown(f"**Цена продажи:** {fmt(bd.selling_price)} {bd.currency}")
        if bd.market == MARKET_RU:
            st.markdown(f"- Налог 13%: {fmt(bd.tax_amount)} {bd.currency}")
            st.markdown(f"- Платформа 6%: {fmt(bd.platform_fee)} {bd.currency}")
        else:
            st.markdown(f"- НДС 20%: {fmt(bd.tax_amount)} {bd.currency}")
            st.markdown(f"- Партнёр 10%: {fmt(bd.partner_fee)} {bd.currency}")
            st.markdown(f"- Платформа 6%: {fmt(bd.platform_fee)} {bd.currency}")
        st.markdown(f"**Прибыль:** {fmt(bd.net_profit)} {bd.currency}")
        margin = (bd.net_profit / bd.selling_price * 100) if bd.selling_price else 0
        st.markdown(f"**Маржа:** {margin:.1f}%")


def render_top_metrics(event: dict, metrics: dict, rate_cb: float) -> None:
    """Render top-level KPI cards for the event."""
    total_invested = metrics["total_invested_aed"]
    sold_cost_aed = metrics["sold_cost_aed"]
    total_revenue = metrics["total_revenue"]

    # Net profit approximation: revenue minus cost of sold tickets in local currency
    # Using RU market formula for AED→RUB conversion as primary estimate
    sold_cost_local = sold_cost_aed * (rate_cb + PricingEngine.RU_RATE_MARKUP) if rate_cb > 0 else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Всего вложено", f"{fmt(total_invested)} AED")
    c2.metric("Продано билетов", f"{metrics['sold_count']} / {metrics['total_tickets']}")
    c3.metric("Выручка от продаж", fmt(total_revenue))
    c4.metric(
        "Чистая прибыль (оценка)",
        fmt(total_revenue - sold_cost_local) if sold_cost_local > 0 else "—",
    )


# ──────────────────────────────────────────────
# Pages
# ──────────────────────────────────────────────
def page_dashboard(event: dict) -> None:
    st.title(event["name"])
    st.caption(f'{event["date"]}  \u00b7  {event["venue"]}')

    # ── Top-level metrics ────────────────────
    metrics = get_event_metrics(event["id"])
    rate_for_metrics = event["rate_cb"] if event["rate_cb"] > 0 else FALLBACK_RATE_RUB_AED
    render_top_metrics(event, metrics, rate_for_metrics)
    st.markdown("---")

    tab_eco, tab_inv, tab_sales, tab_mon = st.tabs(
        ["\U0001f4ca Экономика", "\U0001f3ab Инвентарь", "\U0001f4b0 Продажи", "\U0001f50d Мониторинг"]
    )

    # ── Tab 1: Экономика ─────────────────────
    with tab_eco:
        st.subheader("Калькулятор цены")

        # Auto-fetch rates
        rates = _get_cached_rates()
        st.caption(
            f"Курсы: RUB/AED = {fmt(rates.rub_per_aed)} ({rates.rub_source}) | "
            f"KZT/AED = {fmt(rates.kzt_per_aed)} ({rates.kzt_source})"
        )

        cc, cr = st.columns(2)
        cost_aed = cc.number_input(
            "Цена закупа (AED)", min_value=0.0, value=100.0, step=10.0,
            key=f"cost_{event['id']}",
        )
        default_rate = event["rate_cb"] if event["rate_cb"] > 0 else rates.rub_per_aed
        rate_cb = cr.number_input(
            "Курс ЦБ (AED \u2192 RUB)", min_value=0.0, value=default_rate, step=0.5,
            key=f"rate_{event['id']}",
        )
        if rate_cb != event["rate_cb"]:
            update_event_rate(event["id"], rate_cb)

        if cost_aed > 0 and rate_cb > 0:
            bd_ru, bd_kz = PricingEngine.calculate_both(cost_aed, rate_cb)
            left, right = st.columns(2)
            with left:
                render_pricing_card(bd_ru)
            with right:
                render_pricing_card(bd_kz)
        else:
            st.info("Укажите цену закупа и курс ЦБ для расчёта.")

    # ── Tab 2: Инвентарь ─────────────────────
    with tab_inv:
        st.subheader("Добавить билет")
        with st.form(f"add_ticket_{event['id']}", clear_on_submit=True):
            f1, f2, f3, f4 = st.columns(4)
            t_sec = f1.text_input("Сектор")
            t_row = f2.text_input("Ряд")
            t_seat = f3.text_input("Место")
            t_cost = f4.number_input("Цена (AED)", min_value=0.0, step=10.0)
            if st.form_submit_button("Добавить"):
                if t_cost <= 0:
                    st.warning("Укажите цену закупа.")
                else:
                    add_ticket(event["id"], t_sec, t_row, t_seat, t_cost)
                    st.success("Билет добавлен!")
                    st.rerun()

        # ── Aggregated inventory table ───────
        st.subheader("Сводка по категориям")
        summary = get_inventory_summary(event["id"])
        if summary:
            rate_cb_val = event["rate_cb"] if event["rate_cb"] > 0 else FALLBACK_RATE_RUB_AED
            agg_data = []
            for s in summary:
                # Potential revenue = available tickets * avg price (RU market)
                if s["available"] > 0 and rate_cb_val > 0:
                    bd = PricingEngine.calculate_ru(s["avg_cost_aed"], rate_cb_val)
                    potential = round(bd.selling_price * s["available"], 2)
                else:
                    potential = 0.0
                agg_data.append({
                    "Категория": s["category"] or "(без сектора)",
                    "Всего куплено": s["total"],
                    "Осталось": s["available"],
                    "Продано": s["sold"],
                    "Ср. цена AED": s["avg_cost_aed"],
                    "Потенц. выручка (RUB)": fmt(potential),
                })
            df_agg = pd.DataFrame(agg_data)
            st.dataframe(df_agg, use_container_width=True, hide_index=True)

            # Progress bar: % sold
            total_all = sum(s["total"] for s in summary)
            sold_all = sum(s["sold"] for s in summary)
            pct = sold_all / total_all if total_all > 0 else 0
            st.markdown(f"**Прогресс продаж: {sold_all} / {total_all} ({pct:.0%})**")
            st.progress(pct)
        else:
            st.info("Пока нет билетов. Добавьте первый выше.")

        # ── Full ticket list ─────────────────
        st.subheader("Полный список билетов")
        tickets = list_tickets(event["id"])
        if tickets:
            df = pd.DataFrame(tickets)[["id", "sector", "row", "seat", "cost_aed", "status"]]
            df.columns = ["ID", "Сектор", "Ряд", "Место", "Цена AED", "Статус"]
            st.dataframe(df, use_container_width=True, hide_index=True)

    # ── Tab 3: Продажи ───────────────────────
    with tab_sales:
        # ── CSV/XLSX uploader ────────────────
        st.subheader("Загрузка продаж из файла")
        st.caption(
            "Загрузите total_report.csv (разделитель ;) или XLSX. "
            "Обязательные колонки: **Section**, **Row**, **Seat**. "
            "Опционально: Buyer, Contact, Market, Price."
        )
        uploaded = st.file_uploader(
            "Выберите файл", type=["csv", "xlsx", "xls"],
            key=f"upload_{event['id']}",
        )
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
                            f"выручка составила **{fmt(result['revenue'])}**"
                        )
                        if result["skipped"] > 0:
                            st.info(f"Пропущено (уже продано): {result['skipped']}")
                        if result["not_found"] > 0:
                            st.warning(f"Не найдено в БД: {result['not_found']}")
                        if result["errors"]:
                            with st.expander("Детали ошибок"):
                                for err in result["errors"]:
                                    st.text(err)
                        st.rerun()
            except Exception as e:
                st.error(f"Ошибка чтения файла: {e}")

        st.markdown("---")

        # ── Manual sale form ─────────────────
        st.subheader("Оформить продажу вручную")
        available = list_tickets(event["id"], status=TICKET_AVAILABLE)
        if not available:
            st.info("Нет доступных билетов для продажи.")
        else:
            rate_cb_val = event["rate_cb"] if event["rate_cb"] > 0 else FALLBACK_RATE_RUB_AED
            opts = {
                f"#{t['id']}  {t['sector']}/{t['row']}/{t['seat']} \u2014 {t['cost_aed']} AED": t["id"]
                for t in available
            }
            with st.form(f"sell_{event['id']}", clear_on_submit=True):
                chosen = st.selectbox("Билет", list(opts.keys()))
                s_name = st.text_input("Имя покупателя")
                s_contact = st.text_input("Контакт (TG / Телефон)")
                s_market = st.selectbox("Рынок", [MARKET_RU, MARKET_KZ])

                auto_price = 0.0
                if chosen and rate_cb_val > 0:
                    tid = opts[chosen]
                    tk = get_ticket(tid)
                    if tk:
                        bd = (PricingEngine.calculate_ru if s_market == MARKET_RU
                              else PricingEngine.calculate_kz)(tk["cost_aed"], rate_cb_val)
                        auto_price = bd.selling_price
                        st.markdown(f"**Рекомендованная цена:** {fmt(auto_price)} {bd.currency}")

                s_price = st.number_input(
                    "Финальная цена продажи", min_value=0.0,
                    value=auto_price, step=100.0,
                )
                if st.form_submit_button("Оформить продажу"):
                    if not s_name.strip():
                        st.warning("Укажите имя покупателя.")
                    elif s_price <= 0:
                        st.warning("Укажите цену продажи.")
                    else:
                        create_sale(opts[chosen], s_name.strip(), s_contact.strip(), s_market, s_price)
                        st.success("Продажа оформлена!")
                        st.rerun()

        # ── Sales history ────────────────────
        st.subheader("История продаж")
        sales = list_sales(event["id"])
        if sales:
            df_s = pd.DataFrame(sales)[
                ["id", "sector", "row", "seat", "buyer_name", "buyer_contact",
                 "market", "selling_price", "cost_aed", "sold_at"]
            ]
            df_s.columns = ["ID", "Сектор", "Ряд", "Место", "Покупатель",
                            "Контакт", "Рынок", "Цена продажи", "Закуп AED", "Дата"]
            st.dataframe(df_s, use_container_width=True, hide_index=True)
            total = sum(s["selling_price"] for s in sales)
            total_cost = sum(s["cost_aed"] for s in sales)
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("Общая выручка", fmt(total))
            mc2.metric("Закуп (AED)", fmt(total_cost))
            mc3.metric("Количество продаж", len(sales))
        else:
            st.info("Продаж пока нет.")

    # ── Tab 4: Мониторинг ────────────────────
    with tab_mon:
        st.subheader("Мониторинг рынка")
        st.caption("Поиск предложений в TG / Avito (заглушка для будущего API)")

        kw_current = event.get("keywords", "")
        keywords = st.text_input(
            "Ключевые слова", value=kw_current, key=f"kw_{event['id']}",
        )
        if keywords != kw_current:
            update_event_keywords(event["id"], keywords)

        if st.button("\U0001f50d Поиск", key=f"search_{event['id']}"):
            if not keywords.strip():
                st.warning("Введите ключевые слова.")
            else:
                st.info(f"Поиск: **{keywords}**")
                mock = [
                    {"source": "Telegram", "channel": "@tickets_market",
                     "text": f"Продаю билеты на {event['name']}", "date": "2025-01-15", "url": "#"},
                    {"source": "Avito", "seller": "Иван П.",
                     "text": f"{event['name']} \u2014 2 билета, сектор A", "price": "15 000 RUB",
                     "date": "2025-01-14", "url": "#"},
                    {"source": "Telegram", "channel": "@event_tickets_ru",
                     "text": f"Ищу билеты на {event['name']}, куплю дорого",
                     "date": "2025-01-13", "url": "#"},
                ]
                for i, r in enumerate(mock):
                    cols = st.columns([1, 5])
                    cols[0].markdown(f"**{r['source']}**")
                    cols[1].markdown(r["text"])
                    cols[1].caption(r["date"])
                    if i < len(mock) - 1:
                        st.divider()

        st.markdown("---")
        st.caption(
            "Структура данных подготовлена: source, channel/seller, text, price, date, url. "
            "В будущем \u2014 подключение к Telegram API и парсинг Avito."
        )


def _get_cached_rates() -> ExchangeRates:
    """Fetch exchange rates with Streamlit caching (5 min TTL)."""
    if "exchange_rates" not in st.session_state:
        st.session_state["exchange_rates"] = fetch_exchange_rates()
        st.session_state["rates_fetched_at"] = datetime.datetime.now()
    else:
        elapsed = datetime.datetime.now() - st.session_state["rates_fetched_at"]
        if elapsed.total_seconds() > 300:
            st.session_state["exchange_rates"] = fetch_exchange_rates()
            st.session_state["rates_fetched_at"] = datetime.datetime.now()
    return st.session_state["exchange_rates"]


def sidebar() -> int | None:
    """Боковая панель: список событий + создание нового."""
    st.sidebar.title("\U0001f3ab Ticket Resale")

    # Show current exchange rates in sidebar
    rates = _get_cached_rates()
    st.sidebar.caption(
        f"RUB/AED: {fmt(rates.rub_per_aed)} ({rates.rub_source})\n\n"
        f"KZT/AED: {fmt(rates.kzt_per_aed)} ({rates.kzt_source})"
    )
    if st.sidebar.button("Обновить курсы", key="refresh_rates"):
        st.session_state.pop("exchange_rates", None)
        st.session_state.pop("rates_fetched_at", None)
        st.rerun()

    st.sidebar.markdown("---")

    # Создание события
    with st.sidebar.expander("\u2795 Создать событие", expanded=False):
        with st.form("new_event", clear_on_submit=True):
            e_name = st.text_input("Название")
            e_date = st.date_input("Дата", value=datetime.date.today())
            e_venue = st.text_input("Место проведения")
            e_rate = st.number_input(
                "Базовый курс ЦБ (RUB/AED)", min_value=0.0,
                value=rates.rub_per_aed, step=0.5,
            )
            e_kw = st.text_input("Ключевые слова")
            if st.form_submit_button("Создать"):
                if not e_name.strip():
                    st.warning("Укажите название.")
                else:
                    new_id = create_event(e_name.strip(), str(e_date), e_venue.strip(), e_rate, e_kw.strip())
                    st.success(f"Событие #{new_id} создано!")
                    st.rerun()

    events = list_events()
    if not events:
        return None

    st.sidebar.markdown("### Активные события")
    emap = {f"{e['name']} ({e['date']})": e["id"] for e in events}
    chosen = st.sidebar.radio("Выберите событие", list(emap.keys()), label_visibility="collapsed")
    return emap[chosen] if chosen else None


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
def main() -> None:
    st.set_page_config(page_title="Ticket Resale MVP", page_icon="\U0001f3ab", layout="wide")
    init_db()

    selected_id = sidebar()

    if selected_id is None:
        st.title("Ticket Resale MVP")
        st.info("Создайте первое событие через боковую панель, чтобы начать работу.")
        return

    event = get_event(selected_id)
    if event:
        page_dashboard(event)


if __name__ == "__main__":
    main()
