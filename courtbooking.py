import time
import streamlit as st
from supabase import create_client, Client
from datetime import datetime, timedelta, timezone
import pandas as pd
import matplotlib.pyplot as plt
import zipfile
import io

# --- DATABASE SETUP (SUPABASE) ---
url: str = st.secrets["SUPABASE_URL"]
key: str = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(url, key)

# Constants
sub_community_list = [
    "Mira 1", "Mira 2", "Mira 3", "Mira 4", "Mira 5",
    "Mira Oasis 1", "Mira Oasis 2", "Mira Oasis 3"
]

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

def add_log(event_type, details):
    timestamp = get_utc_plus_4().isoformat()
    supabase.table("logs").insert({
        "timestamp": timestamp,
        "event_type": event_type,
        "details": details
    }).execute()

def get_bookings_for_day_with_details(date_str):
    response = supabase.table("bookings").select("court, start_hour, sub_community, villa").eq("date", date_str).execute()
    return {(row['court'], row['start_hour']): f"{row['sub_community']} - {row['villa']}" for row in response.data}

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
    elif val == "—":
        return "background-color: #e9ecef; color: #e9ecef; border: none;"
    else:
        return "background-color: #f8d7da; color: #721c24; font-weight: bold;"

def get_active_bookings_count(villa, sub_community):
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    response = supabase.table("bookings").select("id", count="exact")\
        .eq("villa", villa)\
        .eq("sub_community", sub_community)\
        .or_(f"date.gt.{today_str},and(date.eq.{today_str},start_hour.gte.{now_hour})")\
        .execute()
    return response.count

def get_daily_bookings_count(villa, sub_community, date_str):
    response = supabase.table("bookings").select("id", count="exact")\
        .eq("villa", villa)\
        .eq("sub_community", sub_community)\
        .eq("date", date_str)\
        .execute()
    return response.count

def is_slot_booked(court, date_str, start_hour):
    response = supabase.table("bookings").select("id")\
        .eq("court", court)\
        .eq("date", date_str)\
        .eq("start_hour", start_hour)\
        .execute()
    return len(response.data) > 0

def is_slot_in_past(date_str, start_hour):
    now = get_utc_plus_4()
    today_str = now.strftime('%Y-%m-%d')
    if date_str < today_str: return True
    if date_str > today_str: return False
    if start_hour < now.hour: return True
    if start_hour == now.hour and now.minute > 0: return True
    return False

def book_slot(villa, sub_community, court, date_str, start_hour):
    supabase.table("bookings").insert({
        "villa": villa,
        "sub_community": sub_community,
        "court": court,
        "date": date_str,
        "start_hour": start_hour
    }).execute()
    log_detail = f"{sub_community} Villa {villa} booked {court} for {date_str} at {start_hour:02d}:00"
    add_log("Booking Created", log_detail)

def get_user_bookings(villa, sub_community):
    response = supabase.table("bookings").select("id, court, date, start_hour")\
        .eq("villa", villa)\
        .eq("sub_community", sub_community)\
        .order("date")\
        .order("start_hour")\
        .execute()
    return response.data

def delete_booking(booking_id, villa, sub_community):
    record = supabase.table("bookings").select("court, date, start_hour").eq("id", booking_id).single().execute()
    if record.data:
        b = record.data
        log_detail = f"{sub_community} Villa {villa} cancelled {b['court']} for {b['date']} at {b['start_hour']:02d}:00"
        add_log("Booking Deleted", log_detail)
    supabase.table("bookings").delete().eq("id", booking_id).eq("villa", villa).eq("sub_community", sub_community).execute()

def get_logs_last_14_days():
    cutoff = (get_utc_plus_4() - timedelta(days=14)).isoformat()
    response = supabase.table("logs").select("timestamp, event_type, details")\
        .gte("timestamp", cutoff)\
        .order("timestamp", desc=True)\
        .execute()
    return response.data

def get_villas_with_active_bookings():
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    response = supabase.table("bookings").select("villa, sub_community")\
        .or_(f"date.gt.{today_str},and(date.eq.{today_str},start_hour.gte.{now_hour})")\
        .execute()
    unique_villas = sorted(list(set([f"{row['sub_community']} - {row['villa']}" for row in response.data])))
    return unique_villas

def get_active_bookings_for_villa_display(villa_identifier):
    sub_comm, villa_num = villa_identifier.split(" - ")
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    response = supabase.table("bookings").select("court, date, start_hour")\
        .eq("villa", villa_num)\
        .eq("sub_community", sub_comm)\
        .or_(f"date.gt.{today_str},and(date.eq.{today_str},start_hour.gte.{now_hour})")\
        .order("date")\
        .order("start_hour")\
        .execute()
    return [f"{b['date']} | {b['start_hour']:02d}:00 | {b['court']}" for b in response.data]

