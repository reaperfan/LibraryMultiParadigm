"""Streamlit-felület: kizárólag a saját FastAPI-backendet hívja, adatbázist nem érint.

Indítás: ``streamlit run frontend/app.py``. A backend címe a ``BACKEND_URL``
környezeti változóból vagy a Streamlit ``secrets.toml`` fájlból jön.
"""

import logging
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
from api_client import ApiClient, ApiError  # noqa: E402

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("frontend")

CACHE_TTL = 30  # mp; minden írás után kézzel ürítjük, így a frissítés azonnali


def backend_url() -> str:
    try:
        return st.secrets["BACKEND_URL"]
    except (KeyError, FileNotFoundError):
        return os.getenv("BACKEND_URL", "http://127.0.0.1:8000")


client = ApiClient(backend_url(), timeout=float(os.getenv("BACKEND_TIMEOUT", "15")))


@st.cache_data(ttl=CACHE_TTL)
def load_books(q: str | None, author: str | None) -> list[dict]:
    return client.books(q, author)


@st.cache_data(ttl=CACHE_TTL)
def load_members() -> list[dict]:
    return client.members()


@st.cache_data(ttl=CACHE_TTL)
def load_loans(status: str) -> list[dict]:
    return client.loans(status)


@st.cache_data(ttl=CACHE_TTL)
def load_stats() -> dict:
    return client.stats()


def invalidate() -> None:
    """Írás után minden gyorsítótárazott lekérés érvénytelen."""
    st.cache_data.clear()


def show_error(exc: ApiError) -> None:
    st.error(str(exc))
    for reason in exc.reasons:
        st.warning(reason)


# ----------------------------------------------------------------------------
st.set_page_config(page_title="Könyvtári kölcsönzés", page_icon="📚", layout="wide")
st.title("📚 Könyvtári kölcsönzés")

with st.sidebar:
    st.caption(f"Backend: `{client.base_url}`")
    try:
        health = client.health()
        if health.get("maintenance"):
            st.warning("Karbantartás folyamatban – az adatmódosítás átmenetileg tiltott.")
        else:
            st.success("Backend elérhető")
    except ApiError as exc:
        st.error(f"Backend nem elérhető: {exc}")
    page = st.radio("Oldal", ["Katalógus", "Kölcsönzés", "Visszavétel", "Statisztika", "Adatfelvitel"])
    if st.button("Frissítés"):
        invalidate()
        st.rerun()

# ----------------------------------------------------------------------------
if page == "Katalógus":
    st.subheader("Könyvek")
    col1, col2 = st.columns(2)
    q = col1.text_input("Címrészlet")
    author = col2.text_input("Szerzőrészlet")
    try:
        books = load_books(q or None, author or None)
    except ApiError as exc:
        show_error(exc)
        books = []
    if not books:
        st.info("Nincs a szűrésnek megfelelő könyv.")
    else:
        df = pd.DataFrame(books)[["id", "title", "author", "year", "isbn", "copies_total", "available_copies"]]
        df.columns = ["ID", "Cím", "Szerző", "Év", "ISBN", "Összes példány", "Szabad példány"]
        st.dataframe(df, width="stretch", hide_index=True)

# ----------------------------------------------------------------------------
elif page == "Kölcsönzés":
    st.subheader("Új kölcsönzés")
    st.caption(
        "Szabály: van szabad példány, a tag nem érte el a limitet, nincs késedelme, "
        "és nem tartja már kint ugyanezt a könyvet."
    )
    try:
        books = load_books(None, None)
        members = load_members()
    except ApiError as exc:
        show_error(exc)
        books, members = [], []
    if books and members:
        book = st.selectbox(
            "Könyv", books, format_func=lambda b: f"{b['title']} – {b['author']} (szabad: {b['available_copies']})"
        )
        member = st.selectbox("Tag", members, format_func=lambda m: f"{m['name']} ({m['email']})")
        c1, c2 = st.columns(2)
        if c1.button("Ellenőrzés (szabály előnézet)"):
            try:
                decision = client.check_loan(book["id"], member["id"])
                if decision["allowed"]:
                    st.success("A kölcsönzés engedélyezhető.")
                else:
                    for reason in decision["reasons"]:
                        st.warning(reason)
            except ApiError as exc:
                show_error(exc)
        if c2.button("Kölcsönzés rögzítése", type="primary"):
            try:
                loan = client.create_loan(book["id"], member["id"])
                invalidate()
                st.success(f"Rögzítve: #{loan['id']} – határidő {loan['due_date']}")
            except ApiError as exc:
                show_error(exc)
    else:
        st.info("Nincs könyv vagy tag az adatbázisban.")

