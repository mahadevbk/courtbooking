import time
import streamlit as st
from supabase import create_client, Client
from datetime import datetime, timedelta, timezone
import pandas as pd
import zipfile
import io
import random
from postgrest.exceptions import APIError 

# --- DATABASE SETUP (SUPABASE) ---
@st.cache_resource
def init_supabase():
    url: str = st.secrets["SUPABASE_URL"]
    key: str = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase: Client = init_supabase()

# Constants
sub_community_list = ["Mira 1", "Mira 2", "Mira 3", "Mira 4", "Mira 5", "Mira Oasis 1", "Mira Oasis 2", "Mira Oasis 3"]
courts = ["Mira 2", "Mira 4", "Mira 5A", "Mira 5B", "Mira Oasis 1", "Mira Oasis 2", "Mira Oasis 3A", "Mira Oasis 3B", "Mira Oasis 3C"]
start_hours = list(range(7, 22))

# --- HELPER FUNCTIONS ---
def get_utc_plus_4():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=4)

def get_today():
    return get_utc_plus_4().date()

def get_next_14_days():
    today = get_today()
    return [today + timedelta(days=i) for i in range(15)]

def run_query(query_method):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return query_method.execute()
        except Exception as e:
            if attempt == max_retries - 1:
                st.error(f"⚠️ Connection Error: {str(e)}")
                class DummyResponse: data = []; count = 0
                return DummyResponse()
            time.sleep((0.5 * (2 ** attempt)) + random.uniform(0, 0.2))

def get_bookings_for_day_with_details(date_str):
    response = run_query(supabase.table("bookings").select("court, start_hour, sub_community, villa").eq("date", date_str))
    return {(row['court'], row['start_hour']): f"{row['sub_community']} - {row['villa']}" for row in response.data}

def abbreviate_community(full_name):
    if full_name.startswith("Mira Oasis"):
        return f"MO{full_name.split()[-1]}"
    if full_name.startswith("Mira"):
        return f"M{full_name.split()[-1]}"
    return full_name

def color_cell(val):
    if val == "Available": return "background-color: #d4edda; color: #155724; font-weight: bold;"
    if val == "—": return "background-color: #e9ecef; color: #e9ecef;"
    return "background-color: #f8d7da; color: #721c24; font-weight: bold;"

def get_active_bookings_count(villa, sub_community):
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    response = run_query(supabase.table("bookings").select("id", count="exact").eq("villa", villa).eq("sub_community", sub_community).or_(f"date.gt.{today_str},and(date.eq.{today_str},start_hour.gte.{now_hour})"))
    return response.count if response.count is not None else 0

def get_daily_bookings_count(villa, sub_community, date_str):
    response = run_query(supabase.table("bookings").select("id", count="exact").eq("villa", villa).eq("sub_community", sub_community).eq("date", date_str))
    return response.count if response.count is not None else 0

def get_available_hours(court, date_str):
    response = run_query(supabase.table("bookings").select("start_hour").eq("court", court).eq("date", date_str))
    booked_hours = [row['start_hour'] for row in response.data]
    return [h for h in start_hours if h not in booked_hours and not is_slot_in_past(date_str, h)]

def is_slot_in_past(date_str, start_hour):
    now = get_utc_plus_4()
    if date_str < now.strftime('%Y-%m-%d'): return True
    if date_str == now.strftime('%Y-%m-%d') and start_hour <= now.hour: return True
    return False

# --- UI STYLING ---
st.markdown("""
<style>
.stApp { background: linear-gradient(to bottom, #010f1a, #052134); color: white; }
h1, h2, h3 { color: #ffffff !important; }
</style>
""", unsafe_allow_html=True)

# --- SESSION STATE ---
if 'active_tab' not in st.session_state: st.session_state.active_tab = 0
if 'prefill' not in st.session_state: st.session_state.prefill = {}

