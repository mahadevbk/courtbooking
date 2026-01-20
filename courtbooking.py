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
    # Check if slot is already booked in DB
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
    # 1. Get all hours already booked for this court/date
    response = supabase.table("bookings").select("start_hour").eq("court", court).eq("date", date_str).execute()
    booked_hours = [row['start_hour'] for row in response.data]
    
    # 2. Filter the global start_hours list
    available = []
    for h in start_hours:
        # Keep if NOT booked AND NOT in the past
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
.stDataFrame th { font-family: 'Audiowide', cursive; font-size: 12px; background-color: #2c3e50 !important; color: white !important; }
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
st.info("6 Active bookings possible over 14 days, A maximum of 2 active bookings per day.")

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

tab1, tab2, tab3, tab4, tab5 = st.tabs(["📅 Availability", "➕ Book", "📋 My Bookings", "❌ Cancel", "📜 Activity Log"])

with tab1:
    st.subheader("Court Availability")
    date_options = [f"{d.strftime('%Y-%m-%d')} ({d.strftime('%A')})" for d in get_next_14_days()]
    selected_date_full = st.selectbox("Select Date:", date_options)
    selected_date = selected_date_full.split(" (")[0]

    bookings_with_details = get_bookings_for_day_with_details(selected_date)
    data = {}
    for h in start_hours:
        label = f"{h:02d}:00 - {h+1:02d}:00"
        row = []
        for court in courts:
            key = (court, h)
            if is_slot_in_past(selected_date, h):
                row.append("—")
            elif key in bookings_with_details:
                full_comm, villa_num = bookings_with_details[key].rsplit(" - ", 1)
                row.append(f"{abbreviate_community(full_comm)}-{villa_num}")
            else:
                row.append("Available")
        data[label] = row

    st.dataframe(pd.DataFrame(data, index=courts).style.map(color_cell), width="stretch")
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

        st.write("**Weekly Intensity Heatmap**")
        heatmap_data = usage_data.groupby(['day_of_week', 'start_hour']).size().unstack(fill_value=0)
        heatmap_data = heatmap_data.reindex(days_order).fillna(0)
        
        st.dataframe(
            heatmap_data.style.background_gradient(cmap="YlGnBu"), 
            width="stretch"
        )
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
            else:
                st.write("No active bookings found for this villa.")


with tab2:
    st.subheader("Book a New Slot")
    # Date selection
    date_options = [f"{d.strftime('%Y-%m-%d')} ({d.strftime('%A')})" for d in get_next_14_days()]
    selected_date_full = st.selectbox("Date:", date_options)
    
    # Extract just the date part (YYYY-MM-DD) for database logic
    date_choice = selected_date_full.split(" (")[0]
    
    court_choice = st.selectbox("Court:", courts)
    
    # Dynamically fetch only free slots
    free_hours = get_available_hours(court_choice, date_choice)
    
    if not free_hours:
        st.warning(f"😔 Sorry, no slots available for {court_choice} on {date_choice}.")
        time_choice = None
    else:
        time_options = [f"{h:02d}:00 - {h+1:02d}:00" for h in free_hours]
        time_choice = st.selectbox("Time Slot:", time_options)

    # Fetch current booking counts for validation
    active_count = get_active_bookings_count(villa, sub_community)
    daily_count = get_daily_bookings_count(villa, sub_community, date_choice)
    
    # Display status to the user
    col_status1, col_status2 = st.columns(2)
    with col_status1:
        st.info(f"Total active bookings: **{active_count} / 6**")
    with col_status2:
        st.info(f"Bookings for {date_choice}: **{daily_count} / 2**")

    if st.button("Book This Slot", type="primary"):
        if not time_choice:
            st.error("Please select an available time slot.")
        elif active_count >= 6: 
            st.error("🚫 Overall limit reached. You cannot have more than 6 active bookings total.")
        elif daily_count >= 2:
            st.error(f"🚫 Daily limit reached. You cannot have more than 2 bookings on {date_choice}.")
        else:
            start_h = int(time_choice.split(":")[0])
            # Final check to prevent double booking if two users are on the same page
            if is_slot_booked(court_choice, date_choice, start_h):
                st.error("❌ This slot was just taken! Please refresh and try another.")
            else:
                book_slot(villa, sub_community, court_choice, date_choice, start_h)
                st.balloons()
                st.success(f"✅ SUCCESS! {court_choice} booked for {date_choice} at {start_h:02d}:00")
                time.sleep(2.5) 
                st.rerun()

with tab3:
    st.subheader("My Bookings")
    my_b = get_user_bookings(villa, sub_community)
    if not my_b: st.info("No bookings.")
    else:
        for b in my_b: st.write(f"• **{b['court']}** – {b['date']} at {b['start_hour']:02d}:00")

with tab4:
    st.subheader("Cancel Booking")
    my_b = get_user_bookings(villa, sub_community)
    if my_b:
        choice = st.selectbox("Select:", [f"{b['court']} on {b['date']} at {b['start_hour']:02d}:00 (ID: {b['id']})" for b in my_b])
        b_id = int(choice.split("ID: ")[-1].strip(")"))
        if st.checkbox("Confirm cancel") and st.button("Cancel Booking", type="primary"):
            delete_booking(b_id, villa, sub_community)
            st.success("Cancelled!")
            st.rerun()

with tab5:
    st.subheader("Community Activity Log (Last 14 Days)")
    st.caption("Timezone: UTC+4")
    
    logs = get_logs_last_14_days()
    
    if logs:
        log_df = pd.DataFrame(logs, columns=["timestamp", "event_type", "details"])
        log_df['timestamp'] = pd.to_datetime(log_df['timestamp']).dt.strftime('%b %d, %H:%M')

        def style_rows(row):
            styles = [''] * len(row)
            if row.event_type == "Booking Created":
                styles[1] = 'background-color: #d4edda; color: #155724; font-weight: bold;'
            elif row.event_type in ["Booking Deleted", "Booking Cancelled"]:
                styles[1] = 'background-color: #f8d7da; color: #721c24; font-weight: bold;'
            return styles

        styled_df = log_df.style.apply(style_rows, axis=1)
        
        st.dataframe(
            styled_df, 
            hide_index=True, 
            width="stretch"
        )
    else:
        st.info("No activity recorded in the last 14 days.")

# --- BACKUP SECTION ---
st.divider()
st.subheader("💾 Data Backup")

def create_zip_backup():
    bookings_data = supabase.table("bookings").select("*").execute().data
    logs_data = supabase.table("logs").select("*").execute().data
    
    df_bookings = pd.DataFrame(bookings_data)
    df_logs = pd.DataFrame(logs_data)
    
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "x", zipfile.ZIP_DEFLATED) as vz:
        vz.writestr(f"bookings_backup_{get_today()}.csv", df_bookings.to_csv(index=False))
        vz.writestr(f"logs_backup_{get_today()}.csv", df_logs.to_csv(index=False))
    return buf.getvalue()

st.download_button(
    label="📥 Download All Data (ZIP)",
    data=create_zip_backup(),
    file_name=f"court_booking_backup_{get_today()}.zip",
    mime="application/zip"
)


# Footer
image_url = "https://raw.githubusercontent.com/mahadevbk/courtbooking/main/qr-code.miracourtbooking.streamlit.app.png"

col1, col2 = st.columns([1, 5])

with col1:
    st.markdown(
        f'<img src="{image_url}" height="100">',
        unsafe_allow_html=True
    )

with col2:
    st.markdown("""
    <div style='background-color: #0d5384; padding: 1rem; border-left: 5px solid #fff500; border-radius: 0.5rem; color: white;'>
    Built with ❤️ using <a href='https://streamlit.io/' style='color: #ccff00;'>Streamlit</a> — free and open source.
    <a href='https://devs-scripts.streamlit.app/' style='color: #ccff00;'>Other Scripts by dev</a> on Streamlit.
    </div>
    """, unsafe_allow_html=True)
