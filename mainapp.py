import os
import sqlite3
from datetime import datetime, date
from pathlib import Path

import streamlit as st

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "cleanconnect.db"

st.set_page_config(page_title="CleanConnect", page_icon="🧽", layout="wide")

def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS cleaners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, business TEXT, email TEXT NOT NULL,
            phone TEXT, service_area TEXT, services TEXT, hourly_rate REAL,
            bio TEXT, created_at TEXT
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_name TEXT NOT NULL, client_email TEXT NOT NULL,
            address TEXT NOT NULL, service TEXT NOT NULL,
            booking_date TEXT NOT NULL, details TEXT,
            cleaner_id INTEGER, cleaner_name TEXT,
            estimated_total REAL NOT NULL, status TEXT NOT NULL DEFAULT 'Requested',
            stripe_session_id TEXT, payment_status TEXT NOT NULL DEFAULT 'Unpaid',
            created_at TEXT
        )""")

def money(value):
    return f"${float(value):,.2f}"

init_db()

st.title("🧽 CleanConnect")
st.caption("A simple marketplace connecting house and business cleaners with clients.")
st.info("Prototype mode: bookings are saved on this device. Online payments work after you connect a Stripe account and add your secret key.")

# Verify a Stripe Checkout return. The payment is only marked paid after Stripe confirms it.
try:
    params = st.query_params
    session_id = params.get("session_id")
    if session_id:
        import stripe
        stripe.api_key = st.secrets.get("STRIPE_SECRET_KEY", os.getenv("STRIPE_SECRET_KEY", ""))
        if stripe.api_key:
            checkout = stripe.checkout.Session.retrieve(session_id)
            booking_id = checkout.metadata.get("booking_id")
            if checkout.payment_status == "paid" and booking_id:
                with db() as con:
                    con.execute("UPDATE bookings SET payment_status='Paid', status='Paid', stripe_session_id=? WHERE id=?",
                                (session_id, booking_id))
                st.success("Payment confirmed. Thank you!")
            else:
                st.warning("Stripe has not confirmed this payment yet.")
            st.query_params.clear()
except Exception as e:
    st.warning(f"Could not verify payment return: {e}")

def get_stripe_key():
    try:
        return st.secrets.get("STRIPE_SECRET_KEY", "")
    except Exception:
        return os.getenv("STRIPE_SECRET_KEY", "")

def create_checkout(booking):
    key = get_stripe_key()
    if not key:
        st.error("Online payments are not configured yet. Follow the Stripe setup instructions in README.md.")
        return
    try:
        import stripe
        stripe.api_key = key
        # Streamlit's public URL is needed for Stripe to return to the app.
        base_url = st.get_option("server.baseUrlPath")
        app_url = os.getenv("APP_URL", "").rstrip("/")
        if not app_url:
            st.error("Set APP_URL to your deployed app's public URL before using Stripe Checkout.")
            return
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": f"CleanConnect booking #{booking['id']} — {booking['service']}"},
                    "unit_amount": int(round(float(booking["estimated_total"]) * 100)),
                },
                "quantity": 1,
            }],
            metadata={"booking_id": str(booking["id"])},
            success_url=app_url + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=app_url,
        )
        with db() as con:
            con.execute("UPDATE bookings SET stripe_session_id=? WHERE id=?", (session.id, booking["id"]))
        st.link_button("Continue to secure Stripe checkout", session.url, type="primary")
        st.caption("Card details are entered on Stripe's hosted checkout page, not stored in CleanConnect.")
    except Exception as e:
        st.error(f"Stripe checkout could not be started: {e}")

page = st.sidebar.radio("Navigate", [
    "Find a cleaner", "Cleaner sign up", "My bookings", "Cleaner dashboard", "About & setup"
])

if page == "Find a cleaner":
    st.header("Find a cleaning professional")
    with db() as con:
        cleaners = con.execute("SELECT * FROM cleaners ORDER BY id DESC").fetchall()
    if not cleaners:
        st.warning("No cleaners have registered yet. Add a cleaner profile from “Cleaner sign up” to try the app.")
    else:
        cols = st.columns(2)
        for i, c in enumerate(cleaners):
            with cols[i % 2]:
                with st.container(border=True):
                    st.subheader(c["business"] or c["name"])
                    st.write(f"**Cleaner:** {c['name']}")
                    st.write(f"**Service area:** {c['service_area'] or 'Not specified'}")
                    st.write(f"**Services:** {c['services'] or 'General cleaning'}")
                    st.write(f"**Starting rate:** {money(c['hourly_rate'] or 0)} / hour")
                    if c["bio"]:
                        st.write(c["bio"])
                    st.caption(f"Contact: {c['email']} · {c['phone'] or 'No phone listed'}")

    st.divider()
    st.subheader("Request a cleaning")
    cleaner_options = {"No preference — match me later": None}
    for c in cleaners:
        cleaner_options[f"{c['business'] or c['name']} (#{c['id']})"] = c["id"]
    with st.form("booking_form"):
        client_name = st.text_input("Your name *")
        client_email = st.text_input("Your email *")
        address = st.text_input("Cleaning address *")
        service = st.selectbox("Type of cleaning", [
            "House cleaning", "Deep cleaning", "Move-in / move-out",
            "Office cleaning", "Retail / commercial cleaning", "Other"
        ])
        preferred_cleaner = st.selectbox("Preferred cleaner", list(cleaner_options.keys()))
        booking_date = st.date_input("Preferred date", min_value=date.today())
        hours = st.number_input("Estimated hours", min_value=1.0, max_value=24.0, value=3.0, step=0.5)
        default_rate = 30.0
        chosen_id = cleaner_options[preferred_cleaner]
        if chosen_id:
            chosen = next((x for x in cleaners if x["id"] == chosen_id), None)
            if chosen and chosen["hourly_rate"]:
                default_rate = float(chosen["hourly_rate"])
        rate = st.number_input("Hourly rate estimate (USD)", min_value=10.0, max_value=500.0, value=float(default_rate), step=5.0)
        details = st.text_area("Notes (rooms, square footage, special requests, etc.)")
        submitted = st.form_submit_button("Send cleaning request", type="primary")
        if submitted:
            if not all([client_name.strip(), client_email.strip(), address.strip()]):
                st.error("Please complete your name, email, and cleaning address.")
            else:
                total = hours * rate
                selected = next((x for x in cleaners if x["id"] == chosen_id), None) if chosen_id else None
                with db() as con:
                    cur = con.execute("""INSERT INTO bookings
                        (client_name,client_email,address,service,booking_date,details,cleaner_id,cleaner_name,estimated_total,status,created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,'Requested',?)""",
                        (client_name.strip(), client_email.strip(), address.strip(), service,
                         booking_date.isoformat(), details.strip(), chosen_id,
                         (selected["business"] or selected["name"]) if selected else None,
                         total, datetime.now().isoformat(timespec="seconds")))
                    booking_id = cur.lastrowid
                st.success(f"Request #{booking_id} sent! Estimated total: {money(total)}. The cleaner must confirm the job before payment.")
                st.session_state["last_booking_id"] = booking_id

elif page == "Cleaner sign up":
    st.header("Join CleanConnect as a cleaner")
    st.write("Create a public profile so nearby clients can discover your services.")
    with st.form("cleaner_form"):
        name = st.text_input("Your full name *")
        business = st.text_input("Business name (optional)")
        email = st.text_input("Business email *")
        phone = st.text_input("Phone number")
        area = st.text_input("Cities / ZIP codes served")
        services = st.multiselect("Services offered", [
            "Standard house cleaning", "Deep cleaning", "Move-in / move-out",
            "Office cleaning", "Retail cleaning", "Post-construction cleaning",
            "Window cleaning", "Other"
        ])
        rate = st.number_input("Hourly starting rate (USD)", min_value=10.0, max_value=500.0, value=30.0, step=5.0)
        bio = st.text_area("About your cleaning service")
        ok = st.checkbox("I agree that my profile contact information will be visible to app users.")
        submit = st.form_submit_button("Create cleaner profile", type="primary")
        if submit:
            if not name.strip() or not email.strip() or not ok:
                st.error("Name, email, and profile visibility consent are required.")
            else:
                with db() as con:
                    con.execute("""INSERT INTO cleaners
                        (name,business,email,phone,service_area,services,hourly_rate,bio,created_at)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                        (name.strip(), business.strip(), email.strip(), phone.strip(), area.strip(),
                         ", ".join(services), rate, bio.strip(), datetime.now().isoformat(timespec="seconds")))
                st.success("Your cleaner profile has been created. It is now listed under Find a cleaner.")

