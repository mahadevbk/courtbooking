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

def color_cell(val):
    if val == "Available":
        return "background-color: #d4edda; color: #155724; font-weight: bold;"
    else:
        return "background-color: #f8d7da; color: #721c24; font-weight: bold;"

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
    today_str = get_today().strftime('%Y-%m-%d')
    now = datetime.now()
    current_hour = now.hour
    current_minute = now.minute
    if date_str < today_str: return True
    if date_str > today_str: return False
    if start_hour < current_hour: return True
    if start_hour == current_hour and current_minute > 0: return True
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

# Styling
st.markdown("""
<link href="https://fonts.googleapis.com/css2?family=Audiowide&display=swap" rel="stylesheet">
<style>
    h1, h2, h3, .stTitle { font-family: 'Audiowide', cursive !important; color: #2c3e50; }
    .stButton>button { background-color: #4CAF50; color: white; font-family: 'Audiowide', cursive; }
    .stDataFrame th { font-family: 'Audiowide', cursive; font-size: 12px; background-color: #2c3e50 !important; color: white !important; }
</style>
""", unsafe_allow_html=True)

# === LOGIC FOR FULL FRAME PAGE ===
if st.query_params.get("view") == "full":
    st.title("📅 14-Day Full Court Schedule")
    if st.button("⬅️ Back to Booking App"):
        st.query_params.clear()
        st.rerun()

    dates = get_next_14_days()
    for d in dates:
        d_str = d.strftime('%Y-%m-%d')
        st.subheader(f"{d_str} ({d.strftime('%A')})")
        
        bookings_with_details = get_bookings_for_day_with_details(d_str)
        time_labels = [f"{h:02d}:00 - {h+1:02d}:00" for h in start_hours]
        
        data = {}
        for h, label in zip(start_hours, time_labels):
            row = []
            for court in courts:
                key = (court, h)
                if key in bookings_with_details:
                    full_comm, villa_num = bookings_with_details[key].rsplit(" - ", 1)
                    row.append(f"{abbreviate_community(full_comm)}-{villa_num}")
                else:
                    row.append("Available")
            data[label] = row
        
        day_df = pd.DataFrame(data, index=courts)
        st.dataframe(day_df.style.map(color_cell), width="stretch")
        st.divider()
    
    st.stop()

# === MAIN BOOKING APP LOGIC ===
st.title("🎾 Book that Court ...")

if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.subheader("Please log in ")
    col1, col2 = st.columns(2)
    with col1:
        sub_community_input = st.selectbox("Select Your Sub-Community", options=sub_community_list, index=None)
    with col2:
        villa_input = st.text_input("Enter Villa Number").strip().upper()

    if st.button("Confirm Identity", type="primary"):
        if sub_community_input and villa_input:
            st.session_state.sub_community = sub_community_input
            st.session_state.villa = villa_input
            st.session_state.authenticated = True
            st.rerun()
    st.stop()

sub_community = st.session_state.sub_community
villa = st.session_state.villa
st.success(f"✅ Logged in as: **{sub_community} - Villa {villa}**")

tab1, tab2, tab3, tab4 = st.tabs(["📅 View Availability", "➕ Book a Slot", "📋 My Bookings", "❌ Cancel Booking"])

with tab1:
    st.subheader("Court Availability")
    dates = get_next_14_days()
    date_options = [f"{d.strftime('%Y-%m-%d')} ({d.strftime('%A')})" for d in dates]
    selected_date_str = st.selectbox("Select Date:", date_options)
    selected_date = selected_date_str.split(" (")[0]

    bookings_with_details = get_bookings_for_day_with_details(selected_date)
    data = {}
    for h in start_hours:
        label = f"{h:02d}:00 - {h+1:02d}:00"
        row = []
        for court in courts:
            key = (court, h)
            if key in bookings_with_details:
                full_comm, villa_num = bookings_with_details[key].rsplit(" - ", 1)
                row.append(f"{abbreviate_community(full_comm)}-{villa_num}")
            else:
                row.append("Available")
        data[label] = row

    df = pd.DataFrame(data, index=courts)
    st.dataframe(df.style.map(color_cell), width="stretch")

    # Link to Full Frame Page
    st.link_button("🌐 View Full 14-Day Schedule (Full Page)", url="/?view=full", width="stretch")

    st.markdown("---")
    st.subheader("🔍 View Active Bookings :")
    villas_with_active = get_villas_with_active_bookings()
    if villas_with_active:
        selected_villa = st.selectbox("Select a villa:", options=["-- Select --"] + villas_with_active)
        if selected_villa != "-- Select --":
            st.selectbox("Active bookings:", options=get_active_bookings_for_villa_display(selected_villa))

with tab2:
    st.subheader("Book a New Slot")
    dates = get_next_14_days()
    date_options = [d.strftime('%Y-%m-%d') for d in dates]
    selected_date = st.selectbox("Date:", date_options)
    selected_court = st.selectbox("Court:", courts)
    selected_time_label = st.selectbox("Time Slot:", [f"{h:02d}:00 - {h+1:02d}:00" for h in start_hours])
    start_hour = int(selected_time_label.split(":")[0])

    active_count = get_active_bookings_count(villa, sub_community)
    st.info(f"You have **{active_count} / 6** active bookings.")

    if st.button("Book This Slot", type="primary"):
        if is_slot_in_past(selected_date, start_hour):
            st.error("⛔ This slot has passed.")
        elif is_slot_booked(selected_court, selected_date, start_hour):
            st.error("❌ Slot taken.")
        elif active_count >= 6:
            st.error("🚫 Limit reached.")
        else:
            book_slot(villa, sub_community, selected_court, selected_date, start_hour)
            st.success("✅ Booked!")
            st.balloons()
            st.rerun()

with tab3:
    st.subheader("My Bookings")
    bookings = get_user_bookings(villa, sub_community)
    if not bookings:
        st.info("No bookings yet.")
    else:
        for b in bookings:
            st.write(f"• **{b[1]}** – {b[2]} at {b[3]:02d}:00")

with tab4:
    st.subheader("Cancel a Booking")
    bookings = get_user_bookings(villa, sub_community)
    if bookings:
        options = [f"{b[1]} on {b[2]} at {b[3]:02d}:00 (ID: {b[0]})" for b in bookings]
        choice = st.selectbox("Select to cancel:", options)
        booking_id = int(choice.split("ID: ")[-1].strip(")"))
        if st.checkbox("Confirm cancel") and st.button("Cancel Booking", type="primary"):
            delete_booking(booking_id, villa, sub_community)
            st.success("Cancelled!")
            st.rerun()

st.markdown("""
<div style='background-color: #0d5384; padding: 1rem; border-left: 5px solid #fff500; border-radius: 0.5rem; color: white;'>
Built with ❤️ using <a href='https://streamlit.io/' style='color: #ccff00;'>Streamlit</a>
</div>
""", unsafe_allow_html=True)