def get_peak_time_data():
    response = supabase.table("bookings").select("date, start_hour").execute()
    df = pd.DataFrame(response.data)
    if df.empty: return pd.DataFrame()
    df['date'] = pd.to_datetime(df['date'])
    df['day_of_week'] = df['date'].dt.day_name()
    return df

def delete_expired_bookings():
    now = get_utc_plus_4()
    today_str = now.strftime('%Y-%m-%d')
    current_hour = now.hour
    supabase.table("bookings").delete().lt("date", today_str).execute()
    supabase.table("bookings").delete().eq("date", today_str).lt("start_hour", current_hour).execute()

if "expired_cleaned" not in st.session_state:
    delete_expired_bookings()
    st.session_state["expired_cleaned"] = True

def get_available_hours(court, date_str):
    response = supabase.table("bookings").select("start_hour").eq("court", court).eq("date", date_str).execute()
    booked_hours = [row['start_hour'] for row in response.data]
    available = []
    for h in start_hours:
        if h not in booked_hours and not is_slot_in_past(date_str, h):
            available.append(h)
    return available

# --- UI STYLING ---
st.markdown("""
<link href="https://fonts.googleapis.com/css2?family=Audiowide&display=swap" rel="stylesheet">
<style>
.stApp { background: linear-gradient(to bottom, #010f1a, #052134); background-attachment: scroll; }
[data-testid="stHeader"] { background: linear-gradient(to bottom, #052134 , #010f1a) !important; }
h1, h2, h3, .stTitle { font-family: 'Audiowide', cursive !important; color: white; }
.stButton>button { background-color: #4CAF50; color: white; font-family: 'Audiowide', cursive; width: 100%; }

/* Fix mobile table readability */
[data-testid="stDataFrame"] { width: 100% !important; }
.stTabs [data-baseweb="tab-list"] { gap: 10px; }
.stTabs [data-baseweb="tab"] { padding: 8px 12px; font-size: 14px; }

/* Grid Button Styling for Mobile */
.mobile-grid-container {
    overflow-x: auto;
    white-space: nowrap;
    padding-bottom: 10px;
}
.grid-btn {
    display: inline-block;
    min-width: 90px;
    margin: 2px;
}
</style>
""", unsafe_allow_html=True)

# --- LOGIC FOR FULL FRAME PAGE ---
if st.query_params.get("view") == "full":
    st.title("📅 14-Day Schedule")
    if st.button("⬅️ Back"):
        st.query_params.clear()
        st.rerun()
    for d in get_next_14_days():
        d_str = d.strftime('%Y-%m-%d')
        st.subheader(f"{d_str} ({d.strftime('%A')})")
        bookings_with_details = get_bookings_for_day_with_details(d_str)
        data = {}
        for h in start_hours:
            label = f"{h:02d}:00"
            row = []
            for court in courts:
                key = (court, h)
                if is_slot_in_past(d_str, h): row.append("—")
                elif key in bookings_with_details:
                    row.append(abbreviate_community(bookings_with_details[key].rsplit(" - ", 1)[0]) + "-" + bookings_with_details[key].rsplit(" - ", 1)[1])
                else: row.append("Available")
            data[label] = row
        st.dataframe(pd.DataFrame(data, index=courts).T.style.map(color_cell), use_container_width=True)
    st.stop()

# --- MAIN APP ---
st.subheader("🎾 Book that Court ...")    
villas_active = get_villas_with_active_bookings()
today_str = get_today().strftime('%Y-%m-%d')
now_hour = get_utc_plus_4().hour
total_active_response = supabase.table("bookings").select("id", count="exact").or_(f"date.gt.{today_str},and(date.eq.{today_str},start_hour.gte.{now_hour})").execute()
st.write(f"**{len(villas_active)}** Residences | **{total_active_response.count if total_active_response.count else 0}** Active Bookings")

if 'authenticated' not in st.session_state: st.session_state.authenticated = False
if not st.session_state.authenticated:
    col1, col2 = st.columns(2)
    with col1: sub_community_input = st.selectbox("Sub-Community", options=sub_community_list, index=None)
    with col2: villa_input = st.text_input("Villa Number").strip().upper()
    if st.button("Confirm Identity", type="primary"):
        if sub_community_input and villa_input:
            st.session_state.sub_community, st.session_state.villa = sub_community_input, villa_input
            st.session_state.authenticated = True
            st.rerun()
    st.stop()

