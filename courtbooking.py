import time
import streamlit as st
from supabase import create_client, Client
from datetime import datetime, timedelta, timezone
import pandas as pd
import zipfile
import io
import random
import json
import base64
import hashlib
import re
from postgrest.exceptions import APIError 
from PIL import Image # For image resizing
from streamlit_javascript import st_javascript
import urllib.parse

# Set page configuration to wide mode by default
st.set_page_config(
    page_title="Mira Court Booking",
    page_icon="🎾",
    layout="wide",
)

# --- DONOR TICKER (scrolls at the top of every tab) ---
DONOR_NAMES = [
    "Abhisek", "Adam", "Arlan", "Alesia", "Angelo", "Carlos", "Charbel", "Dev", "Elie",
    "Farheen", "Hana", "Harith", "Hisham", "Katya", "Khaled", "Leina", "Marko", "Mei",
    "Melissa", "Mustafa", "Nikki", "Rena", "Riin", "Saket", "Sheila", "Sofia", "Vik", "Yousef",
]

def render_donor_ticker(names):
    """Renders a fixed, auto-scrolling ticker of uppercase donor names separated by tennis ball icons."""
    if not names:
        return

    uppercase_names = [name.upper() for name in names]

    tennis_ball_svg = (
        '<svg style="vertical-align: middle; margin: 0 10px; display: inline-block;" '
        'width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#0d5384" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="10" fill="#ccff00" stroke="#0d5384"/>'
        '<path d="M5.64 5.64a9 9 0 0 1 0 12.72"/>'
        '<path d="M18.36 5.64a9 9 0 0 0 0 12.72"/>'
        '</svg>'
    )

    ticker_text = tennis_ball_svg.join(uppercase_names)

    st.markdown(
        f"""
        <style>
        .donor-ticker-wrap {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            z-index: 1000000;
            background-color: #0d5384;
            color: #ccff00;
            overflow: hidden;
            white-space: nowrap;
            padding: 6px 0;
            border-bottom: 2px solid #fff500;
            box-sizing: border-box;
        }}
        .donor-ticker-move {{
            display: inline-block;
            white-space: nowrap;
            padding-left: 100%;
            font-size: 0.9rem;
            font-weight: 600;
            animation: donor-ticker-scroll 30s linear infinite;
        }}
        .donor-ticker-move:hover {{
            animation-play-state: paused;
        }}
        @keyframes donor-ticker-scroll {{
            0%   {{ transform: translate(0, 0); }}
            100% {{ transform: translate(-100%, 0); }}
        }}
        .donor-ticker-spacer {{
            height: 34px;
        }}
        </style>
        <div class="donor-ticker-wrap">
            <div class="donor-ticker-move">
                {tennis_ball_svg} Huge thanks to these legends for their support ! {tennis_ball_svg} {ticker_text} {tennis_ball_svg}
            </div>
        </div>
        <div class="donor-ticker-spacer"></div>
        """,
        unsafe_allow_html=True,
    )

render_donor_ticker(DONOR_NAMES)

# --- DATABASE SETUP (SUPABASE) ---
@st.cache_resource
def init_supabase():
    url: str = st.secrets["SUPABASE_URL"]
    key: str = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase: Client = init_supabase()

@st.cache_data(ttl=3600)
def get_maintenance_data():
    """Fetches maintenance reports with a 1-hour cache."""
    return run_query(supabase.table("court_maintenance").select("*").order("created_at", desc=True))

# Constants
sub_community_list = [
    "Mira 1", "Mira 2", "Mira 3", "Mira 4", "Mira 5",
    "Mira Oasis 1", "Mira Oasis 2", "Mira Oasis 3"
]

courts = ["Mira 2", "Mira 4", "Mira 5A", "Mira 5B", "Mira Oasis 1", "Mira Oasis 2", "Mira Oasis 3A", "Mira Oasis 3B", "Mira Oasis 3C"]

DISPOSABLE_DOMAINS = {
    "mailinator.com", "tempmail.com", "10minutemail.com", "guerrillamail.com",
    "trashmail.com", "yopmail.com", "sharklasers.com", "getairmail.com", "throwawaymail.com"
}

def is_disposable_email(email_str):
    domain = email_str.split("@")[-1].lower() if "@" in email_str else ""
    return domain in DISPOSABLE_DOMAINS

def get_start_hours_for_date(date_str):
    if date_str <= "2026-03-22":
        return list(range(7, 24))
    return list(range(7, 22))

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
        except APIError as e:
            if e.code == "23505":
                raise e
            if attempt == max_retries - 1:
                raise e
            time.sleep((0.5 * (2 ** attempt)) + random.uniform(0, 0.2))
        except Exception as e:
            if attempt == max_retries - 1:
                st.error(f"⚠️ Connection Error: {str(e)}")
                return None
            time.sleep((0.5 * (2 ** attempt)) + random.uniform(0, 0.2))

def add_log(event_type, details, fingerprint=None):
    timestamp = get_utc_plus_4().isoformat()
    try:
        log_entry = {
            "timestamp": timestamp,
            "event_type": event_type,
            "details": details
        }
        if fingerprint:
            log_entry["Fingerprint"] = fingerprint
        supabase.table("logs").insert(log_entry).execute()
    except Exception:
        pass 

def mask_email(email_str):
    """Masks an email address using standard conventions (e.g. j***e@domain.com)."""
    try:
        user, domain = email_str.split("@", 1)
        if len(user) <= 1:
            masked_user = f"{user}*"
        elif len(user) == 2:
            masked_user = f"{user[0]}*"
        else:
            masked_user = f"{user[0]}{'*' * (len(user) - 2)}{user[-1]}"
        return f"{masked_user}@{domain}"
    except Exception:
        return email_str

def mask_emails_in_text(text):
    """Detects and masks all email addresses inside a text string."""
    if not isinstance(text, str):
        return text
    email_pattern = r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
    return re.sub(email_pattern, lambda m: mask_email(m.group(0)), text)

def get_villa_claims_count(sub_community, villa):
    res = run_query(supabase.table("villa_claims").select("id", count="exact")
                    .eq("sub_community", sub_community)
                    .eq("villa", villa)
                    .eq("status", "approved"))
    return res.count if res and res.count is not None else 0

def get_existing_claim(sub_community, villa, email):
    res = run_query(supabase.table("villa_claims").select("*")
                    .eq("sub_community", sub_community)
                    .eq("villa", villa)
                    .eq("email", email.strip().lower()))
    return res.data[0] if res and res.data else None

def get_email_claimed_villas_count(email):
    res = run_query(supabase.table("villa_claims").select("sub_community, villa")
                    .eq("email", email.strip().lower())
                    .eq("status", "approved"))
    if not res or not res.data:
        return 0
    unique_villas = set([f"{r['sub_community']}::{r['villa']}" for r in res.data])
    return len(unique_villas)

def get_uuid_claimed_villas(uuid_val):
    if not uuid_val or uuid_val in ("no_uuid", "device_pending"):
        return set()
    res = run_query(supabase.table("villa_claims").select("sub_community, villa")
                    .eq("fingerprint", uuid_val)
                    .eq("status", "approved"))
    if not res or not res.data:
        return set()
    return set([f"{r['sub_community']}::{r['villa']}" for r in res.data])

def get_claims_for_villa(sub_community, villa):
    res = run_query(supabase.table("villa_claims").select("*")
                    .eq("sub_community", sub_community)
                    .eq("villa", villa)
                    .order("created_at"))
    return res.data if res and res.data else []

