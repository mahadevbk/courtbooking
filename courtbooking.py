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

def run_query(query_method):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return query_method.execute()
        except Exception as e:
            if attempt == max_retries - 1:
                st.error(f"⚠️ Connection Error: {str(e)}")
                class DummyResponse:
                    data = []
                    count = 0
                return DummyResponse()
            time.sleep((0.5 * (2 ** attempt)) + random.uniform(0, 0.2))

def add_log(event_type, details):
    timestamp = get_utc_plus_4().isoformat()
    try:
        run_query(supabase.table("logs").insert({"timestamp": timestamp, "event_type": event_type, "details": details}))
    except:
        pass

def get_bookings_for_day_with_details(date_str):
    response = run_query(supabase.table("bookings").select("court, start_hour, sub_community, villa").eq("date", date_str))
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
    response = run_query(
        supabase.table("bookings").select("id", count="exact")\
        .eq("villa", villa)\
        .eq("sub_community", sub_community)\
        .or_(f"date.gt.{today_str},and(date.eq.{today_str},start_hour.gte.{now_hour})")
    )
    return response.count if response.count is not None else 0

def get_daily_bookings_count(villa, sub_community, date_str):
    response = run_query(
        supabase.table("bookings").select("id", count="exact")\
        .eq("villa", villa)\
        .eq("sub_community", sub_community)\
        .eq("date", date_str)
    )
    return response.count if response.count is not None else 0

def is_slot_booked(court, date_str, start_hour):
    response = run_query(
        supabase.table("bookings").select("id")\
        .eq("court", court)\
        .eq("date", date_str)\
        .eq("start_hour", start_hour)
    )
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
    run_query(supabase.table("bookings").insert({
        "villa": villa,
        "sub_community": sub_community,
        "court": court,
        "date": date_str,
        "start_hour": start_hour
    }))
    log_detail = f"{sub_community} Villa {villa} booked {court} for {date_str} at {start_hour:02d}:00"
    add_log("Booking Created", log_detail)

def get_user_bookings(villa, sub_community):
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    response = run_query(
        supabase.table("bookings").select("id, court, date, start_hour")\
        .eq("villa", villa)\
        .eq("sub_community", sub_community)\
        .or_(f"date.gt.{today_str},and(date.eq.{today_str},start_hour.gte.{now_hour})")\
        .order("date")\
        .order("start_hour")
    )
    return response.data

def delete_booking(booking_id, villa, sub_community):
    record = run_query(supabase.table("bookings").select("court, date, start_hour").eq("id", booking_id).single())
    if record.data:
        b = record.data
        log_detail = f"{sub_community} Villa {villa} cancelled {b['court']} for {b['date']} at {b['start_hour']:02d}:00"
        add_log("Booking Deleted", log_detail)
    run_query(supabase.table("bookings").delete().eq("id", booking_id).eq("villa", villa).eq("sub_community", sub_community))

def get_logs_last_14_days():
    cutoff = (get_utc_plus_4() - timedelta(days=14)).isoformat()
    response = run_query(supabase.table("logs").select("timestamp, event_type, details").gte("timestamp", cutoff).order("timestamp", desc=True))
    return response.data

def get_villas_with_active_bookings():
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    response = run_query(supabase.table("bookings").select("villa, sub_community").or_(f"date.gt.{today_str},and(date.eq.{today_str},start_hour.gte.{now_hour})"))
    unique_villas = sorted(list(set([f"{row['sub_community']} - {row['villa']}" for row in response.data])))
    return unique_villas

def get_active_bookings_for_villa_display(villa_identifier):
    try:
        sub_comm, villa_num = villa_identifier.split(" - ")
        today_str = get_today().strftime('%Y-%m-%d')
        now_hour = get_utc_plus_4().hour
        response = run_query(
            supabase.table("bookings").select("court, date, start_hour")\
            .eq("villa", villa_num)\
            .eq("sub_community", sub_comm)\
            .or_(f"date.gt.{today_str},and(date.eq.{today_str},start_hour.gte.{now_hour})")\
            .order("date")\
            .order("start_hour")
        )
        return [f"{b['date']} | {b['start_hour']:02d}:00 | {b['court']}" for b in response.data]
    except Exception:
        return []