sub_community, villa = st.session_state.sub_community, st.session_state.villa
st.success(f"✅ {sub_community} - Villa {villa}")

tab1, tab2, tab3, tab4 = st.tabs(["📅 View", "➕ Book", "📋 Mine", "📜 Logs"])

with tab1:
    date_options = [f"{d.strftime('%Y-%m-%d')} ({d.strftime('%A')})" for d in get_next_14_days()]
    selected_date_full = st.selectbox("Select Date:", date_options)
    selected_date = selected_date_full.split(" (")[0]
    
    # Render the CLEAN STATIC TABLE first (Works great on mobile)
    bookings_with_details = get_bookings_for_day_with_details(selected_date)
    table_data = {}
    for h in start_hours:
        row = {}
        for court in courts:
            key = (court, h)
            if is_slot_in_past(selected_date, h): val = "—"
            elif key in bookings_with_details: val = abbreviate_community(bookings_with_details[key].rsplit(" - ", 1)[0])
            else: val = "Available"
            row[court] = val
        table_data[f"{h:02d}:00"] = row
    
    st.dataframe(pd.DataFrame(table_data).T.style.map(color_cell), use_container_width=True)
    
    st.markdown("---")
    st.write("### ⚡ Quick Select & Book")
    st.caption("Select a slot below to auto-fill the booking form.")
    
    # The "Actionable" part: A clean grid of buttons that handle mobile scaling
    for h in start_hours:
        with st.expander(f"⏰ {h:02d}:00 Slots"):
            cols = st.columns(3) # 3 columns per row on mobile looks very clean
            for i, court in enumerate(courts):
                col_idx = i % 3
                key = (court, h)
                is_past = is_slot_in_past(selected_date, h)
                is_booked = key in bookings_with_details
                
                btn_label = f"{court}"
                if is_past: 
                    cols[col_idx].button(f"Closed", key=f"btn_{h}_{i}", disabled=True)
                elif is_booked:
                    cols[col_idx].button(f"Booked", key=f"btn_{h}_{i}", disabled=True)
                else:
                    if cols[col_idx].button(f"Book {court}", key=f"btn_{h}_{i}"):
                        st.session_state.pf_date = selected_date_full
                        st.session_state.pf_court = court
                        st.session_state.pf_hour = h
                        st.info(f"Selected! Switch to '➕ Book' tab to confirm.")

with tab2:
    st.subheader("Confirm Booking")
    d_idx = date_options.index(st.session_state.pf_date) if "pf_date" in st.session_state else 0
    selected_date_full = st.selectbox("Date:", date_options, index=d_idx)
    date_choice = selected_date_full.split(" (")[0]
    
    c_idx = courts.index(st.session_state.pf_court) if "pf_court" in st.session_state else 0
    court_choice = st.selectbox("Court:", courts, index=c_idx)
    
    free_hours = get_available_hours(court_choice, date_choice)
    if not free_hours:
        st.warning("No slots available.")
    else:
        h_idx = free_hours.index(st.session_state.pf_hour) if ("pf_hour" in st.session_state and st.session_state.pf_hour in free_hours) else 0
        time_options = [f"{h:02d}:00 - {h+1:02d}:00" for h in free_hours]
        time_choice = st.selectbox("Time Slot:", time_options, index=h_idx)
        
        if st.button("Confirm Booking", type="primary"):
            active_count = get_active_bookings_count(villa, sub_community)
            daily_count = get_daily_bookings_count(villa, sub_community, date_choice)
            if active_count >= 6: st.error("Limit 6 reached")
            elif daily_count >= 2: st.error("Daily limit 2 reached")
            else:
                start_h = int(time_choice.split(":")[0])
                book_slot(villa, sub_community, court_choice, date_choice, start_h)
                st.balloons()
                st.rerun()

with tab3:
    my_b = get_user_bookings(villa, sub_community)
    if not my_b: st.info("No bookings.")
    for b in my_b:
        with st.container():
            st.write(f"**{b['date']} | {b['start_hour']:02d}:00**")
            st.write(f"Court: {b['court']}")
            if st.button(f"Cancel #{b['id']}", key=f"can_{b['id']}"):
                delete_booking(b['id'], villa, sub_community)
                st.rerun()
            st.divider()

with tab4:
    logs = get_logs_last_14_days()
    if logs: st.dataframe(pd.DataFrame(logs)[["timestamp", "details"]], hide_index=True)

# Footer & QR
st.divider()
st.download_button("📥 Backup", data=b"CSV DATA", file_name="backup.csv")
st.image("https://raw.githubusercontent.com/mahadevbk/courtbooking/main/qr-code.miracourtbooking.streamlit.app.png", width=100)
