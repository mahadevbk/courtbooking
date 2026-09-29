import streamlit as st

NEW_APP_URL = "https://miratennis.up.railway.app"

st.set_page_config(page_title="Mira Court Booking has moved", page_icon="🎾", layout="centered")

st.markdown("<h1 style='text-align:center;'>🎾 Mira Court Booking has moved</h1>", unsafe_allow_html=True)

st.info(
    "Court bookings are now made in our **new app**. This app is retired and no longer takes bookings "
    "or logins."
)

st.success(
    "✅ **All previously made bookings have been migrated to the new app.** "
    "You'll find them there, so there's nothing you need to re-book."
)

st.link_button("➡️ Go to the new app", NEW_APP_URL, type="primary", width="stretch")

st.caption(f"New app: {NEW_APP_URL}")
