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
start_hours = list(range(7, 22))  # 7 AM to 9 PM starts

# Helper functions
def get_today():
    return datetime.today().date()

def get_next_14_days():
    today = get_today()
    return [today + timedelta(days=i) for i in range(15)]  # Today + next 14 days

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

# Custom CSS for modern simplistic feel with Google Fonts
st.markdown("""
    <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;700&display=swap" rel="stylesheet">
    <style>
        body {
            font-family: 'Roboto', sans-serif;
            background-color: #f0f4f8;
        }
        .stButton>button {
            background-color: #4CAF50;
            color: white;
            border: none;
            padding: 10px 20px;
            text-align: center;
            text-decoration: none;
            display: inline-block;
            font-size: 16px;
            margin: 4px 2px;
            cursor: pointer;
            border-radius: 4px;
        }
        .stButton>button:hover {
            background-color: #45a049;
        }
        .stTextInput>div>div>input {
            border-radius: 4px;
            border: 1px solid #ccc;
            padding: 10px;
        }
        .stSelectbox>div>div>select {
            border-radius: 4px;
            border: 1px solid #ccc;
            padding: 10px;
        }
    </style>
""", unsafe_allow_html=True)

# App title
st.title("Community Tennis Courts Booking System")

# Session state for villa
if 'villa' not in st.session_state:
    st.session_state.villa = ""

# Enter villa number
villa_input = st.text_input("Enter your Villa Number:", value=st.session_state.villa)
if villa_input:
    st.session_state.villa = villa_input

villa = st.session_state.villa

if not villa:
    st.warning("Please enter your villa number to proceed.")
else:
    # Tabs for different sections
    tab1, tab2, tab3, tab4 = st.tabs(["View Availability", "Book a Slot", "View My Bookings", "Delete a Booking"])

    with tab1:
        st.subheader("View Availability")
        dates = get_next_14_days()
        selected_date = st.selectbox("Select Date:", [d.strftime('%Y-%m-%d') for d in dates])
        
        if selected_date:
            booked_slots = get_bookings_for_day(selected_date)
            time_slots = [f"{h:02d}:00 - {h+1:02d}:00" for h in start_hours]
            data = {time: ["Available" if (court, h) not in booked_slots else "Booked" for court in courts] for h, time in zip(start_hours, time_slots)}
            df = pd.DataFrame(data, index=courts)
            
            def color_status(val):
                color = 'green' if val == "Available" else 'red'
                return f'background-color: {color}; color: white;'
            
            styled_df = df.style.applymap(color_status)
            st.dataframe(styled_df)

    with tab2:
        st.subheader("Book a Slot")
        dates = get_next_14_days()
        date_options = [d.strftime('%Y-%m-%d') for d in dates]
        selected_date = st.selectbox("Select Date:", date_options)
        selected_court = st.selectbox("Select Court:", courts)
        selected_time = st.selectbox("Select Time Slot:", [f"{h:02d}:00 - {h+1:02d}:00" for h in start_hours])
        start_hour = int(selected_time.split(":")[0])
        
        if st.button("Book Slot"):
            date_str = selected_date
            if is_slot_booked(selected_court, date_str, start_hour):
                st.error("This slot is already booked.")
            elif get_active_bookings_count(villa) >= 6:
                st.error("You have reached the limit of 6 active bookings.")
            else:
                book_slot(villa, selected_court, date_str, start_hour)
                st.success("Slot booked successfully!")

    with tab3:
        st.subheader("My Bookings")
        bookings = get_user_bookings(villa)
        if bookings:
            today = get_today()
            today_str = today.strftime('%Y-%m-%d')
            now_hour = datetime.now().hour
            active_bookings = [b for b in bookings if b[2] > today_str or (b[2] == today_str and b[3] > now_hour)]
            past_bookings = [b for b in bookings if b not in active_bookings]
            
            if active_bookings:
                st.write("Active Bookings:")
                for b in active_bookings:
                    st.write(f"ID: {b[0]}, Court: {b[1]}, Date: {b[2]}, Time: {b[3]:02d}:00 - {b[3]+1:02d}:00")
            
            if past_bookings:
                st.write("Past Bookings:")
                for b in past_bookings:
                    st.write(f"ID: {b[0]}, Court: {b[1]}, Date: {b[2]}, Time: {b[3]:02d}:00 - {b[3]+1:02d}:00")
        else:
            st.info("No bookings found.")

    with tab4:
        st.subheader("Delete a Booking")
        bookings = get_user_bookings(villa)
        if bookings:
            booking_options = [f"ID: {b[0]}, Court: {b[1]}, Date: {b[2]}, Time: {b[3]:02d}:00 - {b[3]+1:02d}:00" for b in bookings]
            selected_booking = st.selectbox("Select Booking to Delete:", booking_options)
            if selected_booking:
                booking_id = int(selected_booking.split(",")[0].split(": ")[1])
                confirm = st.checkbox("Confirm: This is my booking and I want to delete it.")
                if confirm and st.button("Delete"):
                    delete_booking(booking_id, villa)
                    st.success("Booking deleted successfully!")
        else:
            st.info("No bookings to delete.")
