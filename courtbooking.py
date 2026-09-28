import streamlit as st

st.set_page_config(
    page_title="Mira Court Booking",
    page_icon="🎾",
    layout="centered",
)

WHATSAPP_GROUP_URL = "https://chat.whatsapp.com/CJ9flIw4gJrE8MTF1WzlhS"

# Same font treatment as the original app: Audiowide for headings and buttons, default body text.
st.markdown(
    """
<link href="https://fonts.googleapis.com/css2?family=Audiowide&display=swap" rel="stylesheet">
<style>
h1, h2, h3, .stTitle { font-family: 'Audiowide', cursive !important; }
[data-testid="stLinkButton"] *, a[data-testid^="stBaseLinkButton"], a[data-testid^="stBaseLinkButton"] * {
    font-family: 'Audiowide', cursive !important;
}
</style>
""",
    unsafe_allow_html=True,
)

st.subheader("🎾 Book that Court ...")

st.markdown(" ")

st.markdown(
    """
Dear Mira Resident,

The Mira Court Booking app is no longer able to serve the community. The sheer volume of users / abusers has been overwhelming (2,740 users as of the last count).


To the 40+ residents who contributed to help meet the hosting costs, thank you for your support.


The Mira Court Booking WhatsApp group has some like-minded individuals who are keen to organise the tennis court booking and use, going forwards.



Cheers
"""
)

st.link_button("💬 Join the WhatsApp group", WHATSAPP_GROUP_URL)
