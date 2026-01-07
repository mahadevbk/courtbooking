import streamlit as st
import sqlite3
from datetime import datetime, timedelta, date
import pandas as pd

# Database setup
conn = sqlite3.connect('bookings.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS bookings
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              villa TEXT,
              sub_community TEXT,
              court TEXT,
              date TEXT,
              start_hour INTEGER)''')
conn.commit()

# Sub-communities and their courts
sub_communities = {
    "Mira 1": [],
    "Mira 2": ["Mira 2"],
    "Mira 3": ["Mira Oasis 3A", "Mira Oasis 3B", "Mira Oasis 3C"],
    "Mira 4": ["Mira 4"],
    "Mira 5": ["Mira 5A", "Mira 5B"],
    "Mira Oasis 1": ["Mira Oasis 1"],
    "Mira Oasis 2": ["Mira Oasis 2"],
    "Mira Oasis 3": ["Mira Oasis 3A", "Mira Oasis 3B", "Mira Oasis 3C"]
}

# All possible courts (for reference)
all_courts = ["Mira 2", "Mira 4", "Mira 5A", "Mira 5B", "Mira Oasis 1", "Mira Oasis 2", "Mira Oasis 3A", "Mira Oasis 3B", "Mira Oasis 3C"]
start_hours = list(range(7, 22))  # 7 AM to 9 PM

# Helper functions
def get_today():
    return date.today()

def get_next_14_days():
    today = get_today()
    return [today + timedelta(days=i) for i in range(15)]

def get_bookings_for_day(date_str):
    c.execute("SELECT court, start_hour FROM bookings WHERE date=?", (date_str,))
    return set((row[0], row[1]) for row in c.fetchall())

def get_active_bookings_count(villa, sub_community):
    today = get_today()
    today_str = today.strftime('%Y-%m-%d')
    now_hour = datetime.now().hour
    c.execute("""
        SELECT COUNT(*) FROM bookings 
        WHERE villa=? AND sub_community=? AND (date > ? OR (date = ? AND start_hour >= ?))
    """, (villa, sub_community, today_str, today_str, now_hour))
    return c.fetchone()[0]

def is_slot_booked(court, date_str, start_hour):
    c.execute("SELECT 1 FROM bookings WHERE court=? AND date=? AND start_hour=?", 
              (court, date_str, start_hour))
    return c.fetchone() is not None

def book_slot(villa, sub_community, court, date_str, start_hour):
    c.execute("INSERT INTO bookings (villa, sub_community, court, date, start_hour) VALUES (?, ?, ?, ?, ?)",
              (villa, sub_community, court, date_str, start_hour))
    conn.commit()

def get_user_bookings(villa, sub_community):
    c.execute("SELECT id, court, date, start_hour FROM bookings WHERE villa=? AND sub_community=? ORDER BY date, start_hour", 
              (villa, sub_community))
    return c.fetchall()

def delete_booking(booking_id, villa, sub_community):
    c.execute("DELETE FROM bookings WHERE id=? AND villa=? AND sub_community=?", (booking_id, villa, sub_community))
    conn.commit()

# Styling
st.markdown("""
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;700&display=swap" rel="stylesheet">
<style>
    body {font-family: 'Roboto', sans-serif; background-color: #f5f7fa;}
    .stButton>button {
        background-color: #4CAF50; color: white; border: none; 
        padding: 12px 24px; border-radius: 8px; font-size: 16px;
    }
    .stButton>button:hover {background-color: #45a049;}
    h1, h2, h3 {color: #2c3e50;}
</style>
""", unsafe_allow_html=True)

st.title("🎾 Community Tennis Courts Booking System")

# Session state
if 'sub_community' not in st.session_state:
    st.session_state.sub_community = ""
if 'villa' not in st.session_state:
    st.session_state.villa = ""

# User inputs
col1, col2 = st.columns(2)
with col1:
    sub_community = st.selectbox(
        "Select Your Sub-Community",
        options=list(sub_communities.keys()),
        index=None,
        placeholder="Choose sub-community"
    )
with col2:
    villa_input = st.text_input("Enter Villa Number", placeholder="e.g. 123", value=st.session_state.villa)

if sub_community:
    st.session_state.sub_community = sub_community
if villa_input:
    st.session_state.villa = villa_input.strip().upper()

sub_community = st.session_state.sub_community
villa = st.session_state.villa

if not sub_community or not villa:
    st.warning("⚠️ Please select your sub-community and enter your villa number to continue.")
    st.stop()

# Get available courts for this sub-community
available_courts = sub_communities.get(sub_community, [])

if not available_courts:
    st.info(f"No courts available in {sub_community}.")
    st.stop()

# Tabs
tab1, tab2, tab3, tab4 = st.tabs(["📅 View Availability", "➕ Book a Slot", "📋 My Bookings", "❌ Cancel Booking"])

# === TAB 1: View Availability ===
with tab1:
    st.subheader(f"{sub_community} Court Availability")
    dates = get_next_14_days()
    selected_date = st.selectbox("Select Date:", 
                                 [d.strftime('%Y-%m-%d') for d in dates], 
                                 key="view_date_select")

    booked_slots = get_bookings_for_day(selected_date)
    time_labels = [f"{h:02d}:00 - {h+1:02d}:00" for h in start_hours]

    data = {}
    for h, label in zip(start_hours, time_labels):
        data[label] = ["Available" if (court, h) not in booked_slots else "Booked" 
                       for court in available_courts]
    df = pd.DataFrame(data, index=available_courts)

    def color_cell(val):
        if val == "Available":
            return "background-color: #d4edda; color: #155724; font-weight: bold;"
        else:
            return "background-color: #f8d7da; color: #721c24; font-weight: bold;"

    styled_df = df.style.map(color_cell)
    st.dataframe(styled_df, width="stretch")

# === TAB 2: Book a Slot ===
with tab2:
    st.subheader("Book a New Slot")
    dates = get_next_14_days()
    date_options = [d.strftime('%Y-%m-%d') for d in dates]

    selected_date = st.selectbox("Date:", date_options, key="book_date")
    selected_court = st.selectbox("Court:", available_courts, key="book_court")
    selected_time_label = st.selectbox("Time Slot:", 
                                       [f"{h:02d}:00 - {h+1:02d}:00" for h in start_hours], 
                                       key="book_time")
    start_hour = int(selected_time_label.split(":")[0])

    active_count = get_active_bookings_count(villa, sub_community)
    st.info(f"You have **{active_count} / 6** active bookings in {sub_community}.")

    if st.button("Book This Slot", type="primary"):
        if is_slot_booked(selected_court, selected_date, start_hour):
            st.error("❌ This slot was just taken.")
        elif active_count >= 6:
            st.error("🚫 You already have 6 active bookings. Cancel one first.")
        else:
            book_slot(villa, sub_community, selected_court, selected_date, start_hour)
            st.success(f"✅ Booked **{selected_court}** on **{selected_date}** at **{selected_time_label}**!")
            st.balloons()
            st.rerun()

# === TAB 3: My Bookings ===
with tab3:
    st.subheader("My Bookings")
    bookings = get_user_bookings(villa, sub_community)
    
    if not bookings:
        st.info("No bookings yet in this sub-community.")
    else:
        today_str = get_today().strftime('%Y-%m-%d')
        now_hour = datetime.now().hour
        
        active = []
        past = []
        for b in bookings:
            if b[2] > today_str or (b[2] == today_str and b[3] >= now_hour):
                active.append(b)
            else:
                past.append(b)
        
        if active:
            st.write("**Active Bookings** (count toward limit):")
            for b in active:
                st.write(f"• **{b[1]}** – {b[2]} at {b[3]:02d}:00 - {b[3]+1:02d}:00")
        
        if past:
            st.write("**Past Bookings**:")
            for b in past:
                st.write(f"• {b[1]} – {b[2]} at {b[3]:02d}:00 - {b[3]+1:02d}:00")

# === TAB 4: Cancel Booking ===
with tab4:
    st.subheader("Cancel a Booking")
    bookings = get_user_bookings(villa, sub_community)
    
    if not bookings:
        st.info("No bookings to cancel.")
    else:
        options = [f"{b[1]} on {b[2]} at {b[3]:02d}:00 - {b[3]+1:02d}:00 (ID: {b[0]})" for b in bookings]
        choice = st.selectbox("Select booking to cancel:", options, key="cancel_select")
        booking_id = int(choice.split("ID: ")[-1].strip(")"))
        
        st.warning("This will free up one active slot.")
        confirm = st.checkbox("Yes, cancel this booking")
        
        if confirm and st.button("Cancel Booking", type="primary"):
            delete_booking(booking_id, villa, sub_community)
            st.success("Booking cancelled!")
            st.rerun()