# ----------------------------------------------------------------------------
elif page == "Visszavétel":
    st.subheader("Aktív kölcsönzések")
    only_overdue = st.checkbox("Csak késedelmes")
    try:
        loans = load_loans("overdue" if only_overdue else "active")
    except ApiError as exc:
        show_error(exc)
        loans = []
    if not loans:
        st.info("Nincs aktív kölcsönzés.")
    else:
        df = pd.DataFrame(loans)[
            ["id", "book_title", "member_name", "loaned_at", "due_date", "days_overdue", "late_fee"]
        ]
        df.columns = ["ID", "Könyv", "Tag", "Kölcsönözve", "Határidő", "Késés (nap)", "Díj (Ft)"]
        st.dataframe(df, width="stretch", hide_index=True)
        loan = st.selectbox(
            "Visszahozott kölcsönzés",
            loans,
            format_func=lambda ln: f"#{ln['id']} {ln['book_title']} – {ln['member_name']}",
        )
        if st.button("Visszavétel rögzítése", type="primary"):
            try:
                result = client.return_loan(loan["id"])
                invalidate()
                st.success(f"Lezárva: #{result['id']}, fizetendő késedelmi díj: {result['late_fee']} Ft")
            except ApiError as exc:
                show_error(exc)

# ----------------------------------------------------------------------------
elif page == "Statisztika":
    st.subheader("Összesítés")
    try:
        stats = load_stats()
    except ApiError as exc:
        show_error(exc)
        stats = None
    if stats:
        c = st.columns(5)
        c[0].metric("Könyvek", stats["books"])
        c[1].metric("Tagok", stats["members"])
        c[2].metric("Aktív kölcsönzés", stats["loans_active"])
        c[3].metric("Késedelmes", stats["loans_overdue"])
        c[4].metric("Kintlévő díj (Ft)", stats["total_late_fee"])
        st.caption(f"Referencia-nap: {stats['reference_date']}")
        per_author = stats["loans_per_author"]
        if per_author:
            st.markdown("**Kölcsönzések száma szerzőnként**")
            df = pd.DataFrame({"Szerző": list(per_author.keys()), "Kölcsönzések": list(per_author.values())})
            st.bar_chart(df.set_index("Szerző"))
        else:
            st.info("Még nincs kölcsönzés.")

# ----------------------------------------------------------------------------
elif page == "Adatfelvitel":
    st.subheader("Új könyv")
    with st.form("book_form"):
        title = st.text_input("Cím")
        author_in = st.text_input("Szerző")
        year = st.number_input("Kiadás éve", min_value=1450, max_value=2100, value=2020)
        isbn = st.text_input("ISBN")
        copies = st.number_input("Példányszám", min_value=1, max_value=100, value=1)
        if st.form_submit_button("Könyv mentése"):
            try:
                book = client.create_book(
                    {"title": title, "author": author_in, "year": int(year), "isbn": isbn, "copies_total": int(copies)}
                )
                invalidate()
                st.success(f"Mentve: #{book['id']} {book['title']}")
            except ApiError as exc:
                show_error(exc)
    st.subheader("Új tag")
    with st.form("member_form"):
        name = st.text_input("Név")
        email = st.text_input("E-mail")
        if st.form_submit_button("Tag mentése"):
            try:
                member = client.create_member({"name": name, "email": email})
                invalidate()
                st.success(f"Mentve: #{member['id']} {member['name']}")
            except ApiError as exc:
                show_error(exc)