def get_peak_time_data():
    response = run_query(supabase.table("bookings").select("date, start_hour"))
    df = pd.DataFrame(response.data)
    if df.empty: return pd.DataFrame()
    df['date'] = pd.to_datetime(df['date'])
    df['day_of_week'] = df['date'].dt.day_name()
    return df

def get_available_hours(court, date_str):
    response = run_query(supabase.table("bookings").select("start_hour").eq("court", court).eq("date", date_str))
    booked_hours = [row['start_hour'] for row in response.data]
    available = [h for h in start_hours if h not in booked_hours and not is_slot_in_past(date_str, h)]
    return available

# --- UI STYLING ---
st.markdown("""
<link href="https://fonts.googleapis.com/css2?family=Audiowide&display=swap" rel="stylesheet">
<style>
.stApp { background: linear-gradient(to bottom, #010f1a, #052134); background-attachment: scroll; }
[data-testid="stHeader"] { background: linear-gradient(to bottom, #052134 , #010f1a) !important; }
h1, h2, h3, .stTitle { font-family: 'Audiowide', cursive !important; color: #2c3e50; }
.stButton>button { background-color: #4CAF50; color: white; font-family: 'Audiowide', cursive; }
.stDataFrame th { font-family: 'Audiowide', cursive; font-size: 12px; background-color: #2c3e50 !important; color: white !important; }
</style>
""", unsafe_allow_html=True)

# --- SESSION STATE INITIALIZATION ---
if 'active_tab' not in st.session_state:
    st.session_state.active_tab = "📅 Availability"
if 'prefill_court' not in st.session_state:
    st.session_state.prefill_court = None
if 'prefill_time' not in st.session_state:
    st.session_state.prefill_time = None

# --- FULL FRAME PAGE ---
if st.query_params.get("view") == "full":
    st.title("📅 Full 14-Day Schedule")
    if st.button("⬅️ Back to Booking App"):
        st.query_params.clear()
        st.rerun()

    for d in get_next_14_days():
        d_str = d.strftime('%Y-%m-%d')
        st.subheader(f"{d_str} ({d.strftime('%A')})")
        bookings_with_details = get_bookings_for_day_with_details(d_str)
        data = {}
        for h in start_hours:
            label = f"{h:02d}:00 - {h+1:02d}:00"
            row = []
            for court in courts:
                key = (court, h)
                if is_slot_in_past(d_str, h): row.append("—")
                elif key in bookings_with_details:
                    full_comm, villa_num = bookings_with_details[key].rsplit(" - ", 1)
                    row.append(f"{abbreviate_community(full_comm)}-{villa_num}")
                else: row.append("Available")
            data[label] = row
        st.dataframe(pd.DataFrame(data, index=courts).style.map(color_cell), width="stretch")
        st.divider()
    st.stop()

# --- MAIN APP ---
st.subheader("🎾 Book that Court ...")    
st.caption("An Un-Official & Community Driven Booking Solution.")

try:
    villas_active = get_villas_with_active_bookings()
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    total_active_response = run_query(supabase.table("bookings").select("id", count="exact").or_(f"date.gt.{today_str},and(date.eq.{today_str},start_hour.gte.{now_hour})"))
    total_residences = len(villas_active)
    total_bookings = total_active_response.count if total_active_response.count is not None else 0
    st.write(f"**{total_residences}** Residences have **{total_bookings}** active bookings.")
except Exception:
    st.write("Unable to load live stats (Network refreshing...)")
    villas_active = []

if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    col1, col2 = st.columns(2)
    with col1: sub_community_input = st.selectbox("Select Your Sub-Community", options=sub_community_list, index=None)
    with col2: villa_input = st.text_input("Enter Villa Number").strip().upper()

    if st.button("Confirm Identity", type="primary"):
        if sub_community_input and villa_input:
            st.session_state.sub_community, st.session_state.villa = sub_community_input, villa_input
            st.session_state.authenticated = True
            st.rerun()
    st.stop()

sub_community, villa = st.session_state.sub_community, st.session_state.villa
st.success(f"✅ Logged in as: **{sub_community} - Villa {villa}**")

# --- NAVIGATION VIA SESSION STATE ---
tabs_list = ["📅 Availability", "➕ Book", "📋 My Bookings", "📜 Activity Log"]
st.session_state.active_tab = st.radio("Navigation", options=tabs_list, horizontal=True, label_visibility="collapsed", index=tabs_list.index(st.session_state.active_tab))