# --- AUTHENTICATION ---
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    col1, col2 = st.columns(2)
    with col1: sub_comm = st.selectbox("Sub-Community", sub_community_list, index=None)
    with col2: villa_num = st.text_input("Villa Number").strip().upper()
    if st.button("Login"):
        if sub_comm and villa_num:
            st.session_state.sub_community, st.session_state.villa = sub_comm, villa_num
            st.session_state.authenticated = True
            st.rerun()
    st.stop()

# --- APP LAYOUT ---
tabs = st.tabs(["📅 Availability", "➕ Book", "📋 My Bookings", "📜 Activity Log"])

# --- TAB 1: AVAILABILITY ---
with tabs[0]:
    st.subheader("Interactive Schedule")
    date_options = [d.strftime('%Y-%m-%d') for d in get_next_14_days()]
    selected_date = st.selectbox("Select Date:", date_options)
    
    # Build Data
    bookings = get_bookings_for_day_with_details(selected_date)
    time_labels = [f"{h:02d}:00" for h in start_hours]
    
    df_data = []
    for h in start_hours:
        row = {"Time": f"{h:02d}:00"}
        for court in courts:
            key = (court, h)
            if is_slot_in_past(selected_date, h): row[court] = "—"
            elif key in bookings:
                owner = bookings[key]
                row[court] = f"Booked ({abbreviate_community(owner.split(' - ')[0])})"
            else: row[court] = "Available"
        df_data.append(row)
    
    df = pd.DataFrame(df_data).set_index("Time")

    # The Magic Part: Selection Event
    event = st.dataframe(
        df.style.map(color_cell),
        use_container_width=True,
        on_select="rerun",
        selection_mode="single_cell"
    )

    # Process click
    if event.selection.cells:
        row_idx, col_idx = event.selection.cells[0]
        selected_time = time_labels[row_idx]
        selected_court = courts[col_idx]
        cell_value = df.iloc[row_idx, col_idx]

        if cell_value == "Available":
            st.session_state.prefill = {
                "court": selected_court,
                "time": f"{selected_time} - {int(selected_time[:2])+1:02d}:00",
                "date": selected_date
            }
            st.success(f"Selected {selected_court} at {selected_time}. Head to 'Book' tab!")
            # Optional: Automatic switch can be tricky with st.tabs, 
            # so we show a clear message or use a button to jump.
            if st.button(f"Confirm: Book {selected_court} @ {selected_time}"):
                # To switch tabs automatically, you'd need to use the radio-button method from before.
                # With st.tabs, the user just clicks the "Book" tab next.
                pass

# --- TAB 2: BOOK ---
with tabs[1]:
    st.subheader("New Booking")
    
    # Check if we have prefilled data
    pf = st.session_state.prefill
    
    date_choice = st.selectbox("Date:", date_options, index=date_options.index(pf['date']) if pf.get('date') in date_options else 0)
    court_choice = st.selectbox("Court:", courts, index=courts.index(pf['court']) if pf.get('court') in courts else 0)
    
    free_hours = get_available_hours(court_choice, date_choice)
    time_options = [f"{h:02d}:00 - {h+1:02d}:00" for h in free_hours]
    
    # Add the prefilled time even if it's not in 'free_hours' (to ensure it shows up)
    if pf.get('time') and pf['time'] not in time_options:
        time_options.insert(0, pf['time'])
        
    time_choice = st.selectbox("Time Slot:", time_options, index=0 if pf.get('time') else 0)

    if st.button("Confirm Booking", type="primary"):
        # (Same booking logic as before)
        start_h = int(time_choice.split(":")[0])
        # Insert into Supabase...
        supabase.table("bookings").insert({
            "villa": st.session_state.villa,
            "sub_community": st.session_state.sub_community,
            "court": court_choice,
            "date": date_choice,
            "start_hour": start_h
        }).execute()
        st.session_state.prefill = {} # Clear prefill
        st.success("Booked!")
        st.rerun()

# --- OTHER TABS (Simplified for brevity) ---
with tabs[2]: st.write("Your active bookings show here.")
with tabs[3]: st.write("Recent community activity.")
