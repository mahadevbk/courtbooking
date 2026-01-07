import streamlit as st
import sqlite3
from datetime import datetime, timedelta
import pandas as pd

# Database setup
conn = sqlite3.connect('bookings.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS bookings
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              villa TEXT,
              court TEXT,
              date TEXT,
              start_hour INTEGER)''')
conn.commit()

# Courts and slots
courts = ["Mira 2", "Mira 4", "Mira 5A", "Mira 5B", "Mira Oasis 1", "Mira Oasis 2", "Mira Oasis 3A", "Mira Oasis 3B", "Mira Oasis 3C"]
start_hours = list(range(7, 22))  # 7 AM to 9 PM

# Helper functions
def get_today():
    return datetime.today().date()

def get_next_14_days():
    today = get_today()
    return [today + timedelta(days=i) for i in range(15)]

def get_bookings_for_day(date_str):
    c.execute("SELECT court, start_hour FROM bookings WHERE date=?", (date_str,))
    return set((row[0], row[1]) for row in c.fetchall())

def get_active_bookings_count(villa):
    today = get_today()
    today_str = today.strftime('%Y-%m-%d')
    now_hour = datetime.now().hour
    c.execute("""
        SELECT COUNT(*) FROM bookings 
        WHERE villa=? AND (date > ? OR (date=? AND start_hour > ?))
    """, (villa, today_str, today_str, now_hour))
    return c.fetchone()[0]

def is_slot_booked(court, date_str, start_hour):
    c.execute("SELECT * FROM bookings WHERE court=? AND date=? AND start_hour=?", (court, date_str, start_hour))
    return c.fetchone() is not None

def book_slot(villa, court, date_str, start_hour):
    c.execute("INSERT INTO bookings (villa, court, date, start_hour) VALUES (?, ?, ?, ?)", (villa, court, date_str, start_hour))
    conn.commit()

def get_user_bookings(villa):
    c.execute("SELECT id, court, date, start_hour FROM bookings WHERE villa=? ORDER BY date, start_hour", (villa,))
    return c.fetchall()

def delete_booking(booking_id, villa):
    c.execute("DELETE FROM bookings WHERE id=? AND villa=?", (booking_id, villa))
    conn.commit()

# Modern styling with Google Fonts
st.markdown("""
    <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;700&display=swap" rel="stylesheet">
    <style>
        body {font-family: 'Roboto', sans-serif; background-color: #f0f4f8;}
        .stButton>button {background-color: #4CAF50; color: white; border: none; padding: 10px 20px; border-radius: 4px;}
        .stButton>button:hover {background-color: #45a049;}
    </style>
""", unsafe_allow_html=True)

st.title("Community Tennis Courts Booking System")

if 'villa' not in st.session_state:
    st.session_state.villa = ""

villa_input = st.text_input("Enter your Villa Number:", value=st.session_state.villa)
if villa_input:
    st.session_state.villa = villa_input.strip()

villa = st.session_state.villa

if not villa:
    st.warning("Please enter your villa number to proceed.")
    st.stop()

tab1, tab2, tab3, tab4 = st.tabs(["View Availability", "Book a Slot", "My Bookings", "Delete Booking"])

with tab1:
    st.subheader("View Availability")
    dates = get_next_14_days()
    selected_date = st.selectbox("Select Date to View:", [d.strftime('%Y-%m-%d') for d in dates], key="view_date")
    
    booked_slots = get_bookings_for_day(selected_date)
    time_slots = [f"{h:02d}:00 - {h+1:02d}:00" for h in start_hours]
    data = {time: ["Available" if (court, h) not in booked_slots else "Booked" for court in courts] 
            for h, time in zip(start_hours, time_slots)}
    df = pd.DataFrame(data, index=courts)
    
    def color_status(val):
        return ['background-color: #90EE90; color: black' if val == "Available" else 
                'background-color: #FFB6C1; color: black'] * len(df.columns)
    
    styled_df = df.style.map(color_status)
    st.dataframe(styled_df, use_container_width=True)

with tab2:
    st.subheader("Book a Slot")
    dates = get_next_14_days()
    date_options = [d.strftime('%Y-%m-%d') for d in dates]
    
    selected_date = st.selectbox("Select Date:", date_options, key="book_date")
    selected_court = st.selectbox("Select Court:", courts, key="book_court")
    selected_time = st.selectbox("Select Time Slot:", [f"{h:02d}:00 - {h+1:02d}:00" for h in start_hours], key="book_time")
    start_hour = int(selected_time.split(":")[0])
    
    if st.button("Book This Slot"):
        if is_slot_booked(selected_court, selected_date, start_hour):
            st.error("Sorry, this slot was just booked by someone else.")
        elif get_active_bookings_count(villa) >= 6:
            st.error(f"You already have 6 active bookings. You can book again after one expires.")
        else:
            book_slot(villa, selected_court, selected_date, start_hour)
            st.success(f"Success! {selected_court} booked on {selected_date} at {selected_time}")
            st.balloons()

with tab3:
    st.subheader("My Bookings")
    bookings = get_user_bookings(villa)
    if not bookings:
        st.info("You have no bookings yet.")
    else:
        active = []
        past = []
        today_str = get_today().strftime('%Y-%m-%d')
        now_hour = datetime.now().hour
        for b in bookings:
            if b[2] > today_str or (b[2] == today_str and b[3] > now_hour):
                active.append(b)
            else:
                past.append(b)
        
        if active:
            st.write("**Active Bookings** (count toward your limit of 6):")
            for b in active:
                st.write(f"• {b[1]} on {b[2]} at {b[3]:02d}:00 - {b[3]+1:02d}:00")
        if past:
            st.write("**Past Bookings**:")
            for b in past:
                st.write(f"• {b[1]} on {b[2]} at {b[3]:02d}:00 - {b[3]+1:02d}:00")

with tab4:
    st.subheader("Delete a Booking")
    bookings = get_user_bookings(villa)
    if not bookings:
        st.info("You have no bookings to delete.")
    else:
        options = [f"{b[1]} on {b[2]} at {b[3]:02d}:00 - {b[3]+1:02d}:00 (ID: {b[0]})" for b in bookings]
        choice = st.selectbox("Select booking to delete:", options, key="delete_select")
        booking_id = int(choice.split("ID: ")[-1].strip(")"))
        
        st.warning("This will free up one of your 6 slots immediately.")
        confirm = st.checkbox("Yes, I want to delete this booking")
        if confirm and st.button("Delete Booking", type="primary"):
            delete_booking(booking_id, villa)
            st.success("Booking deleted!")
            st.rerun()