# --- TAB 1: AVAILABILITY ---
if st.session_state.active_tab == "📅 Availability":
    st.subheader("Court Availability")
    date_options = [f"{d.strftime('%Y-%m-%d')} ({d.strftime('%A')})" for d in get_next_14_days()]
    selected_date_full = st.selectbox("Select Date:", date_options)
    selected_date = selected_date_full.split(" (")[0]

    bookings_with_details = get_bookings_for_day_with_details(selected_date)
    
    # 1. Static Overview Dataframe
    data = {}
    for h in start_hours:
        label = f"{h:02d}:00 - {h+1:02d}:00"
        row = []
        for court in courts:
            key = (court, h)
            if is_slot_in_past(selected_date, h): row.append("—")
            elif key in bookings_with_details:
                full_comm, villa_num = bookings_with_details[key].rsplit(" - ", 1)
                row.append(f"{abbreviate_community(full_comm)}-{villa_num}")
            else: row.append("Available")
        data[label] = row
    st.dataframe(pd.DataFrame(data, index=courts).style.map(color_cell), width="stretch")
    st.link_button("🌐 View Full 14-Day Schedule (Full Page)", url="/?view=full")

    # 2. Interactive Click-to-Book Grid
    st.divider()
    st.markdown("### ⚡ Click a slot to start booking")
    grid_cols = st.columns(len(courts))
    for i, court in enumerate(courts):
        with grid_cols[i]:
            st.markdown(f"**{court}**")
            for h in start_hours:
                key = (court, h)
                is_past = is_slot_in_past(selected_date, h)
                is_booked = key in bookings_with_details
                btn_key = f"grid_{court}_{selected_date}_{h}"
                
                if is_past:
                    st.button(f"{h:02d}:00", key=btn_key, disabled=True)
                elif is_booked:
                    st.button(f"🚫 {h:02d}", key=btn_key, disabled=True, help=f"Booked by {bookings_with_details[key]}")
                else:
                    if st.button(f"✅ {h:02d}", key=btn_key):
                        st.session_state.prefill_court = court
                        st.session_state.prefill_time = f"{h:02d}:00 - {h+1:02d}:00"
                        st.session_state.active_tab = "➕ Book"
                        st.rerun()

    # Community Insights...
    st.divider()
    st.subheader("📊 Community Usage Insights")
    usage_data = get_peak_time_data()
    if not usage_data.empty:
        col_charts1, col_charts2 = st.columns([1, 1])
        with col_charts1:
            st.write("**🔥 Busiest Hours**")
            hour_counts = usage_data['start_hour'].value_counts().sort_index()
            chart_df = pd.DataFrame({"Bookings": hour_counts.values}, index=[f"{h:02d}:00" for h in hour_counts.index])
            st.bar_chart(chart_df, color="#4CAF50")
        with col_charts2:
            st.write("**📅 Busiest Days**")
            day_counts = usage_data['day_of_week'].value_counts()
            days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            day_counts = day_counts.reindex(days_order).fillna(0)
            st.area_chart(day_counts, color="#0d5384")
    
    st.divider()
    st.subheader("🔍 Booking Lookup")
    if villas_active:
        look_villa = st.selectbox("Select Villa to see details:", options=["-- Select --"] + villas_active)
        if look_villa != "-- Select --":
            active_list = get_active_bookings_for_villa_display(look_villa)
            if active_list: st.selectbox("Active bookings for this villa:", options=active_list)
            else: st.write("No active bookings found for this villa.")

