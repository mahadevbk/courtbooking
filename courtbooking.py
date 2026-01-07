import streamlit as st
import sqlite3
from datetime import datetime, timedelta, date
import pandas as pd

# Database setup with schema migration
conn = sqlite3.connect('bookings.db', check_same_thread=False)
c = conn.cursor()

# Create table if not exists
c.execute('''CREATE TABLE IF NOT EXISTS bookings
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              villa TEXT,
              court TEXT,
              date TEXT,
              start_hour INTEGER)''')

# Add sub_community column if missing
c.execute("PRAGMA table_info(bookings)")
columns = [col[1] for col in c.fetchall()]
if 'sub_community' not in columns:
    c.execute("ALTER TABLE bookings ADD COLUMN sub_community TEXT")
    conn.commit()

conn.commit()

# Sub-communities list
sub_community_list = [
    "Mira 1", "Mira 2", "Mira 3", "Mira 4", "Mira 5",
    "Mira Oasis 1", "Mira Oasis 2", "Mira Oasis 3"
]

# All courts
courts = ["Mira 2", "Mira 4", "Mira 5A", "Mira 5B", "Mira Oasis 1", "Mira Oasis 2", "Mira Oasis 3A", "Mira Oasis 3B", "Mira Oasis 3C"]
start_hours = list(range(7, 22))

# Helper functions
def get_today():
    return date.today()

def get_next_14_days():
    today = get_today()
    return [today + timedelta(days=i) for i in range(15)]

def get_bookings_for_day_with_details(date_str):
    c.execute("SELECT court, start_hour, sub_community, villa FROM bookings WHERE date=?", (date_str,))
    return {(row[0], row[1]): f"{row[2]} - {row[3]}" for row in c.fetchall()}

def abbreviate_community(full_name):
    if full_name.startswith("Mira Oasis"):
        num = full_name.split()[-1]
        return f"MO{num}"
    elif full_name.startswith("Mira"):
        num = full_name.split()[-1]
        return f"M{num}"
    return full_name

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

def is_slot_in_past(date_str, start_hour):
    """Check if the selected slot is in the past or currently active (started but not finished)"""
    today_str = get_today().strftime('%Y-%m-%d')
    now = datetime.now()
    current_hour = now.hour
    current_minute = now.minute
    
    if date_str < today_str:
        return True  # Future dates only allowed
    if date_str > today_str:
        return False  # Future dates are fine
    
    # Same day: check if slot has started
    if start_hour < current_hour:
        return True  # Already fully passed
    if start_hour == current_hour and current_minute > 0:
        return True  # Slot has already started (e.g., 18:15 trying to book 18:00)
    return False

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

# New: Get villas with active bookings
def get_villas_with_active_bookings():
    today = get_today()
    today_str = today.strftime('%Y-%m-%d')
    now_hour = datetime.now().hour
    c.execute("""
        SELECT DISTINCT villa, sub_community FROM bookings 
        WHERE date > ? OR (date = ? AND start_hour >= ?)
        ORDER BY villa
    """, (today_str, today_str, now_hour))
    return [f"{row[1]} - {row[0]}" for row in c.fetchall()]

def get_active_bookings_for_villa_display(villa_identifier):
    sub_comm, villa_num = villa_identifier.split(" - ")
    today = get_today()
    today_str = today.strftime('%Y-%m-%d')
    now_hour = datetime.now().hour
    c.execute("""
        SELECT court, date, start_hour FROM bookings 
        WHERE villa=? AND sub_community=? AND (date > ? OR (date = ? AND start_hour >= ?))
        ORDER BY date, start_hour
    """, (villa_num, sub_comm, today_str, today_str, now_hour))
    bookings = c.fetchall()
    options = []
    for court, bdate, hour in bookings:
        dt = datetime.strptime(bdate, '%Y-%m-%d')
        day_name = dt.strftime('%A')
        time_str = f"{hour:02d}:00 - {hour+1:02d}:00"
        options.append(f"{bdate} ({day_name}) | {time_str} | {court}")
    return options


