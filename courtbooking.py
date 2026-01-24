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
    
    if df.empty:
        return pd.DataFrame()

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
.stApp {
  background: linear-gradient(to bottom, #010f1a, #052134);
  background-attachment: scroll;
}
[data-testid="stHeader"] { background: linear-gradient(to bottom, #052134 , #010f1a) !important; }
h1, h2, h3, .stTitle { font-family: 'Audiowide', cursive !important; color: #2c3e50; }
.stButton>button { background-color: #4CAF50; color: white; font-family: 'Audiowide', cursive; }

/* Mobile optimization: Grid Scrolling */
div[data-testid="stVerticalBlock"] > div[data-testid="stColumn"] {
    min-width: 80px !important;
}

/* Allow horizontal scrolling on the grid for mobile */
.st-emotion-cache-1ky8h60 {
    overflow-x: auto !important;
}

/* Calendar Tile Buttons - Compact for Mobile */
div[data-testid="stColumn"] button {
    width: 100% !important;
    padding: 6px 1px !important;
    font-size: 10px !important;
    border-radius: 4px !important;
    border: 1px solid #ccff0022 !important;
    background-color: #1b5e20 !important;
    color: #ccff00 !important;
}

/* Adjust text size for booked slots on mobile */
.booked-slot {
    font-size: 8px !important;
    line-height: 1 !important;
    padding: 2px !important;
}
</style>
""", unsafe_allow_html=True)

# --- LOGIC FOR FULL FRAME PAGE ---
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
                if is_slot_in_past(d_str, h):
                    row.append("—")
                elif key in bookings_with_details:
                    full_comm, villa_num = bookings_with_details[key].rsplit(" - ", 1)
                    abbr = abbreviate_community(full_comm)
                    row.append(f"{abbr}-{villa_num}")
                else:
                    row.append("Available")
            data[label] = row
        st.dataframe(pd.DataFrame(data, index=courts).style.map(color_cell), width="stretch")
        st.divider()
    st.stop()

# --- MAIN APP ---

st.subheader("🎾 Book that Court ...")    
st.caption("An Un-Official & Community Driven Booking Solution.")

villas_active = get_villas_with_active_bookings()
    
today_str = get_today().strftime('%Y-%m-%d')
now_hour = get_utc_plus_4().hour
total_active_response = supabase.table("bookings").select("id", count="exact")\
    .or_(f"date.gt.{today_str},and(date.eq.{today_str},start_hour.gte.{now_hour})")\
    .execute()
    
total_residences = len(villas_active)
total_bookings = total_active_response.count if total_active_response.count else 0

st.write(f"**{total_residences}** Residences have **{total_bookings}** active bookings.")


if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    col1, col2 = st.columns(2)
    with col1:
        sub_community_input = st.selectbox("Select Your Sub-Community", options=sub_community_list, index=None)
    with col2:
        villa_input = st.text_input("Enter Villa Number").strip().upper()

    if st.button("Confirm Identity", type="primary"):
        if sub_community_input and villa_input:
            st.session_state.sub_community, st.session_state.villa = sub_community_input, villa_input
            st.session_state.authenticated = True
            st.rerun()
    st.stop()

sub_community, villa = st.session_state.sub_community, st.session_state.villa
st.success(f"✅ Logged in as: **{sub_community} - Villa {villa}**")

tab1, tab2, tab3, tab4 = st.tabs(["📅 Availability", "➕ Book", "📋 My Bookings", "📜 Activity Log"])

with tab1:
    st.subheader("Court Availability")
    date_options = [f"{d.strftime('%Y-%m-%d')} ({d.strftime('%A')})" for d in get_next_14_days()]
    selected_date_full = st.selectbox("Select Date:", date_options)
    selected_date = selected_date_full.split(" (")[0]

    bookings_with_details = get_bookings_for_day_with_details(selected_date)
    
    st.info("Swipe left on the grid below to see more courts.")
    
    # Grid Header - Using a standard markdown table might be safer for mobile readability 
    # but we will stick with columns and rely on the CSS overflow.
    cols = st.columns([1] + [1] * len(courts))
    cols[0].markdown("**Time**")
    for i, court in enumerate(courts):
        cols[i+1].markdown(f"<div style='text-align:center; font-size:9px;'><b>{court}</b></div>", unsafe_allow_html=True)
    
    for h in start_hours:
        cols = st.columns([1] + [1] * len(courts))
        cols[0].write(f"**{h:02d}:00**")
        for i, court in enumerate(courts):
            key = (court, h)
            if is_slot_in_past(selected_date, h):
                cols[i+1].markdown("<div style='text-align:center; color:#555;'>—</div>", unsafe_allow_html=True)
            elif key in bookings_with_details:
                full_comm, villa_num = bookings_with_details[key].rsplit(" - ", 1)
                label = f"{abbreviate_community(full_comm)}-{villa_num}"
                cols[i+1].markdown(f"<div class='booked-slot' style='color:#ff6b6b; font-weight:bold; text-align:center; border:1px solid #ff6b6b33; border-radius:3px;'>{label}</div>", unsafe_allow_html=True)
            else:
                if cols[i+1].button("Book", key=f"grid_{selected_date}_{court}_{h}"):
                    st.session_state.pf_date = selected_date_full
                    st.session_state.pf_court = court
                    st.session_state.pf_hour = h
                    st.success(f"Selected! Go to '➕ Book' tab.")

    st.link_button("🌐 View Full 14-Day Schedule (Full Page)", url="/?view=full")
    
    st.divider()
    st.subheader("📊 Community Usage Insights")
    
    usage_data = get_peak_time_data()
    
    if not usage_data.empty:
        col_charts1, col_charts2 = st.columns([1, 1])
        
        with col_charts1:
            st.write("**🔥 Busiest Hours**")
            hour_counts = usage_data['start_hour'].value_counts().sort_index()
            chart_df = pd.DataFrame({
                "Bookings": hour_counts.values
            }, index=[f"{h:02d}:00" for h in hour_counts.index])
            st.bar_chart(chart_df, color="#4CAF50")

        with col_charts2:
            st.write("**📅 Busiest Days**")
            day_counts = usage_data['day_of_week'].value_counts()
            days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            day_counts = day_counts.reindex(days_order).fillna(0)
            st.area_chart(day_counts, color="#0d5384")
    else:
        st.info("Charts will appear here once more bookings are made!")
    
    st.divider()
    st.subheader("🔍 Booking Lookup")

    if villas_active:
        look_villa = st.selectbox("Select Villa to see details:", options=["-- Select --"] + villas_active)
        if look_villa != "-- Select --":
            active_list = get_active_bookings_for_villa_display(look_villa)
            if active_list:
                st.selectbox("Active bookings for this villa:", options=active_list)


with tab2:
    st.subheader("Book a New Slot")
    
    # Handle Prefill Logic
    d_idx = 0
    if "pf_date" in st.session_state and st.session_state.pf_date in date_options:
        d_idx = date_options.index(st.session_state.pf_date)
    
    selected_date_full = st.selectbox("Date:", date_options, index=d_idx)
    date_choice = selected_date_full.split(" (")[0]
    
    c_idx = 0
    if "pf_court" in st.session_state and st.session_state.pf_court in courts:
        c_idx = courts.index(st.session_state.pf_court)

    court_choice = st.selectbox("Court:", courts, index=c_idx)
    
    free_hours = get_available_hours(court_choice, date_choice)
    
    if not free_hours:
        st.warning(f"😔 Sorry, no slots available.")
        time_choice = None
    else:
        h_idx = 0
        if "pf_hour" in st.session_state and st.session_state.pf_hour in free_hours:
            h_idx = free_hours.index(st.session_state.pf_hour)
            
        time_options = [f"{h:02d}:00 - {h+1:02d}:00" for h in free_hours]
        time_choice = st.selectbox("Time Slot:", time_options, index=h_idx)

    active_count = get_active_bookings_count(villa, sub_community)
    daily_count = get_daily_bookings_count(villa, sub_community, date_choice)
    
    col_status1, col_status2 = st.columns(2)
    with col_status1:
        st.info(f"Total active: **{active_count}/6**")
    with col_status2:
        st.info(f"Today: **{daily_count}/2**")

    if st.button("Confirm & Book Slot", type="primary"):
        if not time_choice:
            st.error("Please select an available time slot.")
        elif active_count >= 6 or daily_count >= 2:
            st.error("🚫 Booking limits reached.")
        else:
            start_h = int(time_choice.split(":")[0])
            if is_slot_booked(court_choice, date_choice, start_h):
                st.error("❌ Slot taken!")
            else:
                book_slot(villa, sub_community, court_choice, date_choice, start_h)
                for key in ["pf_date", "pf_court", "pf_hour"]:
                    if key in st.session_state: del st.session_state[key]
                st.balloons()
                st.success("✅ Booked!")
                time.sleep(1) 
                st.rerun()

with tab3:
    st.subheader("📋 My Bookings")
    my_b = get_user_bookings(villa, sub_community)
    if not my_b:
        st.info("No active bookings.")
    else:
        for i, b in enumerate(my_b):
            with st.expander(f"🎾 {b['court']} - {b['date']} @ {b['start_hour']:02d}:00"):
                if st.button(f"Cancel Booking #{b['id']}", key=f"cancel_{i}"):
                    delete_booking(b['id'], villa, sub_community)
                    st.rerun()

with tab4:
    st.subheader("📜 Activity Log")
    logs = get_logs_last_14_days()
    if logs:
        st.dataframe(pd.DataFrame(logs)[["timestamp", "event_type", "details"]], hide_index=True)

# --- BACKUP SECTION ---
st.divider()
def create_zip_backup():
    bookings_data = supabase.table("bookings").select("*").execute().data
    df_bookings = pd.DataFrame(bookings_data)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "x", zipfile.ZIP_DEFLATED) as vz:
        vz.writestr(f"backup.csv", df_bookings.to_csv(index=False))
    return buf.getvalue()

st.download_button("📥 Backup (ZIP)", data=create_zip_backup(), file_name=f"backup.zip")

# Footer
image_url = "https://raw.githubusercontent.com/mahadevbk/courtbooking/main/qr-code.miracourtbooking.streamlit.app.png"
col1, col2 = st.columns([1, 5])
with col1:
    st.image(image_url, width=80)
with col2:
    st.markdown("<div style='font-size:10px; color:white;'>Built with Streamlit</div>", unsafe_allow_html=True)