def get_recent_claim_cooldown(sub_community, villa, requesting_email):
    """Checks if this villa was verified by a different email within the last 72 hours."""
    claims = get_claims_for_villa(sub_community, villa)
    if not claims:
        return False, None

    now = get_utc_plus_4()
    req_email_clean = requesting_email.strip().lower()

    for c in claims:
        c_email = c.get("email", "").strip().lower()
        if c_email and c_email != req_email_clean:
            v_time_str = c.get("verified_at") or c.get("created_at")
            if v_time_str:
                try:
                    v_dt = datetime.fromisoformat(v_time_str.replace("Z", "+00:00")).replace(tzinfo=None)
                    delta = now - v_dt
                    if delta < timedelta(hours=72):
                        remaining_hours = max(1, int((timedelta(hours=72) - delta).total_seconds() // 3600))
                        return True, remaining_hours
                except Exception:
                    pass
    return False, None

def get_all_claimed_villas():
    res = run_query(supabase.table("villa_claims").select("sub_community, villa"))
    if not res or not res.data: return []
    unique_villas = sorted(list(set([f"{row['sub_community']} - {row['villa']}" for row in res.data])))
    return unique_villas

def get_bookings_for_day_with_details(date_str):
    response = run_query(supabase.table("bookings").select("court, start_hour, sub_community, villa").eq("date", date_str))
    if not response or not response.data: return {}
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
    
    q_future = supabase.table("bookings").select("id", count="exact")
    q_future = q_future.eq("villa", villa).eq("sub_community", sub_community)
    
    res_future = run_query(q_future.gt("date", today_str))
    count_future = res_future.count if res_future and res_future.count is not None else 0
    
    q_today = supabase.table("bookings").select("id", count="exact")
    q_today = q_today.eq("villa", villa).eq("sub_community", sub_community)
    
    res_today = run_query(q_today.eq("date", today_str).gte("start_hour", now_hour))
    count_today = res_today.count if res_today and res_today.count is not None else 0
    
    return count_future + count_today

def get_daily_bookings_count(villa, sub_community, date_str):
    mira1_group = ["229", "231", "233"]
    is_mira1_group = (sub_community == "Mira 1" and villa in mira1_group)

    if is_mira1_group:
        other_villas = [v for v in mira1_group if v != villa]
        res_others = run_query(supabase.table("bookings").select("id", count="exact").eq("sub_community", "Mira 1").in_("villa", other_villas).eq("date", date_str))
        others_count = res_others.count if res_others and res_others.count is not None else 0
        
        if others_count > 0:
            return 99 
        
        res_self = run_query(supabase.table("bookings").select("id", count="exact").eq("sub_community", "Mira 1").eq("villa", villa).eq("date", date_str))
        return res_self.count if res_self and res_self.count is not None else 0
    else:
        query = supabase.table("bookings").select("id", count="exact")
        query = query.eq("villa", villa).eq("sub_community", sub_community)
        response = run_query(query.eq("date", date_str))
        if response is None or response.count is None: return 99
        return response.count

def is_slot_booked(court, date_str, start_hour):
    response = run_query(
        supabase.table("bookings").select("id")
        .eq("court", court)
        .eq("date", date_str)
        .eq("start_hour", start_hour)
    )
    if not response or not response.data: return False
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
    try:
        run_query(supabase.table("bookings").insert({
            "villa": villa,
            "sub_community": sub_community,
            "court": court,
            "date": date_str,
            "start_hour": start_hour
        }))
        log_detail = f"{sub_community} Villa {villa} booked {court} for {date_str} at {start_hour:02d}:00"
        add_log("Booking Created", log_detail)
        return True
    except APIError as e:
        if e.code == "23505":
            return False
        raise e
    except Exception:
        return False

def get_user_bookings(villa, sub_community):
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    response = run_query(
        supabase.table("bookings").select("id, court, date, start_hour")
        .eq("villa", villa)
        .eq("sub_community", sub_community)
        .or_(f"date.gt.{today_str},and(date.eq.{today_str},start_hour.gte.{now_hour})")
        .order("date")
        .order("start_hour")
    )
    return response.data if response else []

def delete_booking(booking_id, villa, sub_community):
    record = run_query(supabase.table("bookings").select("court, date, start_hour").eq("id", booking_id).single())
    if record and record.data:
        b = record.data
        log_detail = f"{sub_community} Villa {villa} cancelled {b['court']} for {b['date']} at {b['start_hour']:02d}:00"
        add_log("Booking Deleted", log_detail)
    run_query(supabase.table("bookings").delete().eq("id", booking_id).eq("villa", villa).eq("sub_community", sub_community))

@st.cache_data(ttl=60)
def get_logs_last_14_days():
    cutoff = (get_utc_plus_4() - timedelta(days=14)).isoformat()
    response = run_query(
        supabase.table("logs").select("timestamp, event_type, Fingerprint, details")
        .gte("timestamp", cutoff)
        .order("timestamp", desc=True)
    )
    return response.data if response else []

def get_villas_with_active_bookings():
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    try:
        res_future = run_query(supabase.table("bookings").select("villa, sub_community").gt("date", today_str))
        res_today = run_query(supabase.table("bookings").select("villa, sub_community").eq("date", today_str).gte("start_hour", now_hour))
        
        all_rows = (res_future.data if res_future else []) + (res_today.data if res_today else [])
        unique_villas = sorted(list(set([f"{row['sub_community']} - {row['villa']}" for row in all_rows])))
        return unique_villas
    except Exception:
        return []

def get_all_villas_with_any_bookings():
    response = run_query(supabase.table("bookings").select("villa, sub_community"))
    if not response or not response.data: return []
    unique_villas = sorted(list(set([f"{row['sub_community']} - {row['villa']}" for row in response.data])))
    return unique_villas

def get_bookings_for_villa(villa, sub_community):
    response = run_query(
        supabase.table("bookings").select("id, court, date, start_hour")
        .eq("villa", villa)
        .eq("sub_community", sub_community)
        .order("date", desc=True)
        .order("start_hour", desc=True)
    )
    return response.data if response else []

def _process_background_tasks():
    try:
        from database_cleanup import run_db_cleanup
        run_db_cleanup(supabase, courts)
    except Exception:
        pass

def get_active_bookings_for_villa_display(villa_identifier):
    try:
        sub_comm, villa_num = villa_identifier.split(" - ")
        today_str = get_today().strftime('%Y-%m-%d')
        now_hour = get_utc_plus_4().hour
        response = run_query(
            supabase.table("bookings").select("court, date, start_hour")
            .eq("villa", villa_num)
            .eq("sub_community", sub_comm)
            .or_(f"date.gt.{today_str},and(date.eq.{today_str},start_hour.gte.{now_hour})")
            .order("date")
            .order("start_hour")
        )
        return [f"{b['date']} | {b['start_hour']:02d}:00 | {b['court']}" for b in response.data]
    except Exception:
        return []

def get_peak_time_data():
    response = run_query(supabase.table("bookings").select("date, start_hour"))
    if not response or not response.data: return pd.DataFrame()
    df = pd.DataFrame(response.data)
    if df.empty: return pd.DataFrame()
    df['date'] = pd.to_datetime(df['date'])
    df['day_of_week'] = df['date'].dt.day_name()
    return df

def get_available_hours(court, date_str):
    response = run_query(supabase.table("bookings").select("start_hour").eq("court", court).eq("date", date_str))
    if not response or not response.data:
        booked_hours = []
    else:
        booked_hours = [row['start_hour'] for row in response.data]
    available = []
    for h in get_start_hours_for_date(date_str):
        if h not in booked_hours and not is_slot_in_past(date_str, h):
            available.append(h)
    return available

# --- FLOATING ANNOUNCEMENT DIALOG (DISMISSAL WITH APP SCOPE) ---
@st.dialog("🎾 Notice: A Fairer Booking System for Everyone!")
def show_migration_dialog():
    st.markdown("""
    Hi neighbors! 👋

    To keep court bookings fair and stop people from booking under fake or multiple villas, we are introducing a simple **one-time email verification**[cite: 1].

    **What this means for you:**
    * **Fair access for real residents:** Keeps slots open for those who actually live here[cite: 1].
    * **One-time only:** Just enter your email and a 6-digit code once — your device will remember you automatically after that![cite: 1]
    * **Family friendly:** Up to 2 emails can be linked to your villa (e.g. partners or housemates)[cite: 1].

    ---
    Please enter your resident email below to receive your 6-digit verification code[cite: 1].

    💬 *Please reach out to Dev in case you have any queries.*
    """)
    if st.button("Got it — Continue 🎾", type="primary", use_container_width=True):
        st.session_state.seen_migration_notice = True
        st.rerun(scope="app")

# --- ZERO-LATENCY TOKEN AUTH (PERSISTS SYNCHRONOUSLY ACROSS BROWSER REFRESH) ---
AUTH_SALT = "mira_court_booking_salt_2026"

def encode_auth_token(sub_community, villa, email):
    """Encodes credentials into a signed URL token. Strict enforcement: email must be present."""
    if not email:
        return ""
    payload = f"{sub_community}::{villa}::{email}"
    sig = hashlib.sha256(f"{payload}:{AUTH_SALT}".encode()).hexdigest()[:10]
    raw = f"{payload}::{sig}".encode()
    return base64.urlsafe_b64encode(raw).decode()

def decode_auth_token(token_str):
    """Verifies and decodes the URL auth token."""
    try:
        raw = base64.urlsafe_b64decode(token_str.encode()).decode()
        parts = raw.split("::")
        if len(parts) == 4:
            sub, villa, email, sig = parts
            if not email:
                return None
            expected_sig = hashlib.sha256(f"{sub}::{villa}::{email}:{AUTH_SALT}".encode()).hexdigest()[:10]
            if sig == expected_sig:
                return {"sub_community": sub, "villa": villa, "email": email}
    except Exception:
        pass
    return None

def logout_action():
    """Centrally handles logout, clears tokens and localStorage."""
    st_javascript("""
        localStorage.removeItem('court_villa_lock');
        localStorage.removeItem('court_verified_email');
        localStorage.removeItem('verified_claim_info');
        localStorage.removeItem('supabase_refresh_token');
        setTimeout(() => { window.location.href = window.location.origin + window.location.pathname; }, 150);
    """)
    for key in ["authenticated", "sub_community", "villa", "verified_email", "otp_sent", "otp_email", "otp_target_villa", "otp_target_sub", "prefill_sub", "prefill_villa"]:
        if key in st.session_state:
            del st.session_state[key]
    st.query_params.clear()
    st.info("Logging out... Please wait.")
    time.sleep(0.8)
    st.rerun()

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

# --- LOGIC FOR FULL FRAME PAGE ---
if st.query_params.get("view") == "full":
    st.title("📅 Full 14-Day Schedule")
    if st.button("⬅️ Back to Booking App"):
        curr_auth = st.query_params.get("auth")
        st.query_params.clear()
        if curr_auth:
            st.query_params["auth"] = curr_auth
        st.rerun()
    for d in get_next_14_days():
        d_str = d.strftime('%Y-%m-%d')
        st.subheader(f"{d_str} ({d.strftime('%A')})")
        bookings_with_details = get_bookings_for_day_with_details(d_str)
        data = {}
        for h in get_start_hours_for_date(d_str):
            label = f"{h:02d}:00 - {h+1:02d}:00"
            row = []
            for court in courts:
                key = (court, h)
                if is_slot_in_past(d_str, h): row.append("—")
                elif key in bookings_with_details:
                    full_comm, villa_num = bookings_with_details[key].rsplit(" - ", 1)
                    abbr = abbreviate_community(full_comm)
                    row.append(f"{abbr}-{villa_num}")
                else: row.append("Available")
            data[label] = row
        st.dataframe(pd.DataFrame(data, index=courts).style.map(color_cell), width="stretch")
        st.divider()
    st.stop()

# --- MAIN APP ---
st.subheader("🎾 Book that Court ...")    
st.caption("An Un-Official & Community Driven Booking Solution.")
st.markdown(
    "<p style='color:#ccff00; font-weight:700; margin-top:-8px;'>"
    "Serving about 2,350 active users, the operation costs of this app "
    "(DB & SaaS hosting) are shared by the legends of the Mira Tennis Community. "
    "Reach out to Dev, if you'd like to help."
    "</p>",
    unsafe_allow_html=True,
)

try:
    _process_background_tasks()
    villas_active = get_villas_with_active_bookings()
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    
    res_f = run_query(supabase.table("bookings").select("id", count="exact").gt("date", today_str))
    res_t = run_query(supabase.table("bookings").select("id", count="exact").eq("date", today_str).gte("start_hour", now_hour))
    
    total_residences = len(villas_active)
    count_f = res_f.count if res_f and res_f.count is not None else 0
    count_t = res_t.count if res_t and res_t.count is not None else 0
    total_bookings = count_f + count_t
    
    st.write(f"**{total_residences}** Residences have **{total_bookings}** active bookings.")
except Exception:
    st.write("Unable to load live stats (Network refreshing...)")
    villas_active = []

if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

if "device_uuid" not in st.session_state:
    st.session_state.device_uuid = f"dev_{random.randint(10000000, 99999999)}_{int(time.time())}"

# 1. SYNCHRONOUS URL TOKEN AUTH (ONLY ACCEPTS VERIFIED TOKENS CONTAINING EMAIL)
url_token = st.query_params.get("auth")
if url_token and not st.session_state.authenticated:
    verified_claim = decode_auth_token(url_token)
    if verified_claim and verified_claim.get("email"):
        st.session_state.sub_community = verified_claim["sub_community"]
        st.session_state.villa = verified_claim["villa"]
        st.session_state.verified_email = verified_claim["email"]
        st.session_state.authenticated = True

# 2. LOCALSTORAGE INSPECTION (EXTRACT PRE-FILLS, DO NOT AUTO-LOG IN WITHOUT EMAIL)
if not st.session_state.authenticated:
    stored_bundle = st_javascript("(localStorage.getItem('court_villa_lock') || 'no_lock') + ':::' + (localStorage.getItem('court_verified_email') || '') + ':::' + (localStorage.getItem('verified_claim_info') || '');")
    
    if isinstance(stored_bundle, str) and ":::" in stored_bundle:
        parts = stored_bundle.split(":::")
        s_lock = parts[0] if len(parts) > 0 else ""
        s_email = parts[1] if len(parts) > 1 else ""
        s_claim = parts[2] if len(parts) > 2 else ""
        
        # If user ALREADY verified previously with email in localStorage, restore them and upgrade to signed URL token
        if s_email and s_email != "":
            target_sub = None
            target_villa = None
            if s_claim and "::" in s_claim:
                target_sub, target_villa = s_claim.split("::", 1)
            elif s_lock and s_lock != "no_lock" and "-" in s_lock:
                target_sub, target_villa = s_lock.rsplit("-", 1)
                
            if target_sub and target_villa:
                st.session_state.sub_community = target_sub
                st.session_state.villa = target_villa
                st.session_state.verified_email = s_email
                st.session_state.authenticated = True
                st.query_params["auth"] = encode_auth_token(target_sub, target_villa, s_email)
                st.rerun()

        # If phone only had a legacy lock WITHOUT verified email, PRE-FILL but do NOT authenticate
        elif s_lock and s_lock != "no_lock" and "-" in s_lock:
            try:
                locked_sub, locked_villa = s_lock.rsplit("-", 1)
                st.session_state.prefill_sub = locked_sub
                st.session_state.prefill_villa = locked_villa
            except Exception:
                pass

# --- REGISTRATION & VERIFICATION GATE ---
if not st.session_state.authenticated:
    # Trigger the floating modal dialog on first unauthenticated visit
    if "seen_migration_notice" not in st.session_state:
        st.session_state.seen_migration_notice = False

    if not st.session_state.seen_migration_notice:
        show_migration_dialog()

    if "otp_sent" not in st.session_state:
        st.session_state.otp_sent = False
    if "otp_email" not in st.session_state:
        st.session_state.otp_email = ""
    if "otp_target_villa" not in st.session_state:
        st.session_state.otp_target_villa = None
    if "otp_target_sub" not in st.session_state:
        st.session_state.otp_target_sub = None

    default_sub_idx = None
    prefill_sub = st.session_state.get("prefill_sub")
    if prefill_sub in sub_community_list:
        default_sub_idx = sub_community_list.index(prefill_sub)
    default_villa = st.session_state.get("prefill_villa", "")

    st.subheader("🛡️ Resident Email Verification")
    st.caption("One-time 6-digit verification code. Max 2 resident emails per villa.")

    if not st.session_state.otp_sent:
        col_v1, col_v2 = st.columns(2)
        with col_v1:
            otp_sub = st.selectbox("Sub-Community", options=sub_community_list, index=default_sub_idx, key="otp_sub_select")
        with col_v2:
            otp_villa_raw = st.text_input("Villa Number", value=default_villa, key="otp_villa_text").strip()
            otp_villa = "".join(filter(str.isdigit, otp_villa_raw))

        otp_email_input = st.text_input("Email Address", placeholder="name@example.com", key="otp_email_text").strip().lower()

        if st.button("Send 6-Digit Code", type="primary", width='stretch'):
            if not otp_sub or not otp_villa:
                st.error("Please specify your Sub-Community and Villa Number.")
            elif not otp_email_input or "@" not in otp_email_input:
                st.error("Please provide a valid email address.")
            elif is_disposable_email(otp_email_input):
                st.error("Disposable/temporary email domains are not allowed. Please use a personal or work email.")
            else:
                existing_claim = get_existing_claim(otp_sub, otp_villa, otp_email_input)
                current_claims_count = get_villa_claims_count(otp_sub, otp_villa)
                email_villas_count = get_email_claimed_villas_count(otp_email_input)
                
                # Check 72-Hour Cooldown for this villa
                is_on_cooldown, hours_left = get_recent_claim_cooldown(otp_sub, otp_villa, otp_email_input)
                
                target_pair = f"{otp_sub}::{otp_villa}"
                current_uuid = st.session_state.get("device_uuid", "device_pending")
                uuid_villas = get_uuid_claimed_villas(current_uuid)
                
                if not existing_claim and is_on_cooldown:
                    st.error(
                        f"🚫 Security Lockout: This villa ({otp_sub} - Villa {otp_villa}) was recently verified by another resident. "
                        f"Villa changes have a 72-hour security cooldown ({hours_left} hours remaining). "
                        "Please contact Dev in Court Maintenance for urgent reassignment."
                    )
                    add_log("Access Denied", f"Villa {otp_sub} Villa {otp_villa} 72h cooldown triggered by {otp_email_input} ({hours_left}h left)", fingerprint=current_uuid)
                elif not existing_claim and current_claims_count >= 2:
                    st.error(
                        f"🚫 This villa ({otp_sub} - Villa {otp_villa}) already has 2 verified resident emails attached. "
                        "If you recently moved in or need to update your registered email, please reach out via the contact channels in Court Maintenance."
                    )
                elif not existing_claim and email_villas_count >= 3:
                    st.error(
                        "Unable to register this villa to your email address. "
                        "Please contact Dev via the contact details in Court Maintenance for assistance."
                    )
                    add_log("Access Denied", f"Email {otp_email_input} exceeded 3-villa cap attempting {otp_sub} Villa {otp_villa}", fingerprint=current_uuid)
                elif not existing_claim and target_pair not in uuid_villas and len(uuid_villas) >= 3:
                    st.error(
                        "This device has reached the maximum allowed registered villas. "
                        "Please contact Dev via Court Maintenance if you require an exception."
                    )
                    add_log("Access Denied", f"Device UUID {current_uuid} blocked from requesting OTP for 4th villa ({otp_sub} Villa {otp_villa})", fingerprint=current_uuid)
                else:
                    with st.spinner("Sending 6-digit verification code..."):
                        try:
                            supabase.auth.sign_in_with_otp({"email": otp_email_input})
                            st.session_state.otp_sent = True
                            st.session_state.otp_email = otp_email_input
                            st.session_state.otp_target_sub = otp_sub
                            st.session_state.otp_target_villa = otp_villa
                            st.success(f"✅ Code sent! Please check your inbox at {otp_email_input}")
                            time.sleep(1.2)
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to send code: {str(e)}")
        
        st.write("")
        if st.button("🚪 Reset / Clear Details", width='stretch', key="reg_logout_presend"):
            logout_action()
    else:
        st.info(f"Enter the 6-digit code sent to **{st.session_state.otp_email}** for **{st.session_state.otp_target_sub} - Villa {st.session_state.otp_target_villa}**.")
        st.caption("Check your spam/junk folder if the email does not appear in your inbox within a minute.")
        token_input = st.text_input("Enter 6-digit code", max_chars=6, key="otp_token_text").strip()
        
        c1, c2, c3 = st.columns([1.5, 1.2, 1.2])
        with c1:
            if st.button("Verify Code", type="primary", width='stretch'):
                if not token_input or len(token_input) != 6:
                    st.error("Please enter a 6-digit verification code.")
                else:
                    with st.spinner("Verifying code..."):
                        try:
                            res = supabase.auth.verify_otp({
                                "email": st.session_state.otp_email,
                                "token": token_input,
                                "type": "email"
                            })
                            if res and res.session:
                                target_sub = st.session_state.otp_target_sub
                                target_villa = st.session_state.otp_target_villa
                                verified_email = st.session_state.otp_email
                                refresh_tok = res.session.refresh_token
                                resolved_uuid = st.session_state.device_uuid

                                existing = get_existing_claim(target_sub, target_villa, verified_email)
                                now_ts = get_utc_plus_4().isoformat()
                                
                                if not existing:
                                    run_query(supabase.table("villa_claims").insert({
                                        "sub_community": target_sub,
                                        "villa": target_villa,
                                        "email": verified_email,
                                        "fingerprint": resolved_uuid,
                                        "status": "approved",
                                        "verified_at": now_ts
                                    }))
                                    add_log("Villa Claim", f"{target_sub} Villa {target_villa} claimed by {verified_email}", fingerprint=resolved_uuid)
                                else:
                                    run_query(supabase.table("villa_claims").update({
                                        "verified_at": now_ts,
                                        "fingerprint": resolved_uuid,
                                        "status": "approved"
                                    }).eq("id", existing["id"]))

                                fallback_choice = f"{target_sub}-{target_villa}"
                                claim_bundle = f"{target_sub}::{target_villa}"
                                
                                st_javascript(f"""
                                    localStorage.setItem('court_villa_lock', '{fallback_choice}');
                                    localStorage.setItem('court_verified_email', '{verified_email}');
                                    localStorage.setItem('verified_claim_info', '{claim_bundle}');
                                    localStorage.setItem('supabase_refresh_token', '{refresh_tok}');
                                """)

                                # Save synchronous signed token in URL to survive refreshes
                                st.query_params["auth"] = encode_auth_token(target_sub, target_villa, verified_email)

                                st.session_state.sub_community = target_sub
                                st.session_state.villa = target_villa
                                st.session_state.verified_email = verified_email
                                st.session_state.authenticated = True
                                st.session_state.otp_sent = False
                                
                                st.balloons()
                                st.success("✅ Verified and registered successfully! Logging you in...")
                                time.sleep(1.2)
                                st.rerun()
                            else:
                                st.error("Verification failed. Please check the code.")
                        except Exception as e:
                            st.error(f"Invalid code or verification error: {str(e)}")
        with c2:
            if st.button("🔄 Resend Code", width='stretch'):
                with st.spinner("Resending code..."):
                    try:
                        supabase.auth.sign_in_with_otp({"email": st.session_state.otp_email})
                        st.toast(f"A new 6-digit code has been sent to {st.session_state.otp_email}!")
                    except Exception as e:
                        st.error(f"Could not resend code: {str(e)}")
        with c3:
            if st.button("Cancel / Change", width='stretch'):
                st.session_state.otp_sent = False
                st.rerun()
        
        st.write("")
        if st.button("🚪 Reset / Clear Details", width='stretch', key="reg_logout_postsend"):
            logout_action()
    
    st.stop()

sub_community, villa = st.session_state.sub_community, st.session_state.villa
verified_user_email = st.session_state.get("verified_email", "Verified")
st.success(f"✅ Logged in as: **{sub_community} - Villa {villa}** (`{verified_user_email}`)")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["📅 Availability", "➕ Book", "📋 My Bookings", "🛠️ Court Maint.", "📜 Activity Log"])

with tab1:
    st.subheader("Court Availability")
    date_options = [f"{d.strftime('%Y-%m-%d')} ({d.strftime('%A')})" for d in get_next_14_days()]
    selected_date_full = st.selectbox("Select Date:", date_options)
    selected_date = selected_date_full.split(" (")[0]
    bookings_with_details = get_bookings_for_day_with_details(selected_date)
    data = {}
    for h in get_start_hours_for_date(selected_date):
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
    
    curr_auth = st.query_params.get("auth")
    full_url = f"/?view=full&auth={curr_auth}" if curr_auth else "/?view=full"
    st.link_button("🌐 View Full 14-Day Schedule (Full Page)", url=full_url)
    
    st.divider()
    st.markdown("### ⚡ Quick Book")
    q_col1, q_col2, q_col3, q_col4 = st.columns([2, 2, 2, 2])
    with q_col1: q_court = st.selectbox("Select Court", options=courts, key="q_court_select")
    with q_col2:
        q_free_hours = get_available_hours(q_court, selected_date)
        if not q_free_hours:
            st.warning("No slots available"); q_time = None
        else:
            q_time_options = [f"{h:02d}:00" for h in q_free_hours]
            q_time = st.selectbox("Select Time", options=q_time_options, key="q_time_select")
    with q_col3:
        st.write(""); st.write("") 
        q_label = "Book for 2 hours"
        q_disabled = False
        if q_time:
            q_start_h = int(q_time.split(":")[0])
            q_next_h = q_start_h + 1
            q_valid_hours = get_start_hours_for_date(selected_date)
            
            if q_next_h not in q_valid_hours or is_slot_booked(q_court, selected_date, q_next_h) or is_slot_in_past(selected_date, q_next_h):
                q_disabled = True
                q_label = "2nd slot unavailable"
            else:
                q_label = f"Book for 2 hours ({q_start_h:02d}:00 to {q_start_h+2:02d}:00)"
        
        q_2_hours = st.checkbox(q_label, key="q_2_hours_check", disabled=q_disabled)
        q_slots = 2 if q_2_hours else 1
    with q_col4:
        st.write(""); st.write("") 
        if st.button("🚀 Book Now", key="q_book_btn", width='stretch'):
            if q_time:
                active_count = get_active_bookings_count(villa, sub_community)
                daily_count = get_daily_bookings_count(villa, sub_community, selected_date)
                
                start_h = int(q_time.split(":")[0])
                slots_to_book = list(range(start_h, start_h + q_slots))
                valid_hours = get_start_hours_for_date(selected_date)
                
                unavailable = []
                for h in slots_to_book:
                    if h not in valid_hours or is_slot_booked(q_court, selected_date, h) or is_slot_in_past(selected_date, h):
                        unavailable.append(f"{h:02d}:00")
                
                if unavailable:
                    st.error(f"Slot(s) {', '.join(unavailable)} are unavailable.")
                elif active_count + q_slots > 6:
                    st.error(f"Limit Reached (Max 6 active). You can book {max(0, 6-active_count)} more.")
                    add_log("Access Denied", f"{sub_community} Villa {villa} reached active booking limit (6)")
                elif daily_count + q_slots > 2:
                    st.error(f"Daily Limit Reached (Max 2 per day). You can book {max(0, 2-daily_count)} more today.")
                    add_log("Access Denied", f"{sub_community} Villa {villa} reached daily limit (2) for {selected_date}")
                else:
                    success = True
                    booked_slots = []
                    for h in slots_to_book:
                        if book_slot(villa, sub_community, q_court, selected_date, h):
                            booked_slots.append(h)
                        else:
                            success = False
                            break
                    
                    if success:
                        st.balloons()
                        st.success(f"Booked {q_slots} slot(s) for {q_court} starting at {q_time}")
                        time.sleep(2)
                        st.rerun()
                    else:
                        st.error("❌ One or more slots were taken! Please refresh.")

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
        st.write("**Weekly Intensity Heatmap**")
        heatmap_data = usage_data.groupby(['day_of_week', 'start_hour']).size().unstack(fill_value=0)
        heatmap_data = heatmap_data.reindex(days_order).fillna(0)
        try:
            st.dataframe(heatmap_data.style.background_gradient(cmap="YlGnBu"), width="stretch")
        except Exception:
            st.dataframe(heatmap_data, width="stretch")
    else: st.info("Charts will appear here once more bookings are made!")

    st.divider()
    st.subheader("🔍 Booking Lookup")
    if villas_active:
        look_villa = st.selectbox("Select Villa to see details:", options=["-- Select --"] + villas_active)
        if look_villa != "-- Select --":
            active_list = get_active_bookings_for_villa_display(look_villa)
            if active_list: st.selectbox("Active bookings for this villa:", options=active_list)
            else: st.write("No active bookings found for this villa.")

    st.divider()
    if st.button("🚪 Logout / Change Villa", width='stretch', key="tab1_logout"):
        logout_action()

with tab2:
    st.subheader("Book a New Slot")
    
    date_options = [f"{d.strftime('%Y-%m-%d')} ({d.strftime('%A')})" for d in get_next_14_days()]
    selected_date_full = st.selectbox("Date:", date_options)
    date_choice = selected_date_full.split(" (")[0]
    
    if date_choice <= "2026-03-22":
        timing_msg = "7AM to 12AM slots."
    else:
        timing_msg = "7AM to 10PM slots."
    
    st.info(f"App allows 6 Active bookings spanning 14 days, A maximum of 2 active bookings per day. Current date choice timing: **{timing_msg}**")
    
    court_choice = st.selectbox("Court:", courts)
    free_hours = get_available_hours(court_choice, date_choice)
    if not free_hours:
        st.warning(f"😔 Sorry, no slots available for {court_choice} on {date_choice}."); time_choice = None
    else:
        time_options = [f"{h:02d}:00 - {h+1:02d}:00" for h in free_hours]
        time_choice = st.selectbox("Time Slot:", time_options)
    
    tab2_label = "Book for 2 hours"
    tab2_disabled = False
    if time_choice:
        t2_start_h = int(time_choice.split(":")[0])
        t2_next_h = t2_start_h + 1
        t2_valid_hours = get_start_hours_for_date(date_choice)
        
        if t2_next_h not in t2_valid_hours or is_slot_booked(court_choice, date_choice, t2_next_h) or is_slot_in_past(date_choice, t2_next_h):
            tab2_disabled = True
            tab2_label = "2nd slot unavailable"
        else:
            tab2_label = f"Book for 2 hours ({t2_start_h:02d}:00 to {t2_start_h+2:02d}:00)"
            
    slots_2_hours = st.checkbox(tab2_label, key="tab2_slots_2_hours", disabled=tab2_disabled)
    slots_choice = 2 if slots_2_hours else 1

    active_count = get_active_bookings_count(villa, sub_community)
    daily_count = get_daily_bookings_count(villa, sub_community, date_choice)
    col_status1, col_status2 = st.columns(2)
    with col_status1: st.info(f"Total active bookings: **{active_count} / 6**")
    with col_status2: st.info(f"Bookings for {date_choice}: **{daily_count} / 2**")
    
    if st.button("Book This Slot", type="primary"):
        active_count_latest = get_active_bookings_count(villa, sub_community)
        daily_count_latest = get_daily_bookings_count(villa, sub_community, date_choice)
        
        if not time_choice:
            st.error("Please select an available time slot.")
        else:
            start_h = int(time_choice.split(":")[0])
            slots_to_book = list(range(start_h, start_h + slots_choice))
            valid_hours = get_start_hours_for_date(date_choice)

            unavailable = []
            for h in slots_to_book:
                if h not in valid_hours or is_slot_booked(court_choice, date_choice, h) or is_slot_in_past(date_choice, h):
                    unavailable.append(f"{h:02d}:00")

            if unavailable:
                st.error(f"Slot(s) {', '.join(unavailable)} are unavailable.")
            elif active_count_latest + slots_choice > 6: 
                st.error(f"🚫 Overall limit reached. You can book {max(0, 6-active_count_latest)} more slots.")
                add_log("Access Denied", f"{sub_community} Villa {villa} reached active booking limit (6)")
            elif daily_count_latest + slots_choice > 2:
                st.error(f"🚫 Daily limit reached. You can book {max(0, 2-daily_count_latest)} more on {date_choice}.")
                add_log("Access Denied", f"{sub_community} Villa {villa} reached daily limit (2) for {date_choice}")
            else:
                success = True
                booked_slots = []
                for h in slots_to_book:
                    if book_slot(villa, sub_community, court_choice, date_choice, h):
                        booked_slots.append(h)
                    else:
                        success = False
                        break
                
                if success:
                    st.balloons()
                    st.success(f"✅ SUCCESS! {court_choice} booked for {date_choice} starting at {start_h:02d}:00 ({slots_choice} slot(s))")
                    time.sleep(2.5) 
                    st.rerun()
                else:
                    st.error("❌ One or more slots were taken! Please refresh.")

with tab3:
    st.subheader("📋 My Bookings")
    court_locations = {
        "Mira 2": "https://maps.google.com/?q=25.003702,55.306740",
        "Mira 4": "https://maps.google.com/?q=25.010338,55.305798",
        "Mira 5A": "https://maps.google.com/?q=25.007513,55.303432",
        "Mira 5B": "https://maps.google.com/?q=25.007513,55.303432",
        "Mira Oasis 1": "https://maps.google.com/?q=25.010536,55.296654",
        "Mira Oasis 2": "https://maps.google.com/?q=25.016439,55.298626",
        "Mira Oasis 3A": "https://maps.google.com/?q=25.012520,55.298313",
        "Mira Oasis 3B": "https://maps.google.com/?q=25.012520,55.298313",
        "Mira Oasis 3C": "https://maps.google.com/?q=25.015327,55.301998"
    }
    if sub_community == "Mira 1" and villa in ["229", "231", "233"]:
        my_b = []
        for v_num in ["229", "231", "233"]:
            vb = get_user_bookings(v_num, "Mira 1")
            for b in vb: b['orig_v'] = v_num; b['orig_sc'] = "Mira 1"
            my_b.extend(vb)
        limit_val = 6
    else:
        my_b = get_user_bookings(villa, sub_community)
        for b in my_b: b['orig_v'] = villa; b['orig_sc'] = sub_community
        limit_val = 6

    today_str = get_today().strftime('%Y-%m-%d')
    total_active = len(my_b)
    today_bookings = len([b for b in my_b if b['date'] == today_str])

    col_sum1, col_sum2 = st.columns(2)
    with col_sum1:
        st.metric("Total Active Bookings", f"{total_active} / {limit_val}")
    with col_sum2:
        st.metric("Today's Bookings", f"{today_bookings} / 2")
    st.divider()

    if not my_b: st.info("You have no active bookings.")
    else:
        df_my_b = pd.DataFrame(my_b).sort_values(['date', 'court', 'start_hour'])
        merged_bookings = []
        if not df_my_b.empty:
            current_booking = None
            for _, row in df_my_b.iterrows():
                if current_booking is None:
                    current_booking = {'court': row['court'], 'date': row['date'], 'start_hours': [row['start_hour']], 'ids': [row['id']], 'v': row['orig_v'], 'sc': row['orig_sc']}
                else:
                    if (row['date'] == current_booking['date'] and row['court'] == current_booking['court'] and row['orig_v'] == current_booking['v'] and row['orig_sc'] == current_booking['sc'] and row['start_hour'] == max(current_booking['start_hours']) + 1):
                        current_booking['start_hours'].append(row['start_hour']); current_booking['ids'].append(row['id'])
                    else:
                        merged_bookings.append(current_booking)
                        current_booking = {'court': row['court'], 'date': row['date'], 'start_hours': [row['start_hour']], 'ids': [row['id']], 'v': row['orig_v'], 'sc': row['orig_sc']}
            merged_bookings.append(current_booking)

        for i, b in enumerate(merged_bookings):
            b_date = datetime.strptime(b['date'], '%Y-%m-%d')
            day_name = b_date.strftime('%A')
            formatted_date = b_date.strftime('%b %d, %Y')
            
            start_time = min(b['start_hours'])
            end_time = max(b['start_hours']) + 1
            time_display = f"{start_time:02d}:00 - {end_time:02d}:00"
            
            id_list = sorted(b['ids'])
            id_display = f"#{id_list[0]}" if len(id_list) == 1 else f"#{id_list[0]}-{id_list[-1]}"
            map_url = court_locations.get(b['court'], "#")
            
            with st.container():
                st.markdown(f"""
                    <div style="
                        background-color: #0d5384; 
                        padding: 18px; 
                        border-radius: 12px 12px 0px 0px; 
                        border-left: 6px solid #4CAF50; 
                        color: white;
                        box-shadow: 0px 4px 10px rgba(0,0,0,0.4);
                        margin-top: 15px;
                    ">
                        <div style="font-family: 'Audiowide'; color: rgba(255,255,255,0.6); font-size: 0.8rem; margin-bottom: 5px;">
                            BOOKING CONF.: {id_display}
                        </div>
                        <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 2px;">
                            <span style="font-family: 'Audiowide'; font-size: 1.3rem; color: #ccff00;">🎾 {b['court']}</span>
                            <span style="font-size: 1.1rem; font-weight: bold; color: white;">{b['sc']} - {b['v']}</span>
                        </div>
                        <div style="margin-bottom: 10px; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 8px;">
                            <a href="{map_url}" target="_blank" style="color: #ccff00; text-decoration: none; font-size: 0.9rem; font-weight: bold;">
                                📍 View Location Pin
                            </a>
                        </div>
                        <div>
                            <span style="font-size: 1.0rem; opacity: 0.9;">{day_name}, {formatted_date}</span>
                        </div>
                        <div style="font-size: 1.5rem; font-weight: bold; margin-top: 5px; font-family: 'Audiowide'; color: white;">
                            ⏰ {time_display}
                        </div>
                    </div>
                """, unsafe_allow_html=True)
                
                if st.button(f"❌ Cancel Booking {id_display}", key=f"cancel_{i}", width='stretch'):
                    for bid in b['ids']: delete_booking(bid, b['v'], b['sc'])
                    st.success(f"Successfully cancelled booking {id_display}")
                    time.sleep(1.5); st.rerun()
                st.markdown('<div style="margin-bottom: 25px;"></div>', unsafe_allow_html=True)
        
        st.divider()
        if st.button("🚪 Logout / Change Villa", width='stretch'):
            logout_action()

with tab4:
    import base64
    st.subheader("🛠️ Court Maintenance")
    st.markdown(f"""
    <div style="background-color:#0d5384; padding:25px; border-radius:15px; border-left: 8px solid #ccff00;">
        <h2 style="color:#ccff00; margin-top:0;">Power in Numbers</h2>
        <p style="font-size:1.1em; line-height:1.6;">
            This hub centralizes every court issue 
            to facilitate <b>mass maintenance requests</b>. By reporting collectively, we ensure 
            our concerns are impossible to ignore and prioritized for repair.
        </p>
        <hr style="border:0.5px solid #052134; margin:15px 0;">
        <p style="font-style:italic; font-size:0.95em;">
            <b>Community Verified:</b> Once a repair is completed, any resident can mark the issue 
            as <span style="color:#ccff00; font-weight:bold;">FIXED</span> to maintain real-time accuracy for the neighborhood.
        </p>
    </div>
    """, unsafe_allow_html=True)

    with st.expander("📝 Report a New Issue", expanded=False):
        m_court = st.selectbox("Select Court", options=courts, key="maint_court")
        m_desc = st.text_area("Issue Description", placeholder="Please describe the issue in detail...")
        
        m_photo = st.file_uploader("Upload a photo of the issue", type=["png", "jpg", "jpeg"])
        m_image_b64 = None
        if m_photo:
            try:
                img = Image.open(m_photo)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                
                max_res = 640
                if img.width > max_res or img.height > max_res:
                    img.thumbnail((max_res, max_res))
                
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=40)
                m_image_b64 = base64.b64encode(buffer.getvalue()).decode()
            except Exception as e:
                st.error(f"Error processing image: {str(e)}")
                
        if st.button("Submit Report", type="primary", width='stretch'):
            if not m_desc:
                st.error("Please provide a description.")
            else:
                try:
                    now_ts = get_utc_plus_4().isoformat()
                    run_query(supabase.table("court_maintenance").insert({
                        "created_at": now_ts,
                        "court_name": m_court,
                        "description": m_desc,
                        "image_url": m_image_b64,
                        "reported_by": f"{sub_community} Villa {villa}",
                        "is_fixed": False
                    }))
                    add_log("Maintenance Reported", f"Issue reported for {m_court} by {sub_community} Villa {villa}")
                    st.cache_data.clear()
                    st.success("✅ Maintenance report submitted successfully!")
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to submit report: {str(e)}")

    st.divider()
    
    st.markdown("### 📞 Contact Resources")
    c_col1, c_col2, c_col3 = st.columns(3)
    with c_col1:
        st.markdown(f'<div style="background: rgba(255,255,255,0.05); padding: 15px; border-radius: 10px; text-align: center; border: 1px solid rgba(255,255,255,0.1);">'
                    f'<div style="font-size: 20px;">📧 Email</div>'
                    f'<div style="font-size: 14px; margin-top: 5px;"><a href="mailto:support@dubaiholdingcm.ae" style="color: #4CAF50; text-decoration: none;">support@dubaiholdingcm.ae</a></div>'
                    f'</div>', unsafe_allow_html=True)
    with c_col2:
        st.markdown(f'<div style="background: rgba(255,255,255,0.05); padding: 15px; border-radius: 10px; text-align: center; border: 1px solid rgba(255,255,255,0.1);">'
                    f'<div style="font-size: 20px;">💬 WhatsApp</div>'
                    f'<div style="font-size: 14px; margin-top: 5px;"><a href="https://wa.me/971562069871" target="_blank" style="color: #4CAF50; text-decoration: none;">+971 56 206 9871</a></div>'
                    f'</div>', unsafe_allow_html=True)
    with c_col3:
        st.markdown(f'<div style="background: rgba(255,255,255,0.05); padding: 15px; border-radius: 10px; text-align: center; border: 1px solid rgba(255,255,255,0.1);">'
                    f'<div style="font-size: 20px;">🌐 Web</div>'
                    f'<div style="font-size: 14px; margin-top: 5px;"><a href="https://dubaiholdingcommunities.ae" target="_blank" style="color: #4CAF50; text-decoration: none;">dubaiholdingcommunities.ae</a></div>'
                    f'</div>', unsafe_allow_html=True)

    st.divider()
    
    st.markdown("### 📋 Court Issues")
    maint_data = get_maintenance_data()
    if maint_data and maint_data.data:
        open_issues = [item for item in maint_data.data if not item.get('is_fixed')]
        if open_issues:
            phone_number = "+971562069871"
            issue_list = "\n".join([f"- **{item['court_name']}**: {item['description']}" for item in open_issues])
            bulk_message = f"Hello, Please have the maintenance team urgently attend to the following court issues:\n\n{issue_list}"
            encoded_bulk_msg = urllib.parse.quote(bulk_message)
            whatsapp_bulk_url = f"https://wa.me/{phone_number}?text={encoded_bulk_msg}"

            st.markdown(f'''
                <a href="{whatsapp_bulk_url}" target="_blank" style="
                    display: block; 
                    text-align: center; 
                    padding: 12px; 
                    background-color: #28a745; 
                    color: white; 
                    border-radius: 8px; 
                    text-decoration: none; 
                    font-family: 'Audiowide', cursive; 
                    margin-bottom: 20px;
                    border: 2px solid #ccff00;
                ">
                    📢 Share All Open Issues to Mira team via WhatsApp
                </a>
            ''', unsafe_allow_html=True)

        for item in maint_data.data:
            with st.container(border=True):
                l_col1, l_col2, l_col3 = st.columns([1, 2, 1])
                with l_col1:
                    if item.get("image_url"):
                        st.image(f"data:image/png;base64,{item['image_url']}", width='stretch')
                    else:
                        st.info("No Photo")
                with l_col2:
                    st.markdown(f"**{item['court_name']}**")
                    created_dt = datetime.fromisoformat(item['created_at'].replace('Z', '+00:00'))
                    st.caption(f"📅 {created_dt.strftime('%b %d, %Y %I:%M %p')}")
                    st.write(item['description'])
                with l_col3:
                    if item['is_fixed']:
                        fixed_dt = datetime.fromisoformat(item['fixed_at'].replace('Z', '+00:00'))
                        st.success(f"✅ Locked/Fixed\n({fixed_dt.strftime('%b %d')})")
                    else:
                        st.warning("⚠️ Open")
                        if st.button("Fixed", key=f"fix_{item['id']}", width='stretch'):
                            now_ts = get_utc_plus_4().isoformat()
                            run_query(supabase.table("court_maintenance").update({
                                "is_fixed": True,
                                "fixed_at": now_ts
                            }).eq("id", item['id']))
                            st.cache_data.clear()
                            st.rerun()
    else:
        st.info("No maintenance issues reported yet.")

    st.divider()

    # --- ADMIN CONTROLS IN TAB 4 ---
    st.markdown("### 🛠️ Admin Maintenance Controls")
    admin_maint_pwd = st.text_input("Enter Admin Password to Unlock Controls", type="password", key="tab4_admin_pass")
    
    if admin_maint_pwd:
        if admin_maint_pwd == st.secrets.get("ADMIN_PASSWORD", "admin123"):
            st.success("Admin Access Granted")
            with st.expander("🔑 Admin Resident Bypass (Authorize & Switch Active Resident)", expanded=True):
                st.caption("Directly authorize a resident email without OTP and immediately switch this session to them.")
                b_col1, b_col2 = st.columns(2)
                with b_col1:
                    bypass_sub = st.selectbox("Sub-Community", options=sub_community_list, key="tab4_bypass_sub")
                with b_col2:
                    bypass_villa_raw = st.text_input("Villa Number", key="tab4_bypass_villa").strip()
                    bypass_villa = "".join(filter(str.isdigit, bypass_villa_raw))
                    
                bypass_email = st.text_input("Resident Email Address", placeholder="resident@example.com", key="tab4_bypass_email").strip().lower()

                if st.button("Authorize & Switch Session to Resident", type="primary", width='stretch', key="tab4_bypass_btn"):
                    if not bypass_sub or not bypass_villa or not bypass_email or "@" not in bypass_email:
                        st.error("Please specify a valid Sub-Community, Villa, and Email Address.")
                    else:
                        now_ts = get_utc_plus_4().isoformat()
                        existing = get_existing_claim(bypass_sub, bypass_villa, bypass_email)
                        if not existing:
                            run_query(supabase.table("villa_claims").insert({
                                "sub_community": bypass_sub,
                                "villa": bypass_villa,
                                "email": bypass_email,
                                "fingerprint": "admin_bypass_grant",
                                "status": "approved",
                                "verified_at": now_ts
                            }))
                            add_log("Villa Claim", f"Admin directly authorized {bypass_sub} Villa {bypass_villa} for {bypass_email}")
                        else:
                            run_query(supabase.table("villa_claims").update({
                                "verified_at": now_ts,
                                "status": "approved"
                            }).eq("id", existing["id"]))

                        fallback_choice = f"{bypass_sub}-{bypass_villa}"
                        claim_bundle = f"{bypass_sub}::{bypass_villa}"
                        st_javascript(f"""
                            localStorage.setItem('court_villa_lock', '{fallback_choice}');
                            localStorage.setItem('court_verified_email', '{bypass_email}');
                            localStorage.setItem('verified_claim_info', '{claim_bundle}');
                        """)

                        st.session_state.sub_community = bypass_sub
                        st.session_state.villa = bypass_villa
                        st.session_state.verified_email = bypass_email
                        st.session_state.authenticated = True
                        st.query_params["auth"] = encode_auth_token(bypass_sub, bypass_villa, bypass_email)
                        
                        st.success(f"Granted access! Switched active session to {bypass_sub} Villa {bypass_villa} ({bypass_email}).")
                        time.sleep(1.0)
                        st.rerun()
        else:
            st.error("Incorrect Password")

