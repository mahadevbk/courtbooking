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
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from postgrest.exceptions import APIError 
from PIL import Image, ImageDraw, ImageFont
from streamlit_javascript import st_javascript
import streamlit.components.v1 as components
import urllib.parse
import requests

# Set page configuration to wide mode by default
st.set_page_config(
    page_title="Mira Court Booking",
    page_icon="🎾",
    layout="wide",
)

# ==========================================
# --- DONOR NAMES & TICKER (LIVE FROM GOOGLE SHEET) ---
# ==========================================
DONOR_SHEET_ID = "1dKj5XkH87bdPmhXc-1inrumqXVBje8pXsl-_llrefYQ"
DONOR_SHEET_CSV_URL = f"https://docs.google.com/spreadsheets/d/{DONOR_SHEET_ID}/export?format=csv&gid=0"

_FALLBACK_DONOR_NAMES = [
    "Abhisek", "Adam", "Adebayo", "Arlan", "Alesia", "Ameen", "Angelo", "Carlos", "Charbel", "Dev", "Elie",
    "Farheen", "Francois", "Goncalo", "Hatem", "Hana", "Harith", "Hisham", "Katya", "Khaled", "Leina", "Marko", "Mei",
    "Melissa", "Mustafa", "Nick", "Nikki", "Rena", "Ricardo", "Riin", "Saket", "SAS", "Sheila", "Sofia", "Timo", "Vik", "Yousef",
]

MAX_ACTIVE_BOOKINGS_DEFAULT = 6
MAX_ACTIVE_BOOKINGS_DONOR = 8

@st.cache_data(ttl=600, show_spinner=False)
def get_donor_data():
    try:
        resp = requests.get(DONOR_SHEET_CSV_URL, timeout=10)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        df.columns = [str(c).strip() for c in df.columns]
        if df.shape[1] < 1:
            raise ValueError("Donor sheet has no columns")

        name_col = df.columns[0]
        names = [str(n).strip() for n in df[name_col].dropna().tolist() if str(n).strip()]
        if not names:
            raise ValueError("Donor sheet has no names")

        donor_villas = set()
        if df.shape[1] >= 3:
            sub_col, villa_col = df.columns[1], df.columns[2]
            for _, row in df.iterrows():
                sub_val = " ".join(str(row.get(sub_col, "")).lower().split())
                villa_val = str(row.get(villa_col, "")).strip()
                if villa_val.endswith(".0"):
                    villa_val = villa_val[:-2]
                if sub_val and villa_val and sub_val != "nan" and villa_val.lower() != "nan":
                    donor_villas.add((sub_val, villa_val))

        return names, donor_villas
    except Exception:
        return list(_FALLBACK_DONOR_NAMES), set()

DONOR_NAMES, DONOR_VILLAS = get_donor_data()

def is_donor_villa(sub_community, villa):
    norm_sub = " ".join(str(sub_community).lower().split())
    norm_villa = str(villa).strip()
    return (norm_sub, norm_villa) in DONOR_VILLAS

def get_active_booking_limit(sub_community, villa):
    return MAX_ACTIVE_BOOKINGS_DONOR if is_donor_villa(sub_community, villa) else MAX_ACTIVE_BOOKINGS_DEFAULT

def render_donor_legend_banner():
    st.markdown(
        """<style>
@keyframes legend-gold-flow {
    0%   { background-position: 0% 50%; }
    50%  { background-position: 100% 50%; }
    100% { background-position: 0% 50%; }
}
@keyframes legend-shimmer-sweep {
    0%   { transform: translateX(-120%) skewX(-20deg); }
    100% { transform: translateX(220%) skewX(-20deg); }
}
@keyframes legend-glow-pulse {
    0%, 100% { box-shadow: 0 0 12px rgba(255, 215, 0, 0.35), 0 4px 18px rgba(0,0,0,0.25); }
    50%      { box-shadow: 0 0 24px rgba(255, 215, 0, 0.65), 0 4px 22px rgba(0,0,0,0.3); }
}
.legend-banner-wrap {
    position: relative;
    overflow: hidden;
    margin: 0.75rem 0 1.25rem 0;
    padding: 1.1rem 1.6rem;
    border-radius: 0.9rem;
    background: linear-gradient(120deg, #7a5a12, #d4af37, #fff2b0, #d4af37, #7a5a12);
    background-size: 300% 300%;
    animation: legend-gold-flow 6s ease-in-out infinite, legend-glow-pulse 2.8s ease-in-out infinite;
    border: 1.5px solid #ffe27a;
    text-align: center;
}
.legend-banner-shimmer {
    position: absolute;
    top: 0; left: 0; height: 100%; width: 35%;
    background: linear-gradient(90deg, rgba(255,255,255,0) 0%, rgba(255,255,255,0.55) 50%, rgba(255,255,255,0) 100%);
    animation: legend-shimmer-sweep 3.2s ease-in-out infinite;
    pointer-events: none;
}
.legend-banner-title {
    position: relative;
    font-size: 1.25rem;
    font-weight: 800;
    letter-spacing: 0.03em;
    color: #3a2a00;
    text-shadow: 0 1px 0 rgba(255,255,255,0.4);
}
.legend-banner-sub {
    position: relative;
    font-size: 0.9rem;
    font-weight: 600;
    color: #4a3a10;
    margin-top: 0.3rem;
}
</style>
<div class="legend-banner-wrap">
    <div class="legend-banner-shimmer"></div>
    <div class="legend-banner-title">✨🏆 Thank You for Being a Mira Legend! 🏆✨</div>
    <div class="legend-banner-sub">Your generosity keeps these courts thriving — enjoy your enhanced 8-booking allowance!</div>
</div>""",
        unsafe_allow_html=True,
    )

def render_donor_ticker(names):
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
    ticker_text = tennis_ball_svg.join(f"<b>{n}</b>" for n in uppercase_names)
    st.markdown(
        f"""<style>
.donor-ticker-wrap {{
    position: fixed; top: 0; left: 0; width: 100%; z-index: 1000000;
    background-color: #0d5384; color: #ccff00; overflow: hidden; white-space: nowrap;
    padding: 6px 0; border-bottom: 2px solid #fff500; box-sizing: border-box;
}}
.donor-ticker-move {{
    display: inline-block; white-space: nowrap; padding-left: 100%;
    font-size: 0.9rem; font-weight: 600; animation: donor-ticker-scroll 30s linear infinite;
}}
.donor-ticker-move b {{ color: #ffffff !important; font-weight: 700 !important; }}
.donor-ticker-move:hover {{ animation-play-state: paused; }}
@keyframes donor-ticker-scroll {{
    0%   {{ transform: translate(0, 0); }}
    100% {{ transform: translate(-100%, 0); }}
}}
.donor-ticker-spacer {{ height: 34px; }}
</style>
<div class="donor-ticker-wrap">
    <div class="donor-ticker-move">
        {tennis_ball_svg} Huge thanks to these legends for their support ! {tennis_ball_svg} {ticker_text} {tennis_ball_svg}
    </div>
</div>
<div class="donor-ticker-spacer"></div>""",
        unsafe_allow_html=True,
    )

render_donor_ticker(DONOR_NAMES)

st.info("🕘 **Update:** New booking slots for the 15th day now open at **9:00 PM** the night before, instead of 12:00 AM — so you don't have to stay up past midnight to grab a spot.")

# --- ICS & SQUARE JPG CARD GENERATOR HELPERS ---
def get_google_calendar_url(court, date_str, start_hours, sub_community, villa):
    sorted_hours = sorted(start_hours)
    start_h = sorted_hours[0]
    end_h = sorted_hours[-1] + 1
    date_clean = date_str.replace("-", "")
    start_time_str = f"{date_clean}T{start_h:02d}0000Z"
    end_time_str = f"{date_clean}T{end_h:02d}0000Z"
    params = {
        "action": "TEMPLATE",
        "text": f"🎾 Tennis at {court}",
        "dates": f"{start_time_str}/{end_time_str}",
        "details": f"Court reservation at {court} for {sub_community} - Villa {villa}.",
        "location": f"{court} Tennis Court, Mira, Dubai, UAE"
    }
    return f"https://calendar.google.com/calendar/render?{urllib.parse.urlencode(params)}"