# --- TAB 2: BOOK ---
elif st.session_state.active_tab == "➕ Book":
    st.subheader("Book a New Slot")
    st.info("App allows 6 Active bookings spanning 14 days, A maximum of 2 active bookings per day.")
    
    date_options = [f"{d.strftime('%Y-%m-%d')} ({d.strftime('%A')})" for d in get_next_14_days()]
    selected_date_full = st.selectbox("Date:", date_options)
    date_choice = selected_date_full.split(" (")[0]
    
    # Apply Prefills
    court_idx = courts.index(st.session_state.prefill_court) if st.session_state.prefill_court in courts else 0
    court_choice = st.selectbox("Court:", courts, index=court_idx)
    
    free_hours = get_available_hours(court_choice, date_choice)
    if not free_hours:
        st.warning(f"😔 Sorry, no slots available for {court_choice} on {date_choice}.")
        time_choice = None
    else:
        time_options = [f"{h:02d}:00 - {h+1:02d}:00" for h in free_hours]
        time_idx = time_options.index(st.session_state.prefill_time) if st.session_state.prefill_time in time_options else 0
        time_choice = st.selectbox("Time Slot:", time_options, index=time_idx)

    active_count = get_active_bookings_count(villa, sub_community)
    daily_count = get_daily_bookings_count(villa, sub_community, date_choice)
    
    col_status1, col_status2 = st.columns(2)
    with col_status1: st.info(f"Total active bookings: **{active_count} / 6**")
    with col_status2: st.info(f"Bookings for {date_choice}: **{daily_count} / 2**")

    if st.button("Book This Slot", type="primary"):
        if not time_choice: st.error("Please select an available time slot.")
        elif active_count >= 6: st.error("🚫 Overall limit reached.")
        elif daily_count >= 2: st.error(f"🚫 Daily limit reached for {date_choice}.")
        else:
            start_h = int(time_choice.split(":")[0])
            if is_slot_booked(court_choice, date_choice, start_h): st.error("❌ Taken!")
            else:
                book_slot(villa, sub_community, court_choice, date_choice, start_h)
                st.session_state.prefill_court, st.session_state.prefill_time = None, None
                st.balloons()
                st.success(f"✅ Success!")
                time.sleep(2)
                st.session_state.active_tab = "📅 Availability"
                st.rerun()

# --- TAB 3: MY BOOKINGS ---
elif st.session_state.active_tab == "📋 My Bookings":
    st.subheader("📋 My Bookings")
    court_locations = { "Mira 2": "https://maps.google.com/?q=25.003702,55.306740", "Mira 4": "https://maps.google.com/?q=25.010338,55.305798", "Mira 5A": "https://maps.google.com/?q=25.007513,55.303432", "Mira 5B": "https://maps.google.com/?q=25.007513,55.303432", "Mira Oasis 1": "https://maps.google.com/?q=25.010536,55.296654", "Mira Oasis 2": "https://maps.google.com/?q=25.016439,55.298626", "Mira Oasis 3A": "https://maps.google.com/?q=25.012520,55.298313", "Mira Oasis 3B": "https://maps.google.com/?q=25.012520,55.298313", "Mira Oasis 3C": "https://maps.google.com/?q=25.015327,55.301998" }
    my_b = get_user_bookings(villa, sub_community)
    if not my_b: st.info("You have no active bookings.")
    else:
        for b in my_b:
            with st.container():
                st.markdown(f"**🎾 {b['court']}** | {b['date']} | {b['start_hour']:02d}:00")
                if st.button(f"❌ Cancel #{b['id']}", key=f"del_{b['id']}"):
                    delete_booking(b['id'], villa, sub_community)
                    st.rerun()

# --- TAB 4: LOGS ---
elif st.session_state.active_tab == "📜 Activity Log":
    st.subheader("Community Activity Log")
    logs = get_logs_last_14_days()
    if logs:
        log_df = pd.DataFrame(logs)
        st.dataframe(log_df, width="stretch")

# --- BACKUP & FOOTER ---
st.divider()
st.subheader("💾 Data Backup")
if st.button("Generate Backup Link"):
    try:
        bookings_data = run_query(supabase.table("bookings").select("*")).data
        df_bookings = pd.DataFrame(bookings_data)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as vz:
            vz.writestr(f"bookings_{get_today()}.csv", df_bookings.to_csv(index=False))
        st.download_button(label="Click to Download ZIP", data=buf.getvalue(), file_name=f"court_backup_{get_today()}.zip")
    except: st.error("Backup failed.")

col1, col2 = st.columns([1, 5])
with col1: st.markdown(f'<img src="https://raw.githubusercontent.com/mahadevbk/courtbooking/main/qr-code.miracourtbooking.streamlit.app.png" height="100">', unsafe_allow_html=True)
with col2: st.markdown("<div style='background-color: #0d5384; padding: 1rem; border-left: 5px solid #fff500; border-radius: 0.5rem; color: white;'>Built with ❤️ using Streamlit.</div>", unsafe_allow_html=True)