with tab5:
    st.subheader("Community Activity Log")
    st.caption("Timezone: UTC+4")
    
    admin_pass_val = st.session_state.get("log_admin_pass", "")
    is_admin = admin_pass_val == st.secrets.get("ADMIN_PASSWORD", "admin123")

    logs = get_logs_last_14_days()
    if logs:
        log_df = pd.DataFrame(logs, columns=["timestamp", "event_type", "Fingerprint", "details"])
        
        filters = (
            (log_df['event_type'] != "Debug") &
            (log_df['event_type'] != "System Maintenance") &
            (~log_df['details'].str.contains("System-Synced", case=False, na=False))
        )
        
        if not is_admin:
            filters &= (log_df['event_type'] != "Limit Enforcement")
            
        display_df = log_df[filters].copy()        
        display_df['details'] = display_df['details'].str.replace(r'⟦FP:.*?⟧⟦IP:.*?⟧ ', '', regex=True)

        # Mask email addresses for public view (unmasked only if admin is logged in)
        if not is_admin:
            display_df['details'] = display_df['details'].apply(mask_emails_in_text)

        cols = ['timestamp', 'event_type', 'details']
        display_df['timestamp'] = pd.to_datetime(display_df['timestamp'], format='ISO8601').dt.strftime('%b %d, %H:%M')
        
        def style_rows(row):
            styles = [''] * len(row)
            if row.event_type in ["Booking Created", "Villa Claim"]: styles[1] = 'background-color: #d4edda; color: #155724; font-weight: bold;'
            elif row.event_type in ["Booking Deleted", "Booking Cancelled", "Villa Claim Removed"]: styles[1] = 'background-color: #f8d7da; color: #721c24; font-weight: bold;'
            elif row.event_type in ["Access Denied", "Claim Held for Review"]: styles[1] = 'background-color: #ffcc00; color: black; font-weight: bold;'
            return styles
            
        st.dataframe(display_df[cols].style.apply(style_rows, axis=1), hide_index=True, width="stretch")
    else: st.info("No activity.")

    st.divider()
    st.subheader("🛠️ Admin Tools")
    admin_pass = st.text_input("Admin Password", type="password", key="log_admin_pass")
    
    if is_admin:
        st.success("Admin Access Granted")
        
        st.markdown("### 🛡️ Villa Claims & Verification Management")
        
        all_claimed = get_all_claimed_villas()
        col_c1, col_c2 = st.columns([2, 1])
        with col_c1:
            claim_inspect_villa = st.selectbox(
                "Select Claimed Villa to Inspect / Reset",
                options=["-- Select --"] + all_claimed,
                key="admin_claimed_villa_select"
            )
        
        if claim_inspect_villa != "-- Select --":
            try:
                c_sub, c_villa = claim_inspect_villa.split(" - ")
                claims = get_claims_for_villa(c_sub, c_villa)
                
                if claims:
                    st.write(f"Active Verified Emails for **{claim_inspect_villa}** ({len(claims)} / 2):")
                    for claim in claims:
                        c_box1, c_box2 = st.columns([3, 1])
                        with c_box1:
                            v_time = claim.get('verified_at', 'Unverified')
                            if v_time and v_time != 'Unverified':
                                try:
                                    v_dt = datetime.fromisoformat(v_time.replace('Z', '+00:00'))
                                    v_time = v_dt.strftime('%b %d, %Y %I:%M %p')
                                except:
                                    pass
                            fp_sub = f" | *Device UUID:* `{claim['fingerprint'][:8]}...`" if claim.get('fingerprint') else ""
                            st.info(f"📧 **{claim['email']}**  \n*Status:* `{claim['status']}` | *Verified:* {v_time}{fp_sub}")
                        with c_box2:
                            st.write("")
                            if st.button(f"🔓 Clear Cooldown & Release", key=f"del_claim_{claim['id']}", type="secondary", width="stretch"):
                                run_query(supabase.table("villa_claims").delete().eq("id", claim['id']))
                                add_log("Villa Claim Removed", f"Admin cleared cooldown and released claim for {c_sub} Villa {c_villa} ({claim['email']})")
                                st.success(f"Released {claim['email']} and cleared 72h cooldown for {claim_inspect_villa}!")
                                time.sleep(1.2)
                                st.rerun()
                else:
                    st.info("No active claims found for this villa.")
            except Exception as e:
                st.error(f"Error inspecting claims: {str(e)}")

        with st.expander("➕ Manually Add / Authorize an Email Claim (Without OTP)"):
            st.caption("Useful for edge cases or assisting residents who cannot receive the OTP code.")
            c_man1, c_man2 = st.columns(2)
            with c_man1:
                man_sub = st.selectbox("Sub-Community", options=sub_community_list, key="admin_man_sub")
            with c_man2:
                man_villa = st.text_input("Villa Number", key="admin_man_villa").strip()
            man_email = st.text_input("Resident Email Address", key="admin_man_email").strip().lower()
            
            if st.button("Authorize Claim Now", type="primary", key="admin_auth_claim_btn"):
                if not man_villa or not man_email or "@" not in man_email:
                    st.error("Please provide valid villa and email details.")
                else:
                    curr_c = get_villa_claims_count(man_sub, man_villa)
                    email_v_count = get_email_claimed_villas_count(man_email)
                    if curr_c >= 2:
                        st.error(f"Cannot add: {man_sub} Villa {man_villa} already has 2 verified claims.")
                    elif email_v_count >= 3:
                        st.error(f"Cannot add: {man_email} already holds claims for 3 villas (maximum cap reached).")
                    else:
                        now_ts = get_utc_plus_4().isoformat()
                        run_query(supabase.table("villa_claims").insert({
                            "sub_community": man_sub,
                            "villa": man_villa,
                            "email": man_email,
                            "fingerprint": "admin_manual_grant",
                            "status": "approved",
                            "verified_at": now_ts
                        }))
                        add_log("Villa Claim", f"Admin manually authorized {man_sub} Villa {man_villa} for {man_email}")
                        st.success(f"Claim created for {man_sub} Villa {man_villa} ({man_email})!")
                        time.sleep(1.5)
                        st.rerun()

        st.divider()
        st.markdown("### 🏘️ Villa Booking Management")
        
        all_villas = get_all_villas_with_any_bookings()
        selected_villa = st.selectbox("Select Villa to Manage", options=["-- Select --"] + all_villas, key="admin_manage_villa")
        
        if selected_villa != "-- Select --":
            try:
                sub_comm, villa_num = selected_villa.split(" - ")
                bookings = get_bookings_for_villa(villa_num, sub_comm)
                if bookings:
                    df_bookings = pd.DataFrame(bookings)
                    df_bookings['Time'] = df_bookings['start_hour'].apply(lambda x: f"{x:02d}:00")
                    df_bookings.insert(0, "Select", False)
                    
                    st.write(f"Showing bookings for **{selected_villa}**:")
                    
                    edited_df = st.data_editor(
                        df_bookings[["Select", "id", "date", "Time", "court"]],
                        column_config={
                            "Select": st.column_config.CheckboxColumn(
                                "Delete?",
                                help="Select to delete",
                                default=False,
                            ),
                            "id": "ID",
                            "date": "Date",
                            "Time": "Time",
                            "court": "Court"
                        },
                        disabled=["id", "date", "Time", "court"],
                        hide_index=True,
                        key="admin_booking_editor"
                    )
                    
                    if st.button("Delete Selected Bookings", type="primary"):
                        to_delete = edited_df[edited_df["Select"] == True]
                        if not to_delete.empty:
                            with st.spinner(f"Deleting {len(to_delete)} bookings..."):
                                for _, row in to_delete.iterrows():
                                    delete_booking(row['id'], villa_num, sub_comm)
                            st.success(f"Successfully deleted {len(to_delete)} bookings for {selected_villa}.")
                            time.sleep(1.5)
                            st.rerun()
                        else:
                            st.warning("Please select at least one booking to delete.")
                else:
                    st.info(f"No bookings found for {selected_villa}.")
            except Exception as e:
                st.error(f"Error loading bookings: {str(e)}")

    elif admin_pass:
        st.error("Incorrect Password")

    st.divider()
    st.subheader("💾 Data Backup")
    def get_zip_data():
        try:
            b_data = []
            chunk_size = 1000
            offset = 0
            while True:
                res = run_query(supabase.table("bookings").select("*").range(offset, offset + chunk_size - 1))
                if not res or res.data is None: break
                b_data.extend(res.data)
                if len(res.data) < chunk_size: break
                offset += chunk_size
                
            l_data = []
            offset = 0
            while True:
                res = run_query(supabase.table("logs").select("*").range(offset, offset + chunk_size - 1).order("timestamp", desc=True))
                if not res or res.data is None: break
                l_data.extend(res.data)
                if len(res.data) < chunk_size: break
                offset += chunk_size

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as vz:
                vz.writestr(f"bookings_{get_today()}.csv", pd.DataFrame(b_data).to_csv(index=False))
                vz.writestr(f"logs_{get_today()}.csv", pd.DataFrame(l_data).to_csv(index=False))
            return buf.getvalue()
        except Exception as e:
            st.error(f"Backup Error: {str(e)}")
            return None

    if st.button("Generate Backup Link"):
        data = get_zip_data()
        if data: st.download_button(label="Click here to Download ZIP", data=data, file_name=f"court_booking_backup_{get_today()}.zip", mime="application/zip")
        else: st.error("Failed to fetch data for backup.")

col1, col2 = st.columns([1, 5])
with col1: st.markdown(f'<img src="https://raw.githubusercontent.com/mahadevbk/courtbooking/main/qr-code.miracourtbooking.streamlit.app.png" height="100">', unsafe_allow_html=True)
with col2: st.markdown("""
    <div style='background-color: #0d5384; padding: 1rem; border-left: 5px solid #fff500; border-radius: 0.5rem; color: white;'>
    Built with ❤️ using <a href='https://streamlit.io/' style='color: #ccff00;'>Streamlit</a> — free and open source.
    <a href='https://devs-scripts.streamlit.app/' style='color: #ccff00;'>Other Scripts by dev</a> on Streamlit.
    </div>
    """, unsafe_allow_html=True)