def _blend_rgb(bg_hex, fg_hex, alpha):
    bg = tuple(int(bg_hex.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
    fg = tuple(int(fg_hex.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
    return tuple(int(bg[i] * (1 - alpha) + fg[i] * alpha) for i in range(3))

@st.cache_resource
def _get_audiowide_font_path():
    import os, tempfile, urllib.request
    font_path = os.path.join(tempfile.gettempdir(), "Audiowide-Regular.ttf")
    if os.path.exists(font_path) and os.path.getsize(font_path) > 1000:
        return font_path
    try:
        url = "https://raw.githubusercontent.com/google/fonts/main/ofl/audiowide/Audiowide-Regular.ttf"
        urllib.request.urlretrieve(url, font_path)
        if os.path.getsize(font_path) > 1000:
            return font_path
    except Exception:
        pass
    return None

def _draw_tennis_icon(draw, x, y, size=15, color="#ccff00", seam="#0d5384"):
    draw.ellipse([x, y, x + size, y + size], fill=color)
    bbox = [x - size * 0.35, y - size * 0.1, x + size * 1.35, y + size * 1.1]
    draw.arc(bbox, start=200, end=340, fill=seam, width=2)
    draw.arc(bbox, start=20, end=160, fill=seam, width=2)

def _draw_clock_icon(draw, x, y, size=20, color="#ffffff"):
    draw.ellipse([x, y, x + size, y + size], outline=color, width=2)
    cx, cy = x + size / 2, y + size / 2
    draw.line([cx, cy, cx, cy - size * 0.32], fill=color, width=2)
    draw.line([cx, cy, cx + size * 0.24, cy + size * 0.14], fill=color, width=2)

def generate_booking_card_jpg(id_display, court, sub_community, villa, formatted_date, time_display):
    width, height = 300, 300
    BG_HEX = "#0d5384"
    image = Image.new("RGB", (width, height), color=BG_HEX)
    draw = ImageDraw.Draw(image)

    draw.rectangle([0, 0, 6, height], fill="#4CAF50")

    audiowide_path = _get_audiowide_font_path()
    try:
        if audiowide_path:
            font_conf = ImageFont.truetype(audiowide_path, 11)
            font_court = ImageFont.truetype(audiowide_path, 17)
            font_time = ImageFont.truetype(audiowide_path, 20)
        else:
            raise IOError("Audiowide unavailable")
    except Exception:
        font_conf = ImageFont.truetype("DejaVuSans-Bold.ttf", 10)
        font_court = ImageFont.truetype("DejaVuSans-Bold.ttf", 16)
        font_time = ImageFont.truetype("DejaVuSans-Bold.ttf", 19)
    try:
        font_res = ImageFont.truetype("DejaVuSans-Bold.ttf", 14)
        font_date = ImageFont.truetype("DejaVuSans.ttf", 13)
    except Exception:
        font_res = font_date = ImageFont.load_default()

    conf_color = _blend_rgb(BG_HEX, "#ffffff", 0.6)
    divider_color = _blend_rgb(BG_HEX, "#ffffff", 0.12)
    date_color = _blend_rgb(BG_HEX, "#ffffff", 0.9)

    pad_x = 24
    right_edge = width - 24

    y = 20
    draw.text((pad_x, y), f"BOOKING CONF.: {id_display}", fill=conf_color, font=font_conf)
    y += 24

    icon_size = 15
    _draw_tennis_icon(draw, pad_x, y + 3, size=icon_size)
    draw.text((pad_x + icon_size + 6, y), court, fill="#ccff00", font=font_court)

    right_text = f"{sub_community} - {villa}"
    r_bbox = draw.textbbox((0, 0), right_text, font=font_res)
    draw.text((right_edge - (r_bbox[2] - r_bbox[0]), y + 3), right_text, fill="#ffffff", font=font_res)
    y += 34

    draw.line([(pad_x, y), (right_edge, y)], fill=divider_color, width=1)
    y += 20

    draw.text((pad_x, y), formatted_date, fill=date_color, font=font_date)
    y += 30

    clock_size = 20
    _draw_clock_icon(draw, pad_x, y + 2, size=clock_size)
    draw.text((pad_x + clock_size + 8, y), time_display, fill="#ffffff", font=font_time)

    time_bbox = draw.textbbox((pad_x + clock_size + 8, y), time_display, font=font_time)
    icon_bottom = y + 2 + clock_size
    content_bottom = max(time_bbox[3], icon_bottom)
    bottom_pad = 20
    final_height = content_bottom + bottom_pad
    image = image.crop((0, 0, width, final_height))

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()

def render_share_or_download_button(jpg_bytes, filename, id_display, key):
    if st.session_state.get("is_mobile_device", False):
        b64_data = base64.b64encode(jpg_bytes).decode()
        html = f"""
        <button id="share_btn_{key}" style="
            width:100%; padding:0.6rem 1rem; margin-top:0.25rem;
            background-color:#0d5384; color:#ffffff;
            border:1px solid rgba(250,250,250,0.3); border-radius:0.5rem;
            font-size:1rem; font-family: 'Source Sans Pro', sans-serif; cursor:pointer;">
            📤 Share Booking Card {id_display}
        </button>
        <script>
        (function() {{
            const b64 = "{b64_data}";
            const filename = "{filename}";
            document.getElementById("share_btn_{key}").addEventListener("click", async function() {{
                try {{
                    const byteChars = atob(b64);
                    const byteNumbers = new Array(byteChars.length);
                    for (let i = 0; i < byteChars.length; i++) {{ byteNumbers[i] = byteChars.charCodeAt(i); }}
                    const byteArray = new Uint8Array(byteNumbers);
                    const file = new File([byteArray], filename, {{ type: "image/jpeg" }});
                    if (navigator.canShare && navigator.canShare({{ files: [file] }})) {{
                        await navigator.share({{ files: [file], title: "Tennis Court Booking" }});
                    }} else {{
                        const url = URL.createObjectURL(file);
                        const a = document.createElement('a');
                        a.href = url; a.download = filename;
                        document.body.appendChild(a); a.click(); document.body.removeChild(a);
                        URL.revokeObjectURL(url);
                    }}
                }} catch (err) {{
                    console.error("Share failed:", err);
                }}
            }});
        }})();
        </script>
        """
        components.html(html, height=52)
    else:
        st.download_button(
            label=f"📥 Download Booking Card {id_display}",
            data=jpg_bytes,
            file_name=filename,
            mime="image/jpeg",
            key=f"download_jpg_{key}"
        )

# --- GMAIL SMTP EMAIL HELPER ---
def send_gmail_smtp(recipient_email, subject, html_content):
    g_user = st.secrets.get("GMAIL_USER", "devkrea@gmail.com")
    g_pass = st.secrets.get("GMAIL_PASSWORD", "").replace(" ", "")
    if not g_pass or not recipient_email or "@" not in recipient_email:
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Mira Court Booking <{g_user}>"
        msg["To"] = recipient_email
        msg.attach(MIMEText(html_content, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(g_user, g_pass)
            server.sendmail(g_user, recipient_email, msg.as_string())
        return True
    except Exception as e:
        print(f"SMTP Email Error: {e}")
        return False

def send_booking_notification(action_type, villa, sub_community, court, date_str, start_hours, recipient_email):
    if not recipient_email or "@" not in recipient_email:
        return
    try:
        sorted_hours = sorted(start_hours)
        start_h = sorted_hours[0]
        end_h = sorted_hours[-1] + 1
        duration = len(sorted_hours)
        time_display = f"{start_h:02d}:00 - {end_h:02d}:00"
        b_date = datetime.strptime(date_str, '%Y-%m-%d')
        formatted_date = b_date.strftime('%A, %b %d, %Y')
        action_word = "Confirmed" if action_type == "created" else "Cancelled"
        color = "#4CAF50" if action_type == "created" else "#c0392b"
        subject = f"{'✅' if action_type == 'created' else '❌'} Booking {action_word}: {court} ({formatted_date})"
        html_content = f"""
        <div style="max-width: 600px; margin: 20px auto; background-color: #ffffff; border-radius: 8px; border: 1px solid #e1e8ed; padding: 20px;">
          <h2 style="color: {color};">Booking {action_word}</h2>
          <p><b>Court:</b> {court}<br><b>Date:</b> {formatted_date}<br><b>Time:</b> {time_display} ({duration} hr)<br><b>Residence:</b> {sub_community} - Villa {villa}</p>
        </div>
        """
        send_gmail_smtp(recipient_email, subject, html_content)
    except Exception as e:
        print(f"Notification error: {e}")

def send_booking_notification_once(action_type, villa, sub_community, court, date_str, start_hours, recipient_email):
    signature = f"{action_type}::{villa}::{sub_community}::{court}::{date_str}::{sorted(start_hours)}::{(recipient_email or '').strip().lower()}"
    sent_signatures = st.session_state.setdefault("sent_booking_email_signatures", set())
    if signature in sent_signatures:
        return
    sent_signatures.add(signature)
    send_booking_notification(action_type, villa, sub_community, court, date_str, start_hours, recipient_email)

def notify_owner_of_coach_booking(coach_name, villa, sub_community, court, date_str, hour, action="booked"):
    owner_res = run_query(supabase.table("villa_claims").select("email").eq("sub_community", sub_community).eq("villa", villa).eq("status", "approved"))
    if owner_res and owner_res.data:
        owner_email = owner_res.data[0]['email']
        formatted_date = datetime.strptime(date_str, '%Y-%m-%d').strftime('%A, %b %d, %Y')
        if action == "booked":
            subject = f"🎾 Coach {coach_name} Booked a Court Using Your Quota"
            html_content = f"<h3>Coach Booking</h3><p>Coach <b>{coach_name}</b> reserved a slot using your allocation.<br><b>Court:</b> {court}<br><b>Date:</b> {formatted_date}<br><b>Time:</b> {hour}:00 - {hour+1}:00</p>"
        else:
            subject = f"🔄 Coach {coach_name} Cancelled a Booking (Quota Returned)"
            html_content = f"<h3>Coach Cancellation</h3><p>Coach <b>{coach_name}</b> cancelled a slot. Your quota is restored.<br><b>Court:</b> {court}<br><b>Date:</b> {formatted_date}<br><b>Time:</b> {hour}:00 - {hour+1}:00</p>"
        send_gmail_smtp(owner_email, subject, html_content)

def send_all_bookings_summary(villa, sub_community, bookings_list, recipient_email):
    if not recipient_email or "@" not in recipient_email or not bookings_list:
        return False
    try:
        items_html = ""
        for b in bookings_list:
            b_date = datetime.strptime(b['date'], '%Y-%m-%d').strftime('%A, %b %d, %Y')
            start_time = min(b['start_hours'])
            end_time = max(b['start_hours']) + 1
            id_display = f"#{b['ids'][0]}" if len(b['ids']) == 1 else f"#{min(b['ids'])}-{max(b['ids'])}"
            items_html += f"<p><b>{b['court']}</b> | {b_date} at {start_time:02d}:00-{end_time:02d}:00 (Ref: {id_display})</p>"
        subject = f"📋 Summary of All Active Court Bookings ({sub_community} Villa {villa})"
        return send_gmail_smtp(recipient_email, subject, f"<div><h3>Active Bookings</h3>{items_html}</div>")
    except Exception:
        return False

# --- DATABASE SETUP ---
@st.cache_resource(ttl=1800)
def init_supabase():
    url: str = st.secrets["SUPABASE_URL"]
    key: str = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase: Client = init_supabase()

@st.cache_data(ttl=3600)
def get_maintenance_data():
    return run_query(supabase.table("court_maintenance").select("*").order("created_at", desc=True))

sub_community_list = [
    "Mira 1", "Mira 2", "Mira 3", "Mira 4", "Mira 5",
    "Mira Oasis 1", "Mira Oasis 2", "Mira Oasis 3"
]

SUB_COMMUNITY_VILLA_LIMITS = {
    "Mira 1": 322, "Mira 2": 334, "Mira 3": 294, "Mira 4": 600,
    "Mira 5": 316, "Mira Oasis 1": 483, "Mira Oasis 2": 427, "Mira Oasis 3": 483
}

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

def get_utc_plus_4():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=4)

def get_today():
    return get_utc_plus_4().date()

def get_window_today():
    now = get_utc_plus_4()
    if now.hour >= 21:
        return now.date() + timedelta(days=1)
    return now.date()

def get_next_14_days():
    today = get_window_today()
    return [today + timedelta(days=i) for i in range(15)]

def run_query(query_method):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return query_method.execute()
        except APIError as e:
            if e.code == "PGRST303":  
                st.cache_resource.clear()
                global supabase
                supabase = init_supabase()
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
        log_entry = {"timestamp": timestamp, "event_type": event_type, "details": details}
        if fingerprint:
            log_entry["fingerprint"] = fingerprint
        supabase.table("logs").insert(log_entry).execute()
    except Exception:
        pass 

def purge_out_of_range_records():
    try:
        claims_res = run_query(supabase.table("villa_claims").select("id, sub_community, villa"))
        if claims_res and claims_res.data:
            for claim in claims_res.data:
                sub = claim.get("sub_community")
                v_str = str(claim.get("villa", ""))
                max_v = SUB_COMMUNITY_VILLA_LIMITS.get(sub)
                if max_v and v_str.isdigit():
                    v_num = int(v_str)
                    if not (1 <= v_num <= max_v):
                        run_query(supabase.table("villa_claims").delete().eq("id", claim["id"]))
    except Exception:
        pass

def mask_email(email_str):
    try:
        user, domain = email_str.split("@", 1)
        if len(user) <= 2:
            masked_user = f"{user[0]}*"
        else:
            masked_user = f"{user[0]}{'*' * (len(user) - 2)}{user[-1]}"
        return f"{masked_user}@{domain}"
    except Exception:
        return email_str

def mask_emails_in_text(text):
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
    return len(set([f"{r['sub_community']}::{r['villa']}" for r in res.data]))

def get_all_villas_for_email(email):
    res = run_query(supabase.table("villa_claims").select("*")
                    .eq("email", email.strip().lower())
                    .order("created_at"))
    return res.data if res and res.data else []

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
    claims = get_claims_for_villa(sub_community, villa)
    if not claims:
        return False, None
    req_email_clean = requesting_email.strip().lower()
    approved_claims = [c for c in claims if c.get("status") == "approved"]
    registered_emails = {c.get("email", "").strip().lower() for c in approved_claims if c.get("email")}
    if req_email_clean in registered_emails:
        return False, None
    if len(registered_emails) < 2:
        return False, None
    now = get_utc_plus_4()
    for c in approved_claims:
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

def check_device_sniping_status(device_uuid, current_email, current_sub, current_villa):
    if not device_uuid or device_uuid in ("no_uuid", "device_pending"):
        return 0, [], 0
    now = get_utc_plus_4()
    cutoff_96h = (now - timedelta(hours=96)).isoformat()
    current_tag = f"{current_sub} - {current_villa}"
    req_email_clean = (current_email or "").strip().lower()
    try:
        res = run_query(
            supabase.table("logs")
            .select("timestamp, event_type, details, fingerprint")
            .gte("timestamp", cutoff_96h)
            .order("timestamp", desc=True)
        )
        all_logs = res.data if res and res.data else []
    except Exception:
        return 0, [], 0

    recent_villas = set()
    penalized_until = None
    cooldown_cleared_at = None

    for entry in all_logs:
        details = entry.get("details") or ""
        fp = entry.get("fingerprint") or ""
        if fp != device_uuid and req_email_clean not in details.lower():
            continue
        try:
            ts = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            continue
        if entry.get("event_type") == "Admin Reset":
            details_lower = details.lower()
            if any(term in details_lower for term in ["cleared cooldown", "reset cooldown", "cleared restrictions", "ownership reset"]):
                if not cooldown_cleared_at or ts > cooldown_cleared_at:
                    cooldown_cleared_at = ts
        if (entry.get("event_type") == "Sniping Penalty" or "sniping lockout" in entry.get("event_type", "").lower()) and ts >= (now - timedelta(hours=96)):
            expiry = ts + timedelta(hours=96)
            if not penalized_until or expiry > penalized_until:
                penalized_until = expiry
        match = re.search(r"(Mira(?:\s+Oasis)?\s+\d+)\s+Villa\s+(\d+)", details)
        if match:
            v_tag = f"{match.group(1)} - {match.group(2)}"
            if v_tag != current_tag and ts >= (now - timedelta(hours=24)):
                recent_villas.add(v_tag)

    if cooldown_cleared_at and penalized_until and cooldown_cleared_at >= (penalized_until - timedelta(hours=96)):
        penalized_until = None
        recent_villas.clear()
    if penalized_until and penalized_until > now:
        hours_left = max(1, int((penalized_until - now).total_seconds() // 3600))
        return 2, list(recent_villas), hours_left
    total_distinct = len(recent_villas) + 1  
    if total_distinct >= 4:
        return 2, list(recent_villas), 96
    elif total_distinct >= 2:
        return 1, list(recent_villas), 0
    return 0, [], 0

def get_blacklisted_accounts():
    now = get_utc_plus_4()
    cutoff_96h = (now - timedelta(hours=96)).isoformat()
    res = run_query(
        supabase.table("logs").select("timestamp, event_type, details, fingerprint")
        .gte("timestamp", cutoff_96h)
        .in_("event_type", ["Sniping Penalty", "Sniping Lockout", "Admin Reset"])
        .order("timestamp", desc=True)
    )
    logs = res.data if res and res.data else []
    cleared_entities = set()
    active_penalties = {}
    for entry in logs:
        ts = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).replace(tzinfo=None)
        details = entry.get("details", "")
        fp = entry.get("fingerprint")
        ev_type = entry.get("event_type", "")
        if ev_type == "Admin Reset":
            m_email = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', details)
            if m_email:
                cleared_entities.add((m_email.group(0).lower(), ts))
            if fp:
                cleared_entities.add((fp, ts))
            continue
        if ev_type in ["Sniping Penalty", "Sniping Lockout"]:
            m_email = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', details)
            email_val = m_email.group(0).lower() if m_email else None
            is_cleared = any(
                (cleared_key == email_val or (fp and cleared_key == fp)) and reset_time >= ts
                for cleared_key, reset_time in cleared_entities
            )
            if is_cleared:
                continue
            expiry = ts + timedelta(hours=96)
            if expiry > now:
                hrs_left = max(1, int((expiry - now).total_seconds() // 3600))
                primary_key = email_val or fp
                if primary_key not in active_penalties:
                    active_penalties[primary_key] = {
                        "email": email_val or "No Email",
                        "fingerprint": fp or "N/A",
                        "penalized_at": ts.strftime('%b %d, %H:%M'),
                        "hours_left": hrs_left,
                        "details": details
                    }
    blacklisted_list = []
    for key, data in active_penalties.items():
        claims = get_all_villas_for_email(data["email"]) if "@" in data["email"] else []
        data["villas"] = [f"{c['sub_community']} - {c['villa']}" for c in claims]
        data["claims"] = claims
        blacklisted_list.append(data)
    return blacklisted_list

def get_all_claimed_villas():
    res = run_query(supabase.table("villa_claims").select("sub_community, villa"))
    if not res or not res.data: return []
    return sorted(list(set([f"{row['sub_community']} - {row['villa']}" for row in res.data])))

def get_bookings_for_day_with_details(date_str):
    response = run_query(
        supabase.table("bookings")
        .select("court, start_hour, sub_community, villa")
        .eq("date", date_str)
        .limit(500)
    )
    if not response or not response.data: return {}
    return {(row['court'], row['start_hour']): f"{row['sub_community']} - {row['villa']}" for row in response.data}

def abbreviate_community(full_name):
    if full_name.startswith("Mira Oasis"):
        return f"MO{full_name.split()[-1]}"
    elif full_name.startswith("Mira"):
        return f"M{full_name.split()[-1]}"
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
    q_future = supabase.table("bookings").select("id", count="exact").eq("villa", villa).eq("sub_community", sub_community).gt("date", today_str)
    res_future = run_query(q_future)
    count_future = res_future.count if res_future and res_future.count is not None else 0
    q_today = supabase.table("bookings").select("id", count="exact").eq("villa", villa).eq("sub_community", sub_community).eq("date", today_str).gte("start_hour", now_hour)
    res_today = run_query(q_today)
    count_today = res_today.count if res_today and res_today.count is not None else 0
    return count_future + count_today

def get_daily_bookings_count(villa, sub_community, date_str):
    mira1_group = ["229", "231", "233"]
    if sub_community == "Mira 1" and villa in mira1_group:
        other_villas = [v for v in mira1_group if v != villa]
        res_others = run_query(supabase.table("bookings").select("id", count="exact").eq("sub_community", "Mira 1").in_("villa", other_villas).eq("date", date_str))
        if (res_others.count or 0) > 0:
            return 99 
        res_self = run_query(supabase.table("bookings").select("id", count="exact").eq("sub_community", "Mira 1").eq("villa", villa).eq("date", date_str))
        return res_self.count if res_self and res_self.count is not None else 0
    else:
        query = supabase.table("bookings").select("id", count="exact").eq("villa", villa).eq("sub_community", sub_community).eq("date", date_str)
        response = run_query(query)
        return response.count if response and response.count is not None else 0

def is_slot_booked(court, date_str, start_hour):
    response = run_query(supabase.table("bookings").select("id").eq("court", court).eq("date", date_str).eq("start_hour", start_hour))
    return len(response.data) > 0 if response and response.data else False

def is_slot_in_past(date_str, start_hour):
    now = get_utc_plus_4()
    today_str = now.strftime('%Y-%m-%d')
    if date_str < today_str: return True
    if date_str == today_str and (start_hour < now.hour or (start_hour == now.hour and now.minute > 0)): return True
    return False

# --- CORE BOOKING FUNCTIONS ---
def book_slot(villa, sub_community, court, date_str, start_hour, fingerprint=None, coach_email=None):
    try:
        payload = {
            "villa": villa, "sub_community": sub_community,
            "court": court, "date": date_str, "start_hour": start_hour
        }
        if coach_email:
            payload["coach_email"] = coach_email
            
        run_query(supabase.table("bookings").insert(payload))
        
        log_detail = f"Coach {coach_email} booked {court} for {date_str} at {start_hour:02d}:00 using {sub_community} Villa {villa}" if coach_email else f"{sub_community} Villa {villa} booked {court} for {date_str} at {start_hour:02d}:00"
        add_log("Booking Created", log_detail, fingerprint=fingerprint)
        return True
    except APIError as e:
        if e.code == "23505": return False
        raise e
    except Exception:
        return False

def delete_booking(booking_id, villa, sub_community, fingerprint=None, coach_email=None):
    record = run_query(supabase.table("bookings").select("court, date, start_hour").eq("id", booking_id).single())
    if record and record.data:
        b = record.data
        log_detail = f"Coach {coach_email} cancelled {b['court']} for {b['date']} at {b['start_hour']:02d}:00" if coach_email else f"{sub_community} Villa {villa} cancelled {b['court']} for {b['date']} at {b['start_hour']:02d}:00"
        add_log("Booking Deleted", log_detail, fingerprint=fingerprint)
    run_query(supabase.table("bookings").delete().eq("id", booking_id))

# --- COACH SPECIFIC LOGIC ---
def get_coach_dashboard_stats(coach_email):
    villas_res = run_query(supabase.table("coach_villas").select("sub_community, villa").eq("coach_email", coach_email))
    assigned_villas = villas_res.data if villas_res and villas_res.data else []
    total_allowed = sum(get_active_booking_limit(v['sub_community'], v['villa']) for v in assigned_villas)
    total_active = sum(get_active_bookings_count(v['villa'], v['sub_community']) for v in assigned_villas)
    return assigned_villas, total_allowed, total_active

def process_coach_booking(coach_email, coach_name, court, date_str, start_hours, fingerprint=None):
    assigned_villas, _, _ = get_coach_dashboard_stats(coach_email)
    booked_slots = []
    
    for hour in start_hours:
        slot_booked = False
        for v in assigned_villas:
            sub = v['sub_community']
            villa_num = v['villa']
            
            if get_active_bookings_count(villa_num, sub) < get_active_booking_limit(sub, villa_num) and get_daily_bookings_count(villa_num, sub, date_str) < 2:
                if book_slot(villa_num, sub, court, date_str, hour, fingerprint, coach_email=coach_email):
                    booked_slots.append({"hour": hour, "villa": villa_num, "sub_community": sub})
                    notify_owner_of_coach_booking(coach_name, villa_num, sub, court, date_str, hour, action="booked")
                    slot_booked = True
                    break
        
        if not slot_booked:
            return False, f"Failed to find available quota for slot {hour}:00 across your villa pool."
            
    return True, booked_slots

def get_coach_bookings(coach_email):
    today_str = get_today().strftime('%Y-%m-%d') 
    now_hour = get_utc_plus_4().hour 
    response = run_query(
        supabase.table("bookings").select("id, court, date, start_hour, villa, sub_community")
        .eq("coach_email", coach_email)
        .or_(f"date.gt.{today_str},and(date.eq.{today_str},start_hour.gte.{now_hour})")
        .order("date")
        .order("start_hour")
    )
    return response.data if response else []

@st.cache_data
def convert_df_to_csv(df):
    return df.to_csv(index=False).encode('utf-8')

def get_user_bookings(villa, sub_community):
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    response = run_query(
        supabase.table("bookings").select("id, court, date, start_hour")
        .eq("villa", villa)
        .eq("sub_community", sub_community)
        .is_("coach_email", "null")
        .or_(f"date.gt.{today_str},and(date.eq.{today_str},start_hour.gte.{now_hour})")
        .order("date")
        .order("start_hour")
    )
    return response.data if response else []

def get_slot_history(court, date_str, start_hour):
    pattern = f"%{court} for {date_str} at {start_hour:02d}:00%"
    response = run_query(
        supabase.table("logs").select("timestamp, event_type, details")
        .in_("event_type", ["Booking Created", "Booking Deleted"])
        .ilike("details", pattern)
        .order("timestamp")
    )
    if not response or not response.data: return []
    history = []
    for row in response.data:
        details = row.get("details", "") or ""
        match = re.match(r"^(.*?) Villa (\S+) (booked|cancelled) ", details)
        if not match:
            coach_match = re.search(r"Coach (.*?) (booked|cancelled).*?using (.*?) Villa (\S+)", details)
            if coach_match:
                who = f"Coach {coach_match.group(1)} ({coach_match.group(3)} - {coach_match.group(4)})"
                action = coach_match.group(2)
            else:
                continue
        else:
            who = f"{match.group(1)} - Villa {match.group(2)}"
            action = match.group(3)
            
        raw_ts = row.get("timestamp", "")
        try:
            ts_display = datetime.fromisoformat(raw_ts).strftime("%b %d, %Y %I:%M %p")
        except Exception:
            ts_display = raw_ts
        history.append({"action": action, "who": who, "display_time": ts_display})
    return history

@st.cache_data(ttl=60)
def get_logs_last_14_days():
    cutoff = (get_utc_plus_4() - timedelta(days=14)).isoformat()
    response = run_query(supabase.table("logs").select("timestamp, event_type, fingerprint, details").gte("timestamp", cutoff).order("timestamp", desc=True))
    return response.data if response else []

def get_villas_with_active_bookings():
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    try:
        res_future = run_query(supabase.table("bookings").select("villa, sub_community").gt("date", today_str))
        res_today = run_query(supabase.table("bookings").select("villa, sub_community").eq("date", today_str).gte("start_hour", now_hour))
        all_rows = (res_future.data if res_future else []) + (res_today.data if res_today else [])
        return sorted(list(set([f"{row['sub_community']} - {row['villa']}" for row in all_rows])))
    except Exception:
        return []

def get_all_villas_with_any_bookings():
    response = run_query(supabase.table("bookings").select("villa, sub_community"))
    if not response or not response.data: return []
    return sorted(list(set([f"{row['sub_community']} - {row['villa']}" for row in response.data])))

def get_bookings_for_villa(villa, sub_community):
    response = run_query(
        supabase.table("bookings").select("id, court, date, start_hour")
        .eq("villa", villa).eq("sub_community", sub_community)
        .order("date", desc=True).order("start_hour", desc=True)
    )
    return response.data if response else []

def _process_background_tasks():
    try:
        purge_out_of_range_records()
        from database_cleanup import run_db_cleanup
        run_db_cleanup(supabase, courts, donor_villas=DONOR_VILLAS)
    except Exception:
        pass

def get_active_bookings_for_villa_display(villa_identifier):
    try:
        sub_comm, villa_num = villa_identifier.split(" - ")
        today_str = get_today().strftime('%Y-%m-%d')
        now_hour = get_utc_plus_4().hour
        response = run_query(
            supabase.table("bookings").select("court, date, start_hour")
            .eq("villa", villa_num).eq("sub_community", sub_comm)
            .or_(f"date.gt.{today_str},and(date.eq.{today_str},start_hour.gte.{now_hour})")
            .order("date").order("start_hour")
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
    booked_hours = [row['start_hour'] for row in response.data] if response and response.data else []
    return [h for h in get_start_hours_for_date(date_str) if h not in booked_hours and not is_slot_in_past(date_str, h)]

# --- DIALOGS ---
@st.dialog("🎾 Notice: A Fairer Booking System for Everyone!")
def show_migration_dialog():
    st.markdown("""
    Hi neighbors! 👋
    To keep court bookings fair and stop people from booking under fake or multiple villas, we are introducing a simple **one-time email verification**.
    **What this means for you:**
    * **Fair access for real residents:** Keeps slots open for those who actually live here.
    * **One-time only:** Just enter your email and a 6-digit code once!
    * **Family friendly:** Up to 2 emails can be linked to your villa.
    ---
    Please enter your email below to continue.
    """)
    if st.button("Got it — Continue 🎾", type="primary"):
        st.session_state.seen_migration_notice = True
        st.rerun(scope="app")

@st.dialog("⚠️ Villa Sniping Detected")
def show_sniping_warning_dialog(other_villas):
    st.markdown(f"""
    **Potential Misuse Warning**
    Our system detected that this device has recently reserved court slots across multiple villas (**{", ".join(other_villas)}**) within 24 hours.
    Hopping across properties is prohibited.
    """)
    if st.button("I Understand — Proceed", type="primary"):
        st.session_state.seen_sniping_warning = True
        st.rerun(scope="app")

@st.dialog("🚫 Account Suspended: Villa Sniping Lockout")
def show_sniping_lockout_dialog(hours_remaining):
    st.error(f"### 4-Day Security Cooldown Imposed\n\nCross-villa hopping detected. Associated properties locked for {hours_remaining} hours.")
    if st.button("Close / Logout"):
        logout_action()

# --- AUTH TOKEN HELPERS ---
AUTH_SALT = "mira_court_booking_salt_2026"

def encode_auth_token(sub_community, villa, email):
    if not email: return ""
    payload = f"{sub_community}::{villa}::{email}"
    sig = hashlib.sha256(f"{payload}:{AUTH_SALT}".encode()).hexdigest()[:10]
    return base64.urlsafe_b64encode(f"{payload}::{sig}".encode()).decode()

def decode_auth_token(token_str):
    try:
        raw = base64.urlsafe_b64decode(token_str.encode()).decode()
        sub, villa, email, sig = raw.split("::")
        if hashlib.sha256(f"{sub}::{villa}::{email}:{AUTH_SALT}".encode()).hexdigest()[:10] == sig:
            return {"sub_community": sub, "villa": villa, "email": email}
    except Exception:
        pass
    return None

def logout_action():
    st_javascript("""
        localStorage.removeItem('court_villa_lock');
        localStorage.removeItem('court_verified_email');
        localStorage.removeItem('verified_claim_info');
        localStorage.removeItem('supabase_refresh_token');
        setTimeout(() => { window.location.href = window.location.origin + window.location.pathname; }, 150);
    """, key="js_exec_logout")
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.query_params.clear()
    st.info("Logging out...")
    time.sleep(0.8)
    st.rerun()

# --- STYLING ---
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

# --- SCHEDULE ROUTE ---
if st.query_params.get("view") == "full":
    st.title("📅 Full 14-Day Schedule")
    if st.button("⬅️ Back to Booking App", key="back_to_app_btn"):
        curr_auth = st.query_params.get("auth")
        st.query_params.clear()
        if curr_auth: st.query_params["auth"] = curr_auth
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
                    row.append(f"{abbreviate_community(full_comm)}-{villa_num}")
                else: row.append("Available")
            data[label] = row
        st.dataframe(pd.DataFrame(data, index=courts).style.map(color_cell))
        st.divider()
    st.stop()

# --- HEADER STATS ---
st.subheader("🎾 Book that Court ...")    
st.caption("An Un-Official & Community Driven Booking Solution.")
st.markdown("<p style='color:#ccff00; font-weight:700; margin-top:-8px;'>Serving about 2,450 active users, the app is community coded and funded.</p>", unsafe_allow_html=True)

try:
    _process_background_tasks()
    villas_active = get_villas_with_active_bookings()
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    res_f = run_query(supabase.table("bookings").select("id", count="exact").gt("date", today_str))
    res_t = run_query(supabase.table("bookings").select("id", count="exact").eq("date", today_str).gte("start_hour", now_hour))
    count_f = res_f.count if res_f and res_f.count is not None else 0
    count_t = res_t.count if res_t and res_t.count is not None else 0
    st.write(f"**{len(villas_active)}** Residences have **{count_f + count_t}** active bookings.")
except Exception:
    villas_active = []

if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False
if 'is_coach' not in st.session_state:
    st.session_state.is_coach = False

js_device_fetch = st_javascript("""
    (function() {
        let devId = localStorage.getItem('court_device_uuid');
        if (!devId) {
            devId = 'dev_' + Math.floor(Math.random() * 89999999 + 10000000) + '_' + Math.floor(Date.now() / 1000);
            localStorage.setItem('court_device_uuid', devId);
        }
        return devId;
    })();
""", key="device_uuid_unique_fetch")

if isinstance(js_device_fetch, str) and js_device_fetch.startswith("dev_"):
    st.session_state.device_uuid = js_device_fetch
elif "device_uuid" not in st.session_state:
    st.session_state.device_uuid = f"dev_{random.randint(10000000, 99999999)}_{int(time.time())}"

if "is_mobile_device" not in st.session_state:
    ua_check = st_javascript("""
        (function() {
            const ua = navigator.userAgent || '';
            return /Mobi|Android|iPhone|iPad|iPod/i.test(ua) ? 'mobile' : 'desktop';
        })();
    """, key="ua_check_unique_fetch")
    st.session_state.is_mobile_device = (ua_check == "mobile")

url_token = st.query_params.get("auth")
if url_token and not st.session_state.authenticated:
    verified_claim = decode_auth_token(url_token)
    if verified_claim and verified_claim.get("email"):
        st.session_state.sub_community = verified_claim["sub_community"]
        st.session_state.villa = verified_claim["villa"]
        st.session_state.verified_email = verified_claim["email"]
        st.session_state.authenticated = True
        st.session_state.is_coach = False

if not st.session_state.authenticated:
    stored_bundle = st_javascript("(localStorage.getItem('court_villa_lock') || 'no_lock') + ':::' + (localStorage.getItem('court_verified_email') || '') + ':::' + (localStorage.getItem('verified_claim_info') || '');", key="bundle_check_unique_fetch")
    
    if isinstance(stored_bundle, str) and ":::" in stored_bundle:
        parts = stored_bundle.split(":::")
        s_lock, s_email, s_claim = parts[0], parts[1], parts[2]
        if s_email:
            target_sub, target_villa = None, None
            if s_claim and "::" in s_claim:
                target_sub, target_villa = s_claim.split("::", 1)
            elif s_lock and s_lock != "no_lock" and "-" in s_lock:
                target_sub, target_villa = s_lock.rsplit("-", 1)
            if target_sub and target_villa:
                st.session_state.sub_community = target_sub
                st.session_state.villa = target_villa
                st.session_state.verified_email = s_email
                st.session_state.authenticated = True
                st.session_state.is_coach = False
                st.query_params["auth"] = encode_auth_token(target_sub, target_villa, s_email)
                st.rerun()

# --- DISCREET AUTHENTICATION SCREEN ---
if not st.session_state.authenticated:
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
            otp_sub = st.selectbox("Sub-Community", options=sub_community_list, index=default_sub_idx, key="otp_sub_select_box")
        with col_v2:
            max_limit = SUB_COMMUNITY_VILLA_LIMITS.get(otp_sub, 500)
            otp_villa_raw = st.text_input(f"Villa Number (1 - {max_limit})", value=default_villa, key="otp_villa_input_box").strip()
            otp_villa = "".join(filter(str.isdigit, otp_villa_raw))

        otp_email_input = st.text_input("Email Address", placeholder="name@example.com", key="otp_email_input_box").strip().lower()

        if st.button("Continue / Send Code", type="primary", key="submit_discreet_auth_btn"):
            max_allowed = SUB_COMMUNITY_VILLA_LIMITS.get(otp_sub, 9999)
            if not otp_email_input or "@" not in otp_email_input:
                st.error("Please provide a valid email address.")
            elif is_disposable_email(otp_email_input):
                st.error("Disposable/temporary email domains are not allowed.")
            else:
                # 1. DISCREET COACH ROUTING
                coach_check = run_query(supabase.table("coach_accounts").select("*").eq("email", otp_email_input).eq("is_active", True))
                if coach_check and coach_check.data:
                    st.session_state.authenticated = True
                    st.session_state.is_coach = True
                    st.session_state.coach_email = otp_email_input
                    st.session_state.coach_name = coach_check.data[0]['coach_name']
                    st.success(f"Welcome back, Coach {st.session_state.coach_name}!")
                    time.sleep(0.8)
                    st.rerun()

                # 2. STANDARD RESIDENT FLOW
                if not otp_sub or not otp_villa:
                    st.error("Please specify your Sub-Community and Villa Number.")
                elif not otp_villa.isdigit() or not (1 <= int(otp_villa) <= max_allowed):
                    st.error(f"Invalid villa number for {otp_sub}. Must be between 1 and {max_allowed}.")
                else:
                    existing_claim = get_existing_claim(otp_sub, otp_villa, otp_email_input)
                    current_claims_count = get_villa_claims_count(otp_sub, otp_villa)
                    email_villas_count = get_email_claimed_villas_count(otp_email_input)
                    is_on_cooldown, hours_left = get_recent_claim_cooldown(otp_sub, otp_villa, otp_email_input)
                    target_pair = f"{otp_sub}::{otp_villa}"
                    current_uuid = st.session_state.get("device_uuid", "device_pending")
                    uuid_villas = get_uuid_claimed_villas(current_uuid)
                    
                    if not existing_claim and is_on_cooldown:
                        st.error(f"🚫 Security Lockout: Villa is on a 72-hour ownership change cooldown ({hours_left}h left).")
                    elif not existing_claim and current_claims_count >= 2:
                        st.error(f"🚫 Villa already has 2 verified resident emails attached.")
                    elif not existing_claim and email_villas_count >= 3:
                        st.error("Unable to register this villa to your email address (max reached).")
                    elif not existing_claim and target_pair not in uuid_villas and len(uuid_villas) >= 3:
                        st.error("This device has reached the maximum allowed registered villas.")
                    else:
                        with st.spinner("Sending 6-digit verification code..."):
                            try:
                                supabase.auth.sign_in_with_otp({"email": otp_email_input})
                                st.session_state.otp_sent = True
                                st.session_state.otp_email = otp_email_input
                                st.session_state.otp_target_sub = otp_sub
                                st.session_state.otp_target_villa = otp_villa
                                st.success(f"✅ Code sent! Check inbox at {otp_email_input}")
                                time.sleep(1.2)
                                st.rerun()
                            except Exception as e:
                                st.error(f"Failed to send code: {str(e)}")
        
        st.write("")
        if st.button("🚪 Reset / Clear Details", key="reset_auth_form_btn"):
            logout_action()
    else:
        st.info(f"Enter the 6-digit code sent to **{st.session_state.otp_email}** for **{st.session_state.otp_target_sub} - Villa {st.session_state.otp_target_villa}**.")
        token_input = st.text_input("Enter 6-digit code", max_chars=6, key="otp_token_text_input").strip()
        
        c1, c2, c3 = st.columns([1.5, 1.2, 1.2])
        with c1:
            if st.button("Verify Code", type="primary", key="verify_otp_code_btn"):
                if not token_input or len(token_input) != 6:
                    st.error("Please enter a 6-digit verification code.")
                else:
                    with st.spinner("Verifying..."):
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
                                        "sub_community": target_sub, "villa": target_villa,
                                        "email": verified_email, "fingerprint": resolved_uuid,
                                        "status": "approved", "verified_at": now_ts
                                    }))
                                    add_log("Villa Claim", f"{target_sub} Villa {target_villa} claimed by {verified_email}", fingerprint=resolved_uuid)
                                else:
                                    run_query(supabase.table("villa_claims").update({
                                        "verified_at": now_ts, "fingerprint": resolved_uuid, "status": "approved"
                                    }).eq("id", existing["id"]))

                                st_javascript(f"""
                                    localStorage.setItem('court_villa_lock', '{target_sub}-{target_villa}');
                                    localStorage.setItem('court_verified_email', '{verified_email}');
                                    localStorage.setItem('verified_claim_info', '{target_sub}::{target_villa}');
                                    localStorage.setItem('supabase_refresh_token', '{refresh_tok}');
                                    localStorage.setItem('court_device_uuid', '{resolved_uuid}');
                                """, key="js_store_verified_credentials")

                                st.query_params["auth"] = encode_auth_token(target_sub, target_villa, verified_email)
                                st.session_state.sub_community = target_sub
                                st.session_state.villa = target_villa
                                st.session_state.verified_email = verified_email
                                st.session_state.authenticated = True
                                st.session_state.is_coach = False
                                st.session_state.otp_sent = False
                                st.balloons()
                                st.success("✅ Verified successfully! Logging in...")
                                time.sleep(1.2)
                                st.rerun()
                            else:
                                st.error("Verification failed. Please check the code.")
                        except Exception as e:
                            st.error(f"Invalid code or error: {str(e)}")
        with c2:
            if st.button("🔄 Resend Code", key="resend_otp_code_btn"):
                with st.spinner("Resending..."):
                    try:
                        supabase.auth.sign_in_with_otp({"email": st.session_state.otp_email})
                        st.toast(f"Code resent to {st.session_state.otp_email}!")
                    except Exception as e:
                        st.error(f"Error: {e}")
        with c3:
            if st.button("Cancel / Change", key="cancel_otp_flow_btn"):
                st.session_state.otp_sent = False
                st.rerun()
        
        st.write("")
        if st.button("🚪 Reset / Clear Details", key="reset_otp_screen_btn"):
            logout_action()

    st.write("")
    with st.expander("🛠️ Admin Emergency Console", expanded=(st.query_params.get("admin") == "true")):
        login_admin_pwd = st.text_input("Enter Admin Password", type="password", key="login_screen_admin_pass")
        if login_admin_pwd == st.secrets.get("ADMIN_PASSWORD", "admin123"):
            rst_email = st.text_input("Resident Email Address to Restore", placeholder="resident@example.com", key="emergency_rst_email").strip().lower()
            if st.button("🔓 Clear Restrictions & Restore Clean Access", type="primary", key="exec_emergency_reset_btn"):
                if rst_email and "@" in rst_email:
                    now_ts = get_utc_plus_4().isoformat()
                    for c in get_all_villas_for_email(rst_email):
                        run_query(supabase.table("villa_claims").update({"verified_at": now_ts, "status": "approved"}).eq("id", c["id"]))
                    add_log("Admin Reset", f"Admin cleared restrictions for {rst_email}")
                    st.success(f"✅ Restrictions cleared for {rst_email}!")
                    time.sleep(1.2)
                    st.rerun()
        elif login_admin_pwd:
            st.error("Incorrect Password")
    st.stop()

# ==========================================
# --- ROUTING: COACH DASHBOARD VS RESIDENT ---
# ==========================================
if st.session_state.get('is_coach'):
    coach_email = st.session_state.coach_email
    coach_name = st.session_state.coach_name
    st.success(f"✅ Logged in as Coach: **{coach_name}** (`{coach_email}`)")
    
    assigned_villas, total_allowed, total_active = get_coach_dashboard_stats(coach_email)
    
    villa_display_list = []
    for v in assigned_villas:
        sub, villa_num = v['sub_community'], v['villa']
        current_active = get_active_bookings_count(villa_num, sub)
        villa_limit = get_active_booking_limit(sub, villa_num)
        villa_display_list.append(f"{sub} Villa {villa_num} ({current_active}/{villa_limit})")
    
    villas_list_str = ", ".join(villa_display_list) if assigned_villas else "No villas assigned yet"
    st.caption(f"🏡 **Assigned Villas Pool & Availability:** {villas_list_str}")
    
    c_tab1, c_tab2 = st.tabs(["➕ Pool Booking Engine", "📋 My Coach Bookings"])
    
    with c_tab1:
        st.subheader("Book using your Villa Pool")
        col1, col2, col3 = st.columns(3)
        col1.metric("Villas in Pool", len(assigned_villas))
        col2.metric("Total Allowed Quota", total_allowed)
        col3.metric("Currently Active in Pool", f"{total_active} / {total_allowed}")
        
        date_options = [f"{d.strftime('%Y-%m-%d')} ({d.strftime('%A')})" for d in get_next_14_days()]
        date_choice = st.selectbox("Date:", date_options, key="coach_pool_date_select").split(" (")[0]
        court_choice = st.selectbox("Court:", courts, key="coach_pool_court_select")
        
        free_hours = get_available_hours(court_choice, date_choice)
        if not free_hours: 
            st.warning("No slots available.")
            time_choice = None
        else: 
            time_choice = st.selectbox("Time Slot:", [f"{h:02d}:00 - {h+1:02d}:00" for h in free_hours], key="coach_pool_time_select")
        
        slots_2_hours = st.checkbox("Book for 2 hours", disabled=(not time_choice or int(time_choice.split(":")[0])+1 not in free_hours), key="coach_2_hour_toggle")
        
        if st.button("🚀 Book as Coach", type="primary", key="coach_exec_book_btn"):
            if not time_choice: 
                st.error("Select time.")
            else:
                start_h = int(time_choice.split(":")[0])
                hours_to_book = [start_h, start_h+1] if slots_2_hours else [start_h]
                success, result = process_coach_booking(coach_email, coach_name, court_choice, date_choice, hours_to_book, fingerprint="coach_action")
                if success:
                    st.balloons()
                    st.success("Booked successfully using allocations from: " + ", ".join([f"Villa {r['villa']}" for r in result]))
                    time.sleep(2)
                    st.rerun()
                else: 
                    st.error(result)
        
        st.divider()
        if st.button("🚪 Logout", key="coach_dash_logout_btn"): 
            logout_action()

    with c_tab2:
        st.subheader("📋 My Coach Bookings")
        my_coach_b = get_coach_bookings(coach_email)
        if my_coach_b:
            df_coach = pd.DataFrame(my_coach_b)
            df_coach['Time'] = df_coach['start_hour'].apply(lambda x: f"{x:02d}:00")
            csv_data = convert_df_to_csv(df_coach[['id', 'date', 'Time', 'court', 'sub_community', 'villa']])
            st.download_button(label="📥 Export All Upcoming to CSV", data=csv_data, file_name=f"coach_{coach_name}_bookings.csv", mime="text/csv", type="primary", key="coach_export_csv_btn")
            st.divider()
            
            for b in my_coach_b:
                with st.container(border=True):
                    st.write(f"**🎾 {b['court']}** | {b['date']} at {b['start_hour']:02d}:00")
                    st.caption(f"Utilizing Quota: {b['sub_community']} - Villa {b['villa']} | Ref: #{b['id']}")
                    if st.button("❌ Cancel & Refund Quota", key=f"coach_cancel_{b['id']}"):
                        delete_booking(b['id'], b['villa'], b['sub_community'], coach_email=coach_email)
                        notify_owner_of_coach_booking(coach_name, b['villa'], b['sub_community'], b['court'], b['date'], b['start_hour'], action="cancelled")
                        st.success("Cancelled. Quota returned to owner.")
                        time.sleep(1)
                        st.rerun()
        else:
            st.info("No active coach bookings.")

else:
    # ----------------------------------------
    # RESIDENT DASHBOARD
    # ----------------------------------------
    sub_community, villa = st.session_state.sub_community, st.session_state.villa
    verified_user_email = st.session_state.get("verified_email", "Verified")
    st.success(f"✅ Logged in as: **{sub_community} - Villa {villa}** (`{verified_user_email}`)")

    if is_donor_villa(sub_community, villa):
        render_donor_legend_banner()

    current_device = st.session_state.get("device_uuid")
    sniping_level, hopping_villas, cooldown_hrs = check_device_sniping_status(current_device, verified_user_email, sub_community, villa)

    if sniping_level == 2:
        add_log("Sniping Penalty", f"4-day penalty active for {sub_community} Villa {villa}", fingerprint=current_device)
        show_sniping_lockout_dialog(cooldown_hrs)
        st.stop()
    elif sniping_level == 1 and not st.session_state.get("seen_sniping_warning", False):
        add_log("Sniping Warning", f"Warning for {sub_community} Villa {villa}", fingerprint=current_device)
        show_sniping_warning_dialog(hopping_villas)

    tab1, tab2, tab3, tab4, tab5 = st.tabs(["📅 Availability", "➕ Book", "📋 My Bookings", "🛠️ Court Maint.", "📜 Activity Log"])

    with tab1:
        st.subheader("Court Availability")
        date_options = [f"{d.strftime('%Y-%m-%d')} ({d.strftime('%A')})" for d in get_next_14_days()]
        selected_date_full = st.selectbox("Select Date:", date_options, key="res_tab1_date_select")
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
                    abbr = abbreviate_community(full_comm)
                    row.append(f"{abbr}-{villa_num}")
                else: row.append("Available")
            data[label] = row
        st.dataframe(pd.DataFrame(data, index=courts).style.map(color_cell))
        
        curr_auth = st.query_params.get("auth")
        st.link_button("🌐 View Full 14-Day Schedule (Full Page)", url=f"/?view=full&auth={curr_auth}" if curr_auth else "/?view=full")

        st.divider()
        st.subheader("🔍 Court Status & Booking History")
        hist_col1, hist_col2 = st.columns([1, 1])
        with hist_col1:
            hist_court = st.selectbox("Select Court", options=courts, key="tab1_hist_court_select")
        with hist_col2:
            hist_hours = get_start_hours_for_date(selected_date)
            hist_time_label = st.selectbox("Select Time Slot", options=[f"{h:02d}:00 - {h+1:02d}:00" for h in hist_hours], key="tab1_hist_time_select") if hist_hours else None
            hist_hour = hist_hours[[f"{h:02d}:00 - {h+1:02d}:00" for h in hist_hours].index(hist_time_label)] if hist_time_label else None

        if hist_hour is not None:
            if (hist_court, hist_hour) in bookings_with_details:
                st.error(f"🔒 Currently **BOOKED** — {bookings_with_details[(hist_court, hist_hour)]}")
            else:
                st.success("✅ Currently **AVAILABLE**")

            slot_history = get_slot_history(hist_court, selected_date, hist_hour)
            if slot_history:
                for entry in slot_history:
                    st.markdown(f"- {'🟢 **Booked**' if entry['action'] == 'booked' else '🔴 **Cancelled**'} by {entry['who']} — _{entry['display_time']}_")

        st.divider()
        st.markdown("### ⚡ Quick Book")
        q_col1, q_col2, q_col3, q_col4 = st.columns([2, 2, 2, 2])
        with q_col1: q_court = st.selectbox("Select Court", options=courts, key="quick_court_select")
        with q_col2:
            q_free_hours = get_available_hours(q_court, selected_date)
            q_time = st.selectbox("Select Time", options=[f"{h:02d}:00" for h in q_free_hours], key="quick_time_select") if q_free_hours else None
        with q_col3:
            st.write(""); st.write("")
            q_disabled = not q_time or (int(q_time.split(":")[0]) + 1 not in get_start_hours_for_date(selected_date)) or is_slot_booked(q_court, selected_date, int(q_time.split(":")[0]) + 1)
            q_2_hours = st.checkbox("Book for 2 hours", key="quick_2_hours_checkbox", disabled=q_disabled)
            q_slots = 2 if q_2_hours else 1
        with q_col4:
            st.write(""); st.write("") 
            if st.button("🚀 Book Now", key="quick_exec_book_btn"):
                if q_time:
                    active_count = get_active_bookings_count(villa, sub_community)
                    active_limit = get_active_booking_limit(sub_community, villa)
                    daily_count = get_daily_bookings_count(villa, sub_community, selected_date)
                    start_h = int(q_time.split(":")[0])
                    slots_to_book = list(range(start_h, start_h + q_slots))
                    
                    if active_count + q_slots > active_limit:
                        st.error(f"Limit Reached (Max {active_limit} active).")
                    elif daily_count + q_slots > 2:
                        st.error("Daily Limit Reached (Max 2 per day).")
                    else:
                        booked = [h for h in slots_to_book if book_slot(villa, sub_community, q_court, selected_date, h, fingerprint=current_device)]
                        if len(booked) == q_slots:
                            send_booking_notification_once("created", villa, sub_community, q_court, selected_date, booked, verified_user_email)
                            st.balloons()
                            st.success(f"Booked {q_slots} slot(s)!")
                            time.sleep(1.5)
                            st.rerun()

        st.divider()
        st.subheader("📊 Community Usage Insights")
        usage_data = get_peak_time_data()
        if not usage_data.empty:
            c1, c2 = st.columns(2)
            with c1: st.bar_chart(pd.DataFrame({"Bookings": usage_data['start_hour'].value_counts().sort_index().values}, index=[f"{h:02d}:00" for h in usage_data['start_hour'].value_counts().sort_index().index]), color="#4CAF50")
            with c2: st.area_chart(usage_data['day_of_week'].value_counts().reindex(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]).fillna(0), color="#0d5384")

        st.divider()
        if st.button("🚪 Logout / Change Villa", key="res_tab1_logout_btn"):
            logout_action()

    with tab2:
        st.subheader("Book a New Slot")
        date_choice = st.selectbox("Date:", [f"{d.strftime('%Y-%m-%d')} ({d.strftime('%A')})" for d in get_next_14_days()], key="tab2_new_book_date").split(" (")[0]
        tab2_active_limit = get_active_booking_limit(sub_community, villa)
        court_choice = st.selectbox("Court:", courts, key="tab2_new_book_court")
        free_hours = get_available_hours(court_choice, date_choice)
        time_choice = st.selectbox("Time Slot:", [f"{h:02d}:00 - {h+1:02d}:00" for h in free_hours], key="tab2_new_book_time") if free_hours else None
        
        t2_disabled = not time_choice or (int(time_choice.split(":")[0]) + 1 not in get_start_hours_for_date(date_choice)) or is_slot_booked(court_choice, date_choice, int(time_choice.split(":")[0]) + 1)
        slots_2_hours = st.checkbox("Book for 2 hours", key="tab2_2hr_checkbox", disabled=t2_disabled)
        slots_choice = 2 if slots_2_hours else 1

        if st.button("Book This Slot", type="primary", key="tab2_exec_book_btn"):
            if time_choice:
                start_h = int(time_choice.split(":")[0])
                slots_to_book = list(range(start_h, start_h + slots_choice))
                if get_active_bookings_count(villa, sub_community) + slots_choice > tab2_active_limit:
                    st.error(f"Active Limit Reached ({tab2_active_limit}).")
                elif get_daily_bookings_count(villa, sub_community, date_choice) + slots_choice > 2:
                    st.error("Daily Limit Reached (2 max).")
                else:
                    booked = [h for h in slots_to_book if book_slot(villa, sub_community, court_choice, date_choice, h, fingerprint=current_device)]
                    if len(booked) == slots_choice:
                        send_booking_notification_once("created", villa, sub_community, court_choice, date_choice, booked, verified_user_email)
                        st.balloons()
                        st.success("Successfully booked!")
                        time.sleep(1.5)
                        st.rerun()

    with tab3:
        st.subheader("📋 My Bookings")
        my_b = get_user_bookings(villa, sub_community)
        limit_val = get_active_booking_limit(sub_community, villa)
        
        col_sum1, col_sum2 = st.columns(2)
        with col_sum1: st.metric("Total Active Bookings", f"{len(my_b)} / {limit_val}")
        with col_sum2: st.metric("Today's Bookings", f"{len([b for b in my_b if b['date'] == get_today().strftime('%Y-%m-%d')])} / 2")
        st.divider()

        merged_bookings = []
        if my_b:
            df_my_b = pd.DataFrame(my_b).sort_values(['date', 'court', 'start_hour'])
            current_booking = None
            for _, row in df_my_b.iterrows():
                if current_booking is None:
                    current_booking = {'court': row['court'], 'date': row['date'], 'start_hours': [row['start_hour']], 'ids': [row['id']], 'v': villa, 'sc': sub_community}
                else:
                    if row['date'] == current_booking['date'] and row['court'] == current_booking['court'] and row['start_hour'] == max(current_booking['start_hours']) + 1:
                        current_booking['start_hours'].append(row['start_hour'])
                        current_booking['ids'].append(row['id'])
                    else:
                        merged_bookings.append(current_booking)
                        current_booking = {'court': row['court'], 'date': row['date'], 'start_hours': [row['start_hour']], 'ids': [row['id']], 'v': villa, 'sc': sub_community}
            if current_booking: merged_bookings.append(current_booking)

        if merged_bookings:
            if st.button("📧 Email Me All My Bookings", type="primary", key="res_email_summary_btn"):
                if send_all_bookings_summary(villa, sub_community, merged_bookings, verified_user_email):
                    st.success("Summary sent!")
            st.divider()
            for i, b in enumerate(merged_bookings):
                with st.container():
                    start_t, end_t = min(b['start_hours']), max(b['start_hours']) + 1
                    id_display = f"#{b['ids'][0]}" if len(b['ids']) == 1 else f"#{min(b['ids'])}-{max(b['ids'])}"
                    time_display = f"{start_t:02d}:00 - {end_t:02d}:00"
                    st.markdown(f"**🎾 {b['court']}** | {b['date']} | ⏰ {time_display} (Ref: {id_display})")
                    jpg_bytes = generate_booking_card_jpg(id_display, b['court'], b['sc'], b['v'], b['date'], time_display)
                    render_share_or_download_button(jpg_bytes, f"booking_{id_display}.jpg", id_display, key=f"card_{i}")
                    if st.button(f"❌ Cancel Booking {id_display}", key=f"res_cancel_booking_{i}"):
                        for bid in b['ids']: delete_booking(bid, b['v'], b['sc'], fingerprint=current_device)
                        send_booking_notification_once("deleted", b['v'], b['sc'], b['court'], b['date'], b['start_hours'], verified_user_email)
                        st.success("Cancelled!")
                        time.sleep(1)
                        st.rerun()
        else:
            st.info("No active bookings.")

    with tab4:
        st.subheader("🛠️ Court Maintenance")
        with st.expander("📝 Report a New Issue", expanded=False):
            m_court = st.selectbox("Select Court", options=courts, key="tab4_report_court_select")
            m_desc = st.text_area("Issue Description", placeholder="Details...", key="tab4_report_desc")
            if st.button("Submit Report", type="primary", key="tab4_submit_report_btn"):
                if m_desc:
                    run_query(supabase.table("court_maintenance").insert({
                        "created_at": get_utc_plus_4().isoformat(), "court_name": m_court,
                        "description": m_desc, "reported_by": f"{sub_community} Villa {villa}", "is_fixed": False
                    }))
                    st.success("Reported!")
                    time.sleep(1)
                    st.rerun()

    with tab5:
        st.subheader("Community Activity Log")
        admin_pass = st.text_input("Admin Password", type="password", key="tab5_admin_password_input")
        is_admin = admin_pass == st.secrets.get("ADMIN_PASSWORD", "admin123")

        logs = get_logs_last_14_days()
        if logs:
            log_df = pd.DataFrame(logs, columns=["timestamp", "event_type", "details"])
            if not is_admin:
                log_df = log_df[log_df['event_type'] != "Limit Enforcement"]
                log_df['details'] = log_df['details'].apply(mask_emails_in_text)
            log_df['timestamp'] = pd.to_datetime(log_df['timestamp'], format='ISO8601').dt.strftime('%b %d, %H:%M')
            st.dataframe(log_df, hide_index=True)

        if is_admin:
            st.success("Admin Access Granted")
            
            # --- ADMIN COACH POOL CRM ---
            with st.expander("🎾 Coach & Pool Management", expanded=True):
                st.markdown("### 1. Create New Coach Profile")
                with st.form("admin_create_coach_form"):
                    col_c1, col_c2 = st.columns(2)
                    with col_c1: new_c_email = st.text_input("Coach Email", key="adm_coach_email").strip().lower()
                    with col_c2: new_c_name = st.text_input("Coach Name", key="adm_coach_name").strip()
                    if st.form_submit_button("Create Coach Profile", type="primary"):
                        if new_c_email and new_c_name:
                            run_query(supabase.table("coach_accounts").insert({"email": new_c_email, "coach_name": new_c_name}))
                            st.success(f"Created Coach {new_c_name}!")
                            time.sleep(1)
                            st.rerun()

                st.divider()
                st.markdown("### 2. Manage Existing Coaches")
                coaches_res = run_query(supabase.table("coach_accounts").select("*").order("created_at"))
                all_coaches = coaches_res.data if coaches_res and coaches_res.data else []
                if all_coaches:
                    selected_coach_label = st.selectbox("Select Coach", options=["-- Select --"] + [f"{c['coach_name']} ({c['email']})" for c in all_coaches], key="adm_select_coach_edit")
                    if selected_coach_label != "-- Select --":
                        selected_c_email = selected_coach_label.split(" (")[1].replace(")", "")
                        selected_c_data = next((c for c in all_coaches if c['email'] == selected_c_email), None)
                        st.markdown(f"#### 👤 {selected_c_data['coach_name']}'s Profile")
                        
                        if st.button(f"🗑️ Delete {selected_c_data['coach_name']}'s Profile", type="secondary", key="del_coach_prof_btn"):
                            run_query(supabase.table("coach_accounts").delete().eq("email", selected_c_email))
                            st.success("Deleted!")
                            time.sleep(1)
                            st.rerun()

                        st.markdown("##### 🏡 Assigned Villas Pool")
                        villas_res = run_query(supabase.table("coach_villas").select("*").eq("coach_email", selected_c_email))
                        if villas_res and villas_res.data:
                            for v in villas_res.data:
                                col_v1, col_v2 = st.columns([3, 1])
                                with col_v1: st.info(f"**{v['sub_community']} - Villa {v['villa']}**")
                                with col_v2:
                                    if st.button("❌ Remove", key=f"rm_v_{v['id']}"):
                                        run_query(supabase.table("coach_villas").delete().eq("id", v['id']))
                                        st.rerun()
                        else:
                            st.warning("No villas assigned.")

                        with st.form(f"add_v_form_{selected_c_email}"):
                            col_m1, col_m2 = st.columns(2)
                            with col_m1: assign_sub = st.selectbox("Sub-Community", options=sub_community_list, key="assign_sub_pool")
                            with col_m2: assign_villa = st.text_input("Villa Number", key="assign_villa_pool").strip()
                            if st.form_submit_button("Add Villa to Pool", type="primary"):
                                if assign_sub and assign_villa:
                                    run_query(supabase.table("coach_villas").insert({"coach_email": selected_c_email, "sub_community": assign_sub, "villa": assign_villa}))
                                    st.success("Added!")
                                    time.sleep(1)
                                    st.rerun()

            with st.expander("🚨 Security & Lockout Management", expanded=False):
                rst_email = st.text_input("Resident Email Address", key="admin_rst_input").strip().lower()
                if st.button("Clear Lockouts & Cooldowns", type="primary", key="admin_rst_submit_btn"):
                    if rst_email:
                        now_ts = get_utc_plus_4().isoformat()
                        for c in get_all_villas_for_email(rst_email):
                            run_query(supabase.table("villa_claims").update({"verified_at": now_ts, "status": "approved"}).eq("id", c["id"]))
                        add_log("Admin Reset", f"Admin cleared restrictions for {rst_email}")
                        st.success("Cleared!")
                        time.sleep(1)
                        st.rerun()

col1, col2 = st.columns([1, 5])
with col1: st.markdown('<img src="https://raw.githubusercontent.com/mahadevbk/courtbooking/main/qr-code.miracourtbooking.streamlit.app.png" height="100">', unsafe_allow_html=True)
with col2: st.markdown("""
    <div style='background-color: #0d5384; padding: 1rem; border-left: 5px solid #fff500; border-radius: 0.5rem; color: white;'>
    Built with ❤️ using <a href='https://streamlit.io/' style='color: #ccff00;'>Streamlit</a> — free and open source.
    <a href='https://devs-scripts.streamlit.app/' style='color: #ccff00;'>Other Scripts by dev</a> on Streamlit.
    </div>
    """, unsafe_allow_html=True)
