"""
Ticket Resale MVP - Event-Centric Workspace
=============================================
Streamlit + SQLite приложение для управления перепродажей билетов.
Все действия (расчёты, продажи, мониторинг) происходят внутри конкретного события.

Запуск:  streamlit run app.py
БД:      tickets_vibe.db (создаётся автоматически)
"""

from __future__ import annotations

import sqlite3
import datetime
from dataclasses import dataclass
from pathlib import Path
from contextlib import contextmanager
from typing import Iterator

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


# ──────────────────────────────────────────────
# Pages
# ──────────────────────────────────────────────
def page_dashboard(event: dict) -> None:
    st.title(event["name"])
    st.caption(f'{event["date"]}  \u00b7  {event["venue"]}')

    tab_eco, tab_inv, tab_sales, tab_mon = st.tabs(
        ["\U0001f4ca Экономика", "\U0001f3ab Инвентарь", "\U0001f4b0 Продажи", "\U0001f50d Мониторинг"]
    )

    # ── Tab 1: Экономика ─────────────────────
    with tab_eco:
        st.subheader("Калькулятор цены")
        cc, cr = st.columns(2)
        cost_aed = cc.number_input(
            "Цена закупа (AED)", min_value=0.0, value=100.0, step=10.0,
            key=f"cost_{event['id']}",
        )
        default_rate = event["rate_cb"] if event["rate_cb"] > 0 else 25.0
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

        st.subheader("Текущий сток")
        tickets = list_tickets(event["id"])
        if tickets:
            df = pd.DataFrame(tickets)[["id", "sector", "row", "seat", "cost_aed", "status"]]
            df.columns = ["ID", "Сектор", "Ряд", "Место", "Цена AED", "Статус"]
            st.dataframe(df, use_container_width=True, hide_index=True)
            avail = sum(1 for t in tickets if t["status"] == TICKET_AVAILABLE)
            sold = sum(1 for t in tickets if t["status"] == TICKET_SOLD)
            m1, m2, m3 = st.columns(3)
            m1.metric("Всего", len(tickets))
            m2.metric("Доступно", avail)
            m3.metric("Продано", sold)
        else:
            st.info("Пока нет билетов. Добавьте первый выше.")

    # ── Tab 3: Продажи ───────────────────────
    with tab_sales:
        st.subheader("Оформить продажу")
        available = list_tickets(event["id"], status=TICKET_AVAILABLE)
        if not available:
            st.info("Нет доступных билетов для продажи.")
        else:
            opts = {
                f"#{t['id']}  {t['sector']}/{t['row']}/{t['seat']} — {t['cost_aed']} AED": t["id"]
                for t in available
            }
            with st.form(f"sell_{event['id']}", clear_on_submit=True):
                chosen = st.selectbox("Билет", list(opts.keys()))
                s_name = st.text_input("Имя покупателя")
                s_contact = st.text_input("Контакт (TG / Телефон)")
                s_market = st.selectbox("Рынок", [MARKET_RU, MARKET_KZ])

                auto_price = 0.0
                if chosen and rate_cb > 0:
                    tid = opts[chosen]
                    tk = get_ticket(tid)
                    if tk:
                        bd = (PricingEngine.calculate_ru if s_market == MARKET_RU
                              else PricingEngine.calculate_kz)(tk["cost_aed"], rate_cb)
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

        st.subheader("История продаж")
        sales = list_sales(event["id"])
        if sales:
            df_s = pd.DataFrame(sales)[
                ["id", "sector", "row", "seat", "buyer_name", "buyer_contact",
                 "market", "selling_price", "sold_at"]
            ]
            df_s.columns = ["ID", "Сектор", "Ряд", "Место", "Покупатель",
                            "Контакт", "Рынок", "Цена", "Дата"]
            st.dataframe(df_s, use_container_width=True, hide_index=True)
            total = sum(s["selling_price"] for s in sales)
            mc1, mc2 = st.columns(2)
            mc1.metric("Общая выручка", fmt(total))
            mc2.metric("Количество продаж", len(sales))
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
                # Заглушка — структура для будущего API
                mock = [
                    {"source": "Telegram", "channel": "@tickets_market",
                     "text": f"Продаю билеты на {event['name']}", "date": "2025-01-15", "url": "#"},
                    {"source": "Avito", "seller": "Иван П.",
                     "text": f"{event['name']} — 2 билета, сектор A", "price": "15 000 RUB",
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
            "В будущем — подключение к Telegram API и парсинг Avito."
        )


def sidebar() -> int | None:
    """Боковая панель: список событий + создание нового."""
    st.sidebar.title("\U0001f3ab Ticket Resale")
    st.sidebar.markdown("---")

    # Создание события
    with st.sidebar.expander("\u2795 Создать событие", expanded=False):
        with st.form("new_event", clear_on_submit=True):
            e_name = st.text_input("Название")
            e_date = st.date_input("Дата", value=datetime.date.today())
            e_venue = st.text_input("Место проведения")
            e_rate = st.number_input("Базовый курс ЦБ", min_value=0.0, value=25.0, step=0.5)
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