elif page == "My bookings":
    st.header("Client booking lookup")
    email = st.text_input("Enter the email used for your booking")
    if st.button("Find my bookings"):
        if not email.strip():
            st.warning("Enter your email address.")
        else:
            with db() as con:
                rows = con.execute("SELECT * FROM bookings WHERE lower(client_email)=lower(?) ORDER BY id DESC",
                                   (email.strip(),)).fetchall()
            if not rows:
                st.info("No bookings found for that email.")
            for b in rows:
                with st.container(border=True):
                    st.subheader(f"Booking #{b['id']} — {b['service']}")
                    st.write(f"**Date:** {b['booking_date']}  ·  **Cleaner:** {b['cleaner_name'] or 'Not assigned'}")
                    st.write(f"**Address:** {b['address']}")
                    st.write(f"**Estimated total:** {money(b['estimated_total'])}")
                    st.write(f"**Status:** {b['status']}  ·  **Payment:** {b['payment_status']}")
                    if b["details"]:
                        st.write(f"**Notes:** {b['details']}")
                    if b["status"] in ("Accepted", "Confirmed") and b["payment_status"] != "Paid":
                        if st.button(f"Pay booking #{b['id']} securely", key=f"pay_{b['id']}"):
                            create_checkout(b)

elif page == "Cleaner dashboard":
    st.header("Cleaner dashboard")
    st.caption("For this starter version, enter the email used when you created your cleaner profile.")
    email = st.text_input("Cleaner email")
    if st.button("Load my jobs"):
        if not email.strip():
            st.warning("Enter your email.")
        else:
            with db() as con:
                cleaner = con.execute("SELECT * FROM cleaners WHERE lower(email)=lower(?) ORDER BY id DESC LIMIT 1",
                                      (email.strip(),)).fetchone()
                if cleaner:
                    jobs = con.execute("SELECT * FROM bookings WHERE cleaner_id=? ORDER BY id DESC",
                                       (cleaner["id"],)).fetchall()
                else:
                    jobs = []
            if not cleaner:
                st.error("No cleaner profile found for that email.")
            elif not jobs:
                st.info("No jobs assigned to your profile yet.")
            else:
                for b in jobs:
                    with st.container(border=True):
                        st.subheader(f"Booking #{b['id']} — {b['service']}")
                        st.write(f"{b['booking_date']} · {b['client_name']} · {b['address']}")
                        st.write(f"Estimated total: {money(b['estimated_total'])} · Status: {b['status']}")
                        if b["details"]:
                            st.write(b["details"])
                        a, d = st.columns(2)
                        with a:
                            if b["status"] == "Requested" and st.button("Accept job", key=f"accept_{b['id']}"):
                                with db() as con:
                                    con.execute("UPDATE bookings SET status='Accepted' WHERE id=?", (b["id"],))
                                st.success("Job accepted. The client can now pay.")
                                st.rerun()
                        with d:
                            if b["status"] in ("Requested", "Accepted") and st.button("Decline job", key=f"decline_{b['id']}"):
                                with db() as con:
                                    con.execute("UPDATE bookings SET status='Declined' WHERE id=?", (b["id"],))
                                st.rerun()

