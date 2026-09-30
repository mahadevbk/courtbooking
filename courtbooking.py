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

# Custom button with explicit colours so it stays legible whatever theme the app is running under.
st.markdown(
    f"""
    <a href="{NEW_APP_URL}" target="_blank" rel="noopener noreferrer"
       style="display:block; text-align:center; padding:14px 18px; margin:8px 0;
              background:#0B5D3B; color:#FFFFFF !important; font-weight:700; font-size:18px;
              text-decoration:none !important; border-radius:10px; border:2px solid #083F28;">
        ➡️ Go to the new app
    </a>
    """,
    unsafe_allow_html=True,
)

st.caption(f"New app: {NEW_APP_URL}")