# Styling - Now using Audiowide
st.markdown("""
<link href="https://fonts.googleapis.com/css2?family=Audiowide&display=swap" rel="stylesheet">
<style>
    body {
        font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
        background-color: #f5f7fa;
    }
    h1, h2, h3, .stTitle {
        font-family: 'Audiowide', cursive !important;
        color: #2c3e50;
        letter-spacing: 1px;
    }
    .stButton>button {
        background-color: #4CAF50;
        color: white;
        border: none;
        padding: 12px 24px;
        border-radius: 8px;
        font-size: 16px;
        font-family: 'Audiowide', cursive;
        letter-spacing: 1px;
    }
    .stButton>button:hover {
        background-color: #45a049;
    }
    /* Make table headers and important text stand out */
    .stDataFrame th {
        font-family: 'Audiowide', cursive;
        font-size: 12px;
        background-color: #2c3e50 !important;
        color: white !important;
    }
    /* Optional: Make booked/available text bolder */
    .stDataFrame td {
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

st.title("🎾 Grab that court... ")



# Session state
if 'sub_community' not in st.session_state:
    st.session_state.sub_community = None
if 'villa' not in st.session_state:
    st.session_state.villa = ""
if 'just_booked' not in st.session_state:
    st.session_state.just_booked = False

# User inputs
col1, col2 = st.columns(2)
with col1:
    sub_community = st.selectbox(
        "Select Your Sub-Community",
        options=sub_community_list,
        index=None,
        placeholder="Choose sub-community",
        key="sub_community_input"
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

# Tabs
tab1, tab2, tab3, tab4 = st.tabs(["📅 View Availability", "➕ Book a Slot", "📋 My Bookings", "❌ Cancel Booking"])

# === TAB 1: View Availability ===
with tab1:
    st.subheader("Court Availability")
    dates = get_next_14_days()
    
    date_options = []
    for d in dates:
        day_name = d.strftime('%A')
        date_options.append(f"{d.strftime('%Y-%m-%d')} ({day_name})")
    
    selected_date_str = st.selectbox("Select Date:", date_options, key="view_date_select")
    selected_date = selected_date_str.split(" (")[0]

    bookings_with_details = get_bookings_for_day_with_details(selected_date)
    time_labels = [f"{h:02d}:00 - {h+1:02d}:00" for h in start_hours]

    data = {}
    for h, label in zip(start_hours, time_labels):
        row = []
        for court in courts:
            key = (court, h)
            if key in bookings_with_details:
                full_comm, villa_num = bookings_with_details[key].rsplit(" - ", 1)
                abbr = abbreviate_community(full_comm)
                row.append(f"{abbr}-{villa_num}")
            else:
                row.append("Available")
        data[label] = row

    df = pd.DataFrame(data, index=courts)

    def color_cell(val):
        if val == "Available":
            return "background-color: #d4edda; color: #155724; font-weight: bold;"
        else:
            return "background-color: #f8d7da; color: #721c24; font-weight: bold;"

    styled_df = df.style.map(color_cell)
    st.dataframe(styled_df, width="stretch")

    st.markdown("---")
    st.subheader("🔍 View Active Bookings :")

    villas_with_active = get_villas_with_active_bookings()
    if villas_with_active:
        selected_villa = st.selectbox(
            "Select a villa to view their active bookings:",
            options=["-- Select a villa --"] + villas_with_active,
            key="villa_lookup"
        )
        if selected_villa != "-- Select a villa --":
            booking_list = get_active_bookings_for_villa_display(selected_villa)
            if booking_list:
                st.selectbox("Active bookings:", options=booking_list, key="villa_booking_list")
            else:
                st.info("No active bookings found.")
    else:
        st.info("No active bookings in the community right now.")

# === TAB 2: Book a Slot ===
with tab2:
    st.subheader("Book a New Slot")
    dates = get_next_14_days()
    date_options = [d.strftime('%Y-%m-%d') for d in dates]

    selected_date = st.selectbox("Date:", date_options, key="book_date")
    selected_court = st.selectbox("Court:", courts, key="book_court")
    selected_time_label = st.selectbox("Time Slot:", [f"{h:02d}:00 - {h+1:02d}:00" for h in start_hours], key="book_time")
    start_hour = int(selected_time_label.split(":")[0])

    active_count = get_active_bookings_count(villa, sub_community)
    st.info(f"You have **{active_count} / 6** active bookings.")

    if st.button("Book This Slot", type="primary"):
        # NEW: Prevent booking past or active slots
        if is_slot_in_past(selected_date, start_hour):
            st.error("⛔ This slot has already started or passed. You cannot book it.")
        elif is_slot_booked(selected_court, selected_date, start_hour):
            st.error("❌ This slot was just taken by someone else.")
        elif active_count >= 6:
            st.error("🚫 You already have 6 active bookings. Cancel one first.")
        else:
            book_slot(villa, sub_community, selected_court, selected_date, start_hour)
            st.success(f"✅ Booked **{selected_court}** on **{selected_date}** at **{selected_time_label}**!")
            st.session_state.just_booked = True
            st.rerun()

    if st.session_state.just_booked:
        st.balloons()
        st.session_state.just_booked = False

# === TAB 3 & 4 unchanged ===
with tab3:
    st.subheader("My Bookings")
    bookings = get_user_bookings(villa, sub_community)
    
    if not bookings:
        st.info("No bookings yet.")
    else:
        today_str = get_today().strftime('%Y-%m-%d')
        now_hour = datetime.now().hour
        
        active = [b for b in bookings if b[2] > today_str or (b[2] == today_str and b[3] >= now_hour)]
        past = [b for b in bookings if b not in active]
        
        if active:
            st.write("**Active Bookings** (count toward limit):")
            for b in active:
                st.write(f"• **{b[1]}** – {b[2]} at {b[3]:02d}:00 - {b[3]+1:02d}:00")
        
        if past:
            st.write("**Past Bookings**:")
            for b in past:
                st.write(f"• {b[1]} – {b[2]} at {b[3]:02d}:00 - {b[3]+1:02d}:00")

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



st.markdown("""
<div style='background-color: #0d5384; padding: 1rem; border-left: 5px solid #fff500; border-radius: 0.5rem; color: white;'>
Built with ❤️ using <a href='https://streamlit.io/' style='color: #ccff00;'>Streamlit</a> — free and open source.
<a href='https://devs-scripts.streamlit.app/' style='color: #ccff00;'>Other Scripts by dev</a> on Streamlit.
</div>
""", unsafe_allow_html=True)