elif page == "About & setup":
    st.header("About CleanConnect")
    st.write("CleanConnect is a starter marketplace for residential and commercial cleaning services.")
    st.markdown("""
    **Included in this starter app**
    - Cleaner profiles and service listings
    - Client cleaning requests and estimated pricing
    - Cleaner job acceptance / decline
    - Client booking lookup by email
    - Stripe-hosted card checkout (after configuration)

    **Important before launching publicly**
    - This is a learning prototype, not a production marketplace.
    - There is no secure user login yet. Email lookup is not identity verification.
    - Add authentication, server-side authorization, input validation, privacy terms, and deployment security.
    - For a real marketplace, use Stripe Connect so each cleaner can receive payouts. A normal Stripe Checkout payment goes to the platform account; it does not automatically split or pay cleaners.
    - Do not store card numbers or security codes in this app.
    """)
    st.subheader("Enable Stripe test payments")
    st.markdown("""
    1. Create a Stripe account at [stripe.com](https://stripe.com).
    2. In Stripe, switch to **Test mode** and copy the **Secret key** (starts with `sk_test_`).
    3. Create `.streamlit/secrets.toml` in this project and add:

    ```toml
    STRIPE_SECRET_KEY = "sk_test_your_key_here"
    ```

    4. Before using checkout, set your public app URL in your hosting environment as `APP_URL`.
    5. Install dependencies with `pip install -r requirements.txt`, then run `streamlit run app.py`.

    Test with Stripe's test card `4242 4242 4242 4242`, any future expiry date, and any 3-digit CVC. Never use live keys until the app has proper security and payment/payout setup.
    """)
    st.subheader("Run locally")
    st.code("python3 -m venv .venv\nsource .venv/bin/activate\npip install -r requirements.txt\nstreamlit run app.py", language="bash")
