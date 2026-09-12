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
from PIL import Image, ImageDraw, ImageFont # For dynamic JPG card rendering
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
    "Abhishek", "Adam", "Adebayo", "Alesia", "Ameen", "Angelo", "Arlan", "Asim", "Carlos", "Charbel", "Dev", "Elie", "Farheen", "Francois",
    "Goncalo", "Guru", "Hana", "Harith", "Hatem", "Hisham", "Katya", "KD", "Khaled", "Laurent", "Leina", "Lisa", "Marko", "Matthieu",
    "Mei", "Melissa", "Mostafa", "Mustafa", "Nick", "Nikki", "Peter", "Rena", "Ricardo", "Riin", "Saket", "SAS", "Sheila", "Sofia",
    "Timo", "Vik", "Wael", "Yann", "Yousef"
]

MAX_ACTIVE_BOOKINGS_DEFAULT = 6
MAX_ACTIVE_BOOKINGS_DONOR = 8
MAX_VILLAS_PER_COACH = 10

# ==========================================
# --- COACH FEATURE MASTER SWITCH ---
# ==========================================
# The coach pooling facility (coach login, coach dashboard, coach admin panel, coach
# explainer, coach detection at login) is fully built but suppressed for regular users
# for now, since it triggered a negative reaction from residents. Every coach-related
# code path below is gated behind this single flag rather than deleted, so the whole
# feature can be switched back on later just by setting this to True — no other code
# changes needed. All existing coach-made bookings have been migrated to plain villa
# ownership (coach_email cleared) via the one-time admin migration tool, so nothing is
# stuck in a coach-only state while this is off.
COACH_FEATURE_ENABLED = False

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
    """True if this Sub Community + Villa belongs to a recorded donor (whitespace and case normalized)."""
    norm_sub = " ".join(str(sub_community).lower().split())
    norm_villa = str(villa).strip()
    return (norm_sub, norm_villa) in DONOR_VILLAS

# "Legends of Mira" donor perk window: the elevated 8-slot quota runs for 6 months from
# 1 Sept 2026 and reverts to the normal 6-slot quota afterwards.
DONOR_PERK_START_DATE = datetime(2026, 9, 1).date()
DONOR_PERK_END_DATE = datetime(2027, 3, 1).date()  # exclusive — this date itself is back to 6

def get_active_booking_limit(sub_community, villa, for_date=None):
    """Donor villas get an increased quota of 8 active bookings instead of 6 — but only for
    slots dated within the perk's 6-month window (2026-09-01 up to, not including,
    2027-03-01). A slot dated on/after the end date is capped at the normal 6-slot limit
    even while the perk is still running for nearer-term dates.

    This is the "intelligent transition" mechanism: because the booking horizon only looks
    ~14 days ahead, the only way a donor villa could ever end up holding more than 6 active
    bookings that are still active *after* the perk ends is if we let the elevated limit
    apply to bookings dated past the cutoff. By gating on the requested slot's own date
    instead of just "today", any booking dated on/after 2027-03-01 is already held to 6 the
    whole time it's being created — so nothing needs to be force-cancelled when the perk
    actually ends. Pre-cutoff-dated bookings still enjoy the full 8-slot quota right up
    until their date passes, then simply age out of the active count naturally, as always.

    `for_date` accepts a date/datetime, or 'YYYY-MM-DD' string, for the specific slot being
    booked. If omitted, defaults to today — used for general dashboard-style quota displays
    that aren't tied to one specific date.
    """
    if not is_donor_villa(sub_community, villa):
        return MAX_ACTIVE_BOOKINGS_DEFAULT

    ref_date = for_date if for_date is not None else get_today()
    if isinstance(ref_date, str):
        ref_date = datetime.strptime(ref_date, "%Y-%m-%d").date()
    elif isinstance(ref_date, datetime):
        ref_date = ref_date.date()

    if DONOR_PERK_START_DATE <= ref_date < DONOR_PERK_END_DATE:
        return MAX_ACTIVE_BOOKINGS_DONOR
    return MAX_ACTIVE_BOOKINGS_DEFAULT

def render_donor_legend_banner():
    today = get_today()
    days_left = (DONOR_PERK_END_DATE - today).days
    if today >= DONOR_PERK_END_DATE:
        subtitle = "Your generosity keeps these courts thriving — thank you for your support!"
    elif days_left <= 14:
        subtitle = (
            f"Your enhanced 8-booking allowance winds down in {days_left} day{'s' if days_left != 1 else ''} "
            f"(ends {DONOR_PERK_END_DATE.strftime('%-d %b %Y')}) — bookings made for dates on/after that day "
            "already count toward the normal 6-slot quota, so nothing you've booked will ever need to be cancelled."
        )
    else:
        subtitle = f"Legend status = 8 active bookings, running through {DONOR_PERK_END_DATE.strftime('%-d %b %Y')}. Cheers for the support!"

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
    <div class="legend-banner-sub">"""
        + subtitle
        + """</div>
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
def generate_ics_content(court, date_str, start_hours, sub_community, villa):
    sorted_hours = sorted(start_hours)
    start_h = sorted_hours[0]
    end_h = sorted_hours[-1] + 1
    
    date_clean = date_str.replace("-", "")
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    
    ics_text = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Mira Court Booking//EN
CALSCALE:GREGORIAN
METHOD:PUBLISH
BEGIN:VEVENT
UID:mira-booking-{date_str}-{start_h}-{court.replace(' ', '')}@miracourtbooking
DTSTAMP:{now_stamp}
DTSTART:{date_clean}T{start_h:02d}0000
DTEND:{date_clean}T{end_h:02d}0000
SUMMARY:🎾 Tennis at {court}
DESCRIPTION:Court reservation at {court} for {sub_community} - Villa {villa}.
LOCATION:{court} Tennis Court, Mira, Dubai, UAE
END:VEVENT
END:VCALENDAR"""
    return ics_text.encode("utf-8")

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
            key=f"download_jpg_{key}",
            use_container_width=True
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

        if action_type == "created":
            subject = f"✅ Booking Confirmed: {court} ({formatted_date})"
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
              <meta charset="utf-8">
              <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f4f7f6; margin: 0; padding: 0; }}
                .email-wrapper {{ max-width: 600px; margin: 30px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 15px rgba(0,0,0,0.05); border: 1px solid #e1e8ed; }}
                .email-header {{ background: linear-gradient(135deg, #0d5384, #052134); padding: 30px; text-align: center; color: #ffffff; }}
                .email-header h1 {{ margin: 0; font-size: 22px; font-weight: 700; letter-spacing: 0.5px; }}
                .email-body {{ padding: 30px; color: #333333; line-height: 1.6; }}
                .info-card {{ background-color: #f8fafc; border-radius: 8px; padding: 20px; margin: 20px 0; border: 1px solid #e2e8f0; border-left: 5px solid #4CAF50; }}
                .info-row {{ margin: 8px 0; font-size: 15px; color: #2d3748; }}
                .footer {{ background-color: #f8fafc; padding: 20px; text-align: center; font-size: 12px; color: #718096; border-top: 1px solid #e2e8f0; }}
              </style>
            </head>
            <body>
              <div class="email-wrapper">
                <div class="email-header">
                  <h1>✅ Court Booking Confirmed</h1>
                </div>
                <div class="email-body">
                  <p>Hello Resident,</p>
                  <p>Your court reservation has been successfully created.</p>
                  
                  <div class="info-card">
                    <div class="info-row"><b>Court:</b> {court}</div>
                    <div class="info-row"><b>Date:</b> {formatted_date}</div>
                    <div class="info-row"><b>Time Slot:</b> {time_display}</div>
                    <div class="info-row"><b>Duration:</b> {duration} hour(s)</div>
                    <div class="info-row"><b>Residence:</b> {sub_community} - Villa {villa}</div>
                  </div>
                </div>
                <div class="footer">
                  Mira Court Booking App • Community Fair-Use Solution
                </div>
              </div>
            </body>
            </html>
            """
            send_gmail_smtp(recipient_email, subject, html_content)
        elif action_type == "deleted":
            subject = f"❌ Booking Cancelled: {court} ({formatted_date})"
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
              <meta charset="utf-8">
              <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f4f7f6; margin: 0; padding: 0; }}
                .email-wrapper {{ max-width: 600px; margin: 30px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 15px rgba(0,0,0,0.05); border: 1px solid #e1e8ed; }}
                .email-header {{ background: linear-gradient(135deg, #c0392b, #962d22); padding: 30px; text-align: center; color: #ffffff; }}
                .email-header h1 {{ margin: 0; font-size: 22px; font-weight: 700; letter-spacing: 0.5px; }}
                .email-body {{ padding: 30px; color: #333333; line-height: 1.6; }}
                .info-card {{ background-color: #f8fafc; border-radius: 8px; border-left: 5px solid #c0392b; padding: 20px; margin: 20px 0; border: 1px solid #e2e8f0; border-left: 5px solid #c0392b; }}
                .info-row {{ margin: 8px 0; font-size: 15px; color: #2d3748; }}
                .footer {{ background-color: #f8fafc; padding: 20px; text-align: center; font-size: 12px; color: #718096; border-top: 1px solid #e2e8f0; }}
              </style>
            </head>
            <body>
              <div class="email-wrapper">
                <div class="email-header">
                  <h1>❌ Court Booking Cancelled</h1>
                </div>
                <div class="email-body">
                  <p>Hello Resident,</p>
                  <p>Your court reservation has been successfully cancelled.</p>
                  
                  <div class="info-card">
                    <div class="info-row"><b>Court:</b> {court}</div>
                    <div class="info-row"><b>Date:</b> {formatted_date}</div>
                    <div class="info-row"><b>Time Slot:</b> {time_display}</div>
                    <div class="info-row"><b>Duration:</b> {duration} hour(s)</div>
                    <div class="info-row"><b>Residence:</b> {sub_community} - Villa {villa}</div>
                  </div>
                </div>
                <div class="footer">
                  Mira Court Booking App • Community Fair-Use Solution
                </div>
              </div>
            </body>
            </html>
            """
            send_gmail_smtp(recipient_email, subject, html_content)
    except Exception as e:
        print(f"Error sending cancellation email: {e}")

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
            html_content = f"<h3>Coach Booking Notification</h3><p>Coach <b>{coach_name}</b> has reserved a slot using your allocation.</p><p>Court: {court}<br>Date: {formatted_date}<br>Time: {hour}:00 - {hour+1}:00</p>"
        else:
            subject = f"🔄 Coach {coach_name} Cancelled a Booking (Quota Returned)"
            html_content = f"<h3>Coach Cancellation Notification</h3><p>Coach <b>{coach_name}</b> has cancelled a session that was booked using your allocation. Your limits have been restored.</p><p>Court: {court}<br>Date: {formatted_date}<br>Time: {hour}:00 - {hour+1}:00</p>"
        send_gmail_smtp(owner_email, subject, html_content)

def send_all_bookings_summary(villa, sub_community, bookings_list, recipient_email, coach_label=None):
    """Emails a summary of active bookings. Pass villa/sub_community for a single-residence
    summary, or leave them as None and pass coach_label for a coach summary spanning multiple
    villas (each booking dict's own 'v'/'sc' keys are then used for per-item details)."""
    if not recipient_email or "@" not in recipient_email or not bookings_list:
        return False
    try:
        items_html = ""
        for b in bookings_list:
            item_villa = b.get('v', villa)
            item_sub = b.get('sc', sub_community)
            b_date = datetime.strptime(b['date'], '%Y-%m-%d')
            formatted_date = b_date.strftime('%A, %b %d, %Y')
            start_time = min(b['start_hours'])
            end_time = max(b['start_hours']) + 1
            time_display = f"{start_time:02d}:00 - {end_time:02d}:00"
            duration = len(b['start_hours'])
            id_list = sorted(b['ids'])
            id_display = f"#{id_list[0]}" if len(id_list) == 1 else f"#{id_list[0]}-{id_list[-1]}"
            g_url = get_google_calendar_url(b['court'], b['date'], b['start_hours'], item_sub, item_villa)
            villa_line = f"<p style=\"margin: 4px 0; color: #2d3748;\"><b>Villa:</b> {item_sub} - Villa {item_villa}</p>" if coach_label else ""

            items_html += f"""
            <div style="background: #f8fafc; padding: 18px; border-radius: 8px; border-left: 5px solid #0d5384; margin-bottom: 15px; border: 1px solid #e2e8f0; border-left: 5px solid #0d5384;">
                <p style="margin: 4px 0; color: #718096; font-size: 0.8rem;"><b>Reference:</b> {id_display}</p>
                <p style="margin: 4px 0; font-size: 1.1rem; color: #0d5384;"><b>🎾 {b['court']}</b></p>
                <p style="margin: 4px 0; color: #2d3748;"><b>Date:</b> {formatted_date}</p>
                <p style="margin: 4px 0; color: #2d3748;"><b>Time:</b> {time_display} ({duration} hour(s))</p>
                {villa_line}
                <p style="margin: 8px 0 4px 0;"><a href="{g_url}" target="_blank" style="color: #0d5384; font-size: 13px; text-decoration: none; font-weight: bold;">📅 Add to Google Calendar</a></p>
            </div>
            """

        if coach_label:
            subject = f"📋 Summary of All Active Coach Bookings ({coach_label})"
            greeting = f"Hello Coach {coach_label},"
            intro = "Here is the complete overview of all your active coaching sessions across your assigned villas:"
        else:
            subject = f"📋 Summary of All Active Court Bookings ({sub_community} Villa {villa})"
            greeting = "Hello Resident,"
            intro = f"Here is the complete overview of all your active court reservations for <b>{sub_community} - Villa {villa}</b>:"

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
          <meta charset="utf-8">
          <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f4f7f6; margin: 0; padding: 0; }}
            .email-wrapper {{ max-width: 600px; margin: 30px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 15px rgba(0,0,0,0.05); border: 1px solid #e1e8ed; }}
            .email-header {{ background: linear-gradient(135deg, #0d5384, #052134); padding: 30px; text-align: center; color: #ffffff; }}
            .email-header h1 {{ margin: 0; font-size: 22px; font-weight: 700; letter-spacing: 0.5px; }}
            .email-body {{ padding: 30px; color: #333333; line-height: 1.6; }}
            .footer {{ background-color: #f8fafc; padding: 20px; text-align: center; font-size: 12px; color: #718096; border-top: 1px solid #e2e8f0; }}
          </style>
        </head>
        <body>
          <div class="email-wrapper">
            <div class="email-header">
              <h1>📋 Active Bookings Summary</h1>
            </div>
            <div class="email-body">
              <p>{greeting}</p>
              <p>{intro}</p>
              <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 20px 0;">
              {items_html}
            </div>
            <div class="footer">
              Mira Court Booking App • Community Fair-Use Solution
            </div>
          </div>
        </body>
        </html>
        """
        return send_gmail_smtp(recipient_email, subject, html_content)
    except Exception as e:
        print(f"Error sending summary email: {e}")
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
    "Mira 1": 400,
    "Mira 2": 400,
    "Mira 3": 400,
    "Mira 4": 600,
    "Mira 5": 400,
    "Mira Oasis 1": 500,
    "Mira Oasis 2": 500,
    "Mira Oasis 3": 500
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
    """Like get_today(), but the booking window rolls over to the next day at 21:00
    (9 PM) instead of at midnight, so a new day's slots become bookable earlier in
    the evening rather than right at 12 AM."""
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

def purge_old_logs(days=90):
    """Deletes activity-log rows older than `days` to keep the logs table small and the
    Community Activity Log tab fast to load. Timestamps are stored as naive UTC+4
    isoformat strings by add_log(), so the cutoff is computed the same way."""
    try:
        cutoff = (get_utc_plus_4() - timedelta(days=days)).isoformat()
        run_query(supabase.table("logs").delete().lt("timestamp", cutoff))
    except Exception:
        pass

@st.cache_data(ttl=21600, show_spinner=False)  # at most once every 6 hours per running app instance
def _run_scheduled_log_retention():
    purge_old_logs(days=90)
    return True

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
                        add_log("Purge Out-of-Range", f"Deleted invalid claim for {sub} Villa {v_num}")

        bookings_res = run_query(supabase.table("bookings").select("id, sub_community, villa, court, date, start_hour"))
        if bookings_res and bookings_res.data:
            for booking in bookings_res.data:
                sub = booking.get("sub_community")
                v_str = str(booking.get("villa", ""))
                max_v = SUB_COMMUNITY_VILLA_LIMITS.get(sub)
                if max_v and v_str.isdigit():
                    v_num = int(v_str)
                    if not (1 <= v_num <= max_v):
                        run_query(supabase.table("bookings").delete().eq("id", booking["id"]))
                        log_detail = f"{sub} Villa {v_num} cancelled {booking['court']} for {booking['date']} at {booking['start_hour']:02d}:00"
                        add_log("Booking Deleted", log_detail)
                        add_log("Purge Out-of-Range", f"Deleted invalid booking for {sub} Villa {v_num}")
    except Exception:
        pass

def mask_email(email_str):
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
            if any(term in details_lower for term in ["cleared cooldown", "reset cooldown", "cleared restrictions", "ownership reset", "wrong villa"]):
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
            is_cleared = False
            for cleared_key, reset_time in cleared_entities:
                if (cleared_key == email_val or (fp and cleared_key == fp)) and reset_time >= ts:
                    is_cleared = True
                    break
            if is_cleared:
                continue
            expiry = ts + timedelta(hours=96)
            if expiry > now:
                hrs_left = max(1, int((expiry - now).total_seconds() // 3600))
                primary_key = email_val or fp
                if primary_key not in active_penalties:
                    active_penalties[primary_key] = {
                        "email": email_val or "No Email (Hardware Lock)",
                        "fingerprint": fp or "N/A",
                        "penalized_at": ts.strftime('%b %d, %H:%M'),
                        "hours_left": hrs_left,
                        "details": details
                    }
    blacklisted_list = []
    for key, data in active_penalties.items():
        if data["email"] and "@" in data["email"]:
            claims = get_all_villas_for_email(data["email"])
            villas = [f"{c['sub_community']} - {c['villa']}" for c in claims]
        else:
            claims = []
            villas = []
        data["villas"] = villas
        data["claims"] = claims
        blacklisted_list.append(data)
    return blacklisted_list

def get_all_claimed_villas():
    res = run_query(supabase.table("villa_claims").select("sub_community, villa"))
    if not res or not res.data: return []
    unique_villas = sorted(list(set([f"{row['sub_community']} - {row['villa']}" for row in res.data])))
    return unique_villas

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
    response = run_query(supabase.table("bookings").select("id").eq("court", court).eq("date", date_str).eq("start_hour", start_hour))
    return len(response.data) > 0 if response and response.data else False

def is_slot_in_past(date_str, start_hour):
    now = get_utc_plus_4()
    if date_str < now.strftime('%Y-%m-%d'): return True
    if date_str == now.strftime('%Y-%m-%d') and (start_hour < now.hour or (start_hour == now.hour and now.minute > 0)): return True
    return False


# --- CORE BOOKING & DELETION FUNCTIONS (UPDATED FOR COACH POOL) ---

def book_slot(villa, sub_community, court, date_str, start_hour, fingerprint=None, coach_email=None):
    try:
        payload = {
            "villa": villa,
            "sub_community": sub_community,
            "court": court,
            "date": date_str,
            "start_hour": start_hour
        }
        if coach_email:
            payload["coach_email"] = coach_email
            
        run_query(supabase.table("bookings").insert(payload))
        
        if coach_email:
            log_detail = f"Coach {coach_email} booked {court} for {date_str} at {start_hour:02d}:00 using {sub_community} Villa {villa}"
        else:
            log_detail = f"{sub_community} Villa {villa} booked {court} for {date_str} at {start_hour:02d}:00"
            
        add_log("Booking Created", log_detail, fingerprint=fingerprint)
        return True
    except APIError as e:
        if e.code == "23505":
            return False
        raise e
    except Exception:
        return False

def delete_booking(booking_id, villa, sub_community, fingerprint=None, coach_email=None):
    record = run_query(supabase.table("bookings").select("court, date, start_hour").eq("id", booking_id).single())
    if record and record.data:
        b = record.data
        if coach_email:
            log_detail = f"Coach {coach_email} cancelled {b['court']} for {b['date']} at {b['start_hour']:02d}:00 (Quota returned to {sub_community} Villa {villa})"
        else:
            log_detail = f"{sub_community} Villa {villa} cancelled {b['court']} for {b['date']} at {b['start_hour']:02d}:00"
        add_log("Booking Deleted", log_detail, fingerprint=fingerprint)
    run_query(supabase.table("bookings").delete().eq("id", booking_id))

# --- COACH SPECIFIC HELPER FUNCTIONS ---

def get_coach_dashboard_stats(coach_email):
    """Calculates cumulative quota available across a coach's assigned villa pool."""
    villas_res = run_query(supabase.table("coach_villas").select("sub_community, villa").eq("coach_email", coach_email))
    assigned_villas = villas_res.data if villas_res and villas_res.data else []
    
    total_allowed = 0
    total_active = 0
    
    for v in assigned_villas:
        limit = get_active_booking_limit(v['sub_community'], v['villa']) 
        active = get_active_bookings_count(v['villa'], v['sub_community']) 
        
        total_allowed += limit
        total_active += active
        
    return assigned_villas, total_allowed, total_active

def process_coach_booking(coach_email, coach_name, court, date_str, start_hours, fingerprint=None):
    """Attempts to iterate through a coach's villa pool and assign slots intelligently."""
    assigned_villas, _, _ = get_coach_dashboard_stats(coach_email)
    booked_slots = []
    
    for hour in start_hours:
        slot_booked = False
        
        # Iterate through the coach's cache to find a valid villa for THIS hour
        for v in assigned_villas:
            sub = v['sub_community']
            villa_num = v['villa']
            
            active_count = get_active_bookings_count(villa_num, sub)
            active_limit = get_active_booking_limit(sub, villa_num, for_date=date_str)
            daily_count = get_daily_bookings_count(villa_num, sub, date_str)
            
            # Check if this specific villa can take the slot
            if active_count < active_limit and daily_count < 2:
                # Attempt to book
                if book_slot(villa_num, sub, court, date_str, hour, fingerprint, coach_email=coach_email):
                    booked_slots.append({
                        "hour": hour,
                        "villa": villa_num,
                        "sub_community": sub
                    })
                    
                    notify_owner_of_coach_booking(coach_name, villa_num, sub, court, date_str, hour, action="booked")
                    slot_booked = True
                    break # Move to the next hour
        
        if not slot_booked:
            return False, f"Failed to find available quota for slot {hour}:00 across all assigned villas."
            
    return True, booked_slots

def get_coach_bookings(coach_email):
    """Fetches bookings exclusively tagged with the provided coach_email."""
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

# --- USER & MISC HELPERS ---
def get_user_bookings(villa, sub_community):
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    response = run_query(
        supabase.table("bookings").select("id, court, date, start_hour")
        .eq("villa", villa)
        .eq("sub_community", sub_community)
        .is_("coach_email", "null") # Exclude coach bookings so residents only see their own
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
    if not response or not response.data:
        return []
    history = []
    for row in response.data:
        details = row.get("details", "") or ""
        match = re.match(r"^(.*?) Villa (\S+) (booked|cancelled) ", details)
        if not match:
            # Let's also check if it matches a coach pattern
            coach_match = re.search(r"Coach (.*?) (booked|cancelled).*?using (.*?) Villa (\S+)", details)
            if coach_match:
                c_email, action, sub_comm, villa_num = coach_match.group(1), coach_match.group(2), coach_match.group(3), coach_match.group(4)
                who = f"Coach {c_email} (via {sub_comm} - Villa {villa_num})"
            else:
                continue
        else:
            sub_comm, villa_num, action = match.group(1), match.group(2), match.group(3)
            who = f"{sub_comm} - Villa {villa_num}"
            
        raw_ts = row.get("timestamp", "")
        try:
            ts_display = datetime.fromisoformat(raw_ts).strftime("%b %d, %Y %I:%M %p")
        except Exception:
            ts_display = raw_ts
        history.append({
            "action": "booked" if action == "booked" else "cancelled",
            "who": who,
            "display_time": ts_display,
        })
    return history

@st.cache_data(ttl=60)
def get_logs_last_14_days():
    cutoff = (get_utc_plus_4() - timedelta(days=14)).isoformat()
    response = run_query(
        supabase.table("logs").select("timestamp, event_type, fingerprint, details")
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
        purge_out_of_range_records()
        _run_scheduled_log_retention()
        # database_cleanup.py remains separate but we mimic the call
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

# --- FLOATING ANNOUNCEMENT DIALOGS ---
@st.dialog("🎾 Notice: A Fairer Booking System for Everyone!")
def show_migration_dialog():
    st.markdown("""
    Hi neighbors! 👋
    To keep court bookings fair and stop people from booking under fake or multiple villas, we are introducing a simple **one-time email verification**.
    **What this means for you:**
    * **Fair access for real residents:** Keeps slots open for those who actually live here.
    * **One-time only:** Just enter your email and a 6-digit code once — your device will remember you automatically after that!
    * **Family friendly:** Up to 2 emails can be linked to your villa (e.g. partners or housemates).
    ---
    Please enter your resident email below to receive your 6-digit verification code.
    💬 *Please reach out to Dev in case you have any queries.*
    """)
    if st.button("Got it — Continue 🎾", type="primary", use_container_width=True):
        st.session_state.seen_migration_notice = True
        st.rerun(scope="app")

@st.dialog("⚠️ Villa Sniping Detected")
def show_sniping_warning_dialog(other_villas):
    villas_text = ", ".join(other_villas)
    st.markdown(f"""
    **Potential Misuse Warning**
    Our system detected that this device has recently reserved court slots across multiple villas (**{villas_text}**) within the last 24 hours.
    Please do not abuse the booking system by hopping across multiple properties. **All villas associated with your account have been flagged for review.**
    🚨 **Notice:** If you log out and switch to another residence to reserve courts, an automatic **4-day security cooldown** will be imposed on all properties linked to your account.
    ---
    *If you believe this is incorrect, please contact Dev in Court Maintenance.*
    """)
    if st.button("I Understand — Proceed", type="primary", use_container_width=True):
        st.session_state.seen_sniping_warning = True
        st.rerun(scope="app")

@st.dialog("🚫 Account Suspended: Villa Sniping Lockout")
def show_sniping_lockout_dialog(hours_remaining):
    st.error(
        f"### 4-Day Security Cooldown Imposed\n\n"
        f"Cross-villa sniping was detected from this device across 4 or more properties within 24 hours.\n\n"
        f"In accordance with community fair-use rules, **all bookings and access for your associated villas are locked for the next {hours_remaining} hours**.\n\n"
        f"💬 *If you believe this is an error or require an exception, please contact Dev directly via Court Maintenance.*"
    )
    if st.button("Close / Logout", use_container_width=True):
        logout_action()

# --- ZERO-LATENCY TOKEN AUTH ---
AUTH_SALT = "mira_court_booking_salt_2026"

def encode_auth_token(sub_community, villa, email):
    if not email:
        return ""
    payload = f"{sub_community}::{villa}::{email}"
    sig = hashlib.sha256(f"{payload}:{AUTH_SALT}".encode()).hexdigest()[:10]
    raw = f"{payload}::{sig}".encode()
    return base64.urlsafe_b64encode(raw).decode()

def decode_auth_token(token_str):
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

def encode_coach_token(email):
    """Signed token used to keep a coach session alive across refreshes, analogous to encode_auth_token."""
    if not email:
        return ""
    sig = hashlib.sha256(f"coach::{email}:{AUTH_SALT}".encode()).hexdigest()[:10]
    raw = f"coach::{email}::{sig}".encode()
    return base64.urlsafe_b64encode(raw).decode()

def decode_coach_token(token_str):
    try:
        raw = base64.urlsafe_b64decode(token_str.encode()).decode()
        parts = raw.split("::")
        if len(parts) == 3 and parts[0] == "coach":
            _, email, sig = parts
            expected_sig = hashlib.sha256(f"coach::{email}:{AUTH_SALT}".encode()).hexdigest()[:10]
            if sig == expected_sig:
                return email
    except Exception:
        pass
    return None

def _finish_coach_login(coach_email, coach_name):
    """Shared completion step for both first-time PIN setup and returning PIN login."""
    resolved_uuid = st.session_state.get("device_uuid", "")
    st_javascript(f"""
        localStorage.setItem('court_coach_email', '{coach_email}');
        localStorage.setItem('court_device_uuid', '{resolved_uuid}');
    """, key=f"js_set_coach_storage_{coach_email}")

    st.query_params["cauth"] = encode_coach_token(coach_email)
    st.session_state.authenticated = True
    st.session_state.is_coach = True
    st.session_state.coach_email = coach_email
    st.session_state.coach_name = coach_name
    st.session_state.auth_step = "input_email"

    add_log("Coach Login", f"Coach {coach_email} logged in", fingerprint=resolved_uuid)
    st.success(f"✅ Welcome, Coach {coach_name}!")
    time.sleep(1.0)
    st.rerun()

def logout_action():
    st_javascript("""
        localStorage.removeItem('court_villa_lock');
        localStorage.removeItem('court_verified_email');
        localStorage.removeItem('verified_claim_info');
        localStorage.removeItem('supabase_refresh_token');
        localStorage.removeItem('court_coach_email');
        setTimeout(() => { window.location.href = window.location.origin + window.location.pathname; }, 150);
    """, key="js_logout")
    st.session_state.clear()
    st.query_params.clear()
    st.info("Logging out... Please wait.")
    time.sleep(0.8)
    st.rerun()

def _attempt_resident_login(otp_sub, otp_villa, otp_email_input):
    """Runs the normal resident validation + OTP/PIN routing. Shared by the main login form
    and by the 'Continue as Resident' choice offered to emails that are registered as both
    a coach and a resident."""
    max_allowed = SUB_COMMUNITY_VILLA_LIMITS.get(otp_sub, 9999)
    if not otp_sub or not otp_villa:
        st.error("Please specify your Sub-Community and Villa Number.")
    elif not otp_villa.isdigit() or not (1 <= int(otp_villa) <= max_allowed):
        st.error(f"Invalid villa number for {otp_sub}. Must be between 1 and {max_allowed}.")
    elif not otp_email_input or "@" not in otp_email_input:
        st.error("Please provide a valid email address.")
    elif is_disposable_email(otp_email_input):
        st.error("Disposable/temporary email domains are not allowed. Please use a personal or work email.")
    else:
        existing_claim = get_existing_claim(otp_sub, otp_villa, otp_email_input)
        current_claims_count = get_villa_claims_count(otp_sub, otp_villa)
        email_villas_count = get_email_claimed_villas_count(otp_email_input)
        is_on_cooldown, hours_left = get_recent_claim_cooldown(otp_sub, otp_villa, otp_email_input)
        target_pair = f"{otp_sub}::{otp_villa}"
        current_uuid = st.session_state.get("device_uuid", "device_pending")
        uuid_villas = get_uuid_claimed_villas(current_uuid)

        if not existing_claim and is_on_cooldown:
            st.error(
                f"🚫 Security Lockout: This villa ({otp_sub} - Villa {otp_villa}) already has 2 registered emails, "
                f"with an active 72-hour ownership change cooldown ({hours_left} hours remaining). "
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
            add_log("Access Denied", f"Device UUID {current_uuid} blocked from requesting access for 4th villa ({otp_sub} Villa {otp_villa})", fingerprint=current_uuid)
        else:
            st.session_state.auth_email = otp_email_input
            st.session_state.auth_sub = otp_sub
            st.session_state.auth_villa = otp_villa

            if existing_claim and existing_claim.get("pin"):
                st.session_state.auth_existing_claim = existing_claim
                st.session_state.auth_step = "enter_pin"
                st.rerun()
            else:
                with st.spinner("Sending 6-digit verification code..."):
                    try:
                        supabase.auth.sign_in_with_otp({"email": otp_email_input})
                        st.session_state.auth_step = "verify_otp"
                        st.success(f"✅ Code sent! Please check your inbox at {otp_email_input}")
                        time.sleep(1.2)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to send code: {str(e)}")

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

# --- FULL FRAME PAGE ---
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

@st.cache_data(ttl=3600, show_spinner=False)
def get_live_active_users_count():
    """Approximates 'active users' as the number of approved villa_claims rows — i.e. real
    households currently verified in the app — pulled live from Supabase. This is refreshed
    at most once an hour. Streamlit Community Cloud's own analytics (page views/unique
    viewers) aren't exposed to the running app via any API, only in the 'Manage app' owner
    dashboard, so we can't pull that number in; this is the closest real, live equivalent
    we have direct access to."""
    try:
        res = run_query(supabase.table("villa_claims").select("id", count="exact").eq("status", "approved"))
        return res.count if res and res.count is not None else None
    except Exception:
        return None

# --- MAIN APP ---
st.subheader("🎾 Book that Court ...")    
st.caption("An Un-Official & Community Driven Booking Solution.")
_live_user_count = get_live_active_users_count()
_user_count_label = f"{_live_user_count:,}" if _live_user_count else "2,450"
st.markdown(
    "<p style='color:#ccff00; font-weight:700; margin-top:-8px;'>"
    f"Serving {_user_count_label} active users, the app is community coded and funded."
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
""", key="js_device_fetch")

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
    """, key="js_ua_check")
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

url_coach_token = st.query_params.get("cauth")
if COACH_FEATURE_ENABLED and url_coach_token and not st.session_state.authenticated:
    verified_coach_email = decode_coach_token(url_coach_token)
    if verified_coach_email:
        coach_restore = run_query(supabase.table("coach_accounts").select("*").eq("email", verified_coach_email).eq("is_active", True))
        if coach_restore and coach_restore.data:
            st.session_state.authenticated = True
            st.session_state.is_coach = True
            st.session_state.coach_email = verified_coach_email
            st.session_state.coach_name = coach_restore.data[0].get("coach_name", "Coach")

if COACH_FEATURE_ENABLED and not st.session_state.authenticated:
    stored_coach_email = st_javascript("localStorage.getItem('court_coach_email') || '';", key="js_stored_coach")
    if isinstance(stored_coach_email, str) and stored_coach_email and "@" in stored_coach_email:
        coach_restore2 = run_query(supabase.table("coach_accounts").select("*").eq("email", stored_coach_email).eq("is_active", True))
        if coach_restore2 and coach_restore2.data:
            st.session_state.authenticated = True
            st.session_state.is_coach = True
            st.session_state.coach_email = stored_coach_email
            st.session_state.coach_name = coach_restore2.data[0].get("coach_name", "Coach")
            st.query_params["cauth"] = encode_coach_token(stored_coach_email)
            st.rerun()

if not st.session_state.authenticated:
    stored_bundle = st_javascript("(localStorage.getItem('court_villa_lock') || 'no_lock') + ':::' + (localStorage.getItem('court_verified_email') || '') + ':::' + (localStorage.getItem('verified_claim_info') || '');", key="js_stored_bundle")
    
    if isinstance(stored_bundle, str) and ":::" in stored_bundle:
        parts = stored_bundle.split(":::")
        s_lock = parts[0] if len(parts) > 0 else ""
        s_email = parts[1] if len(parts) > 1 else ""
        s_claim = parts[2] if len(parts) > 2 else ""
        
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
                st.session_state.is_coach = False
                st.query_params["auth"] = encode_auth_token(target_sub, target_villa, s_email)
                st.rerun()

        elif s_lock and s_lock != "no_lock" and "-" in s_lock:
            try:
                locked_sub, locked_villa = s_lock.rsplit("-", 1)
                st.session_state.prefill_sub = locked_sub
                st.session_state.prefill_villa = locked_villa
            except Exception:
                pass

if not st.session_state.authenticated:
    if "seen_migration_notice" not in st.session_state:
        st.session_state.seen_migration_notice = False

    if not st.session_state.seen_migration_notice:
        show_migration_dialog()

    if "auth_step" not in st.session_state:
        st.session_state.auth_step = "input_email"
    if "auth_email" not in st.session_state:
        st.session_state.auth_email = ""
    if "auth_sub" not in st.session_state:
        st.session_state.auth_sub = None
    if "auth_villa" not in st.session_state:
        st.session_state.auth_villa = None

    default_sub_idx = None
    prefill_sub = st.session_state.get("prefill_sub")
    if prefill_sub in sub_community_list:
        default_sub_idx = sub_community_list.index(prefill_sub)
    default_villa = st.session_state.get("prefill_villa", "")

    st.subheader("🛡️ Resident Email Verification")
    st.caption("Secure login for your residence. Max 2 resident emails per villa.")
    
    if st.session_state.auth_step == "input_email":
        col_v1, col_v2 = st.columns(2)
        with col_v1:
            otp_sub = st.selectbox("Sub-Community", options=sub_community_list, index=default_sub_idx, key="otp_sub_select")
        with col_v2:
            max_limit = SUB_COMMUNITY_VILLA_LIMITS.get(otp_sub, 500)
            otp_villa_raw = st.text_input(f"Villa Number (1 - {max_limit})", value=default_villa, key="otp_villa_text").strip()
            otp_villa = "".join(filter(str.isdigit, otp_villa_raw))

        otp_email_input = st.text_input("Email Address", placeholder="name@example.com", key="otp_email_text").strip().lower()

        if st.button("Continue", type="primary", width='stretch'):
            # Discreet coach detection: recognized coach emails skip the sub-community/villa
            # requirement entirely and are routed straight into the coach PIN flow — unless
            # the same email is also a registered resident, in which case we ask which
            # account they want to use.
            # Suppressed while COACH_FEATURE_ENABLED is False — see master switch above.
            coach_lookup = None
            if COACH_FEATURE_ENABLED and otp_email_input and "@" in otp_email_input:
                coach_res = run_query(supabase.table("coach_accounts").select("*").eq("email", otp_email_input).eq("is_active", True))
                if coach_res and coach_res.data:
                    coach_lookup = coach_res.data[0]

            if coach_lookup:
                also_resident = get_email_claimed_villas_count(otp_email_input) > 0
                if also_resident:
                    st.session_state.pending_coach_lookup = coach_lookup
                    st.session_state.pending_otp_email = otp_email_input
                    st.session_state.pending_otp_sub = otp_sub
                    st.session_state.pending_otp_villa = otp_villa
                    st.session_state.auth_step = "coach_or_resident_choice"
                    st.rerun()
                else:
                    st.session_state.coach_email = otp_email_input
                    st.session_state.coach_name = coach_lookup.get("coach_name", "Coach")
                    st.session_state.coach_pin_hash = coach_lookup.get("pin")
                    if coach_lookup.get("pin"):
                        st.session_state.auth_step = "coach_enter_pin"
                    else:
                        st.session_state.auth_step = "coach_set_pin"
                    st.rerun()
            else:
                _attempt_resident_login(otp_sub, otp_villa, otp_email_input)

        st.write("")
        if st.button("🚪 Reset / Clear Details", width='stretch', key="reg_logout_presend"):
            logout_action()

    elif st.session_state.auth_step == "coach_or_resident_choice":
        st.info("This email is registered as **both** a Coach and a Resident. How would you like to continue?")
        cor1, cor2 = st.columns(2)
        with cor1:
            if st.button("🎾 Continue as Coach", type="primary", width='stretch', key="cor_as_coach"):
                coach_lookup = st.session_state.pending_coach_lookup
                st.session_state.coach_email = st.session_state.pending_otp_email
                st.session_state.coach_name = coach_lookup.get("coach_name", "Coach")
                st.session_state.coach_pin_hash = coach_lookup.get("pin")
                st.session_state.auth_step = "coach_enter_pin" if coach_lookup.get("pin") else "coach_set_pin"
                st.rerun()
        with cor2:
            if st.button("🏡 Continue as Resident", width='stretch', key="cor_as_resident"):
                _attempt_resident_login(
                    st.session_state.pending_otp_sub,
                    st.session_state.pending_otp_villa,
                    st.session_state.pending_otp_email,
                )
        st.write("")
        if st.button("🚪 Cancel", width='stretch', key="cor_cancel"):
            st.session_state.auth_step = "input_email"
            st.rerun()

    elif st.session_state.auth_step == "enter_pin":
        st.info(f"Welcome back! Enter your 4-digit PIN for **{st.session_state.auth_sub} - Villa {st.session_state.auth_villa}**.")
        pin_input = st.text_input("4-Digit PIN", type="password", max_chars=4, key="pin_input_text").strip()

        c1, c2, c3 = st.columns([1.5, 1.5, 1.2])
        with c1:
            if st.button("Log In", type="primary", width='stretch'):
                if pin_input == st.session_state.auth_existing_claim.get("pin"):
                    now_ts = get_utc_plus_4().isoformat()
                    resolved_uuid = st.session_state.device_uuid
                    target_sub = st.session_state.auth_sub
                    target_villa = st.session_state.auth_villa
                    verified_email = st.session_state.auth_email

                    run_query(supabase.table("villa_claims").update({
                        "verified_at": now_ts,
                        "fingerprint": resolved_uuid,
                        "status": "approved"
                    }).eq("id", st.session_state.auth_existing_claim["id"]))

                    fallback_choice = f"{target_sub}-{target_villa}"
                    claim_bundle = f"{target_sub}::{target_villa}"

                    st_javascript(f"""
                        localStorage.setItem('court_villa_lock', '{fallback_choice}');
                        localStorage.setItem('court_verified_email', '{verified_email}');
                        localStorage.setItem('verified_claim_info', '{claim_bundle}');
                        localStorage.setItem('court_device_uuid', '{resolved_uuid}');
                    """, key=f"js_set_storage_pin_{target_sub}_{target_villa}_{verified_email}")

                    st.query_params["auth"] = encode_auth_token(target_sub, target_villa, verified_email)
                    st.session_state.sub_community = target_sub
                    st.session_state.villa = target_villa
                    st.session_state.verified_email = verified_email
                    st.session_state.authenticated = True
                    st.session_state.is_coach = False
                    st.session_state.auth_step = "input_email"
                    
                    st.success("✅ PIN verified! Logging you in...")
                    time.sleep(1.2)
                    st.rerun()
                else:
                    st.error("❌ Incorrect PIN.")
        with c2:
            if st.button("Forgot PIN? Send OTP", width='stretch'):
                with st.spinner("Sending 6-digit verification code..."):
                    try:
                        supabase.auth.sign_in_with_otp({"email": st.session_state.auth_email})
                        st.session_state.auth_step = "verify_otp"
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to send code: {str(e)}")
        with c3:
            if st.button("Cancel", width='stretch'):
                st.session_state.auth_step = "input_email"
                st.rerun()

    elif st.session_state.auth_step == "coach_set_pin":
        st.info(f"Welcome, **{st.session_state.coach_name}**! Since this is your first login, please set a 4-digit PIN for quick access next time.")
        new_coach_pin = st.text_input("Set your 4-Digit PIN", type="password", max_chars=4, key="new_coach_pin_text").strip()
        confirm_coach_pin = st.text_input("Confirm PIN", type="password", max_chars=4, key="confirm_coach_pin_text").strip()

        cc1, cc2 = st.columns([2, 1])
        with cc1:
            if st.button("Save PIN & Log In", type="primary", width='stretch', key="coach_setpin_btn"):
                if not new_coach_pin or len(new_coach_pin) != 4 or not new_coach_pin.isdigit():
                    st.error("Please enter a valid 4-digit PIN (numbers only).")
                elif new_coach_pin != confirm_coach_pin:
                    st.error("PINs do not match.")
                else:
                    run_query(supabase.table("coach_accounts").update({"pin": new_coach_pin}).eq("email", st.session_state.coach_email))
                    _finish_coach_login(st.session_state.coach_email, st.session_state.coach_name)
        with cc2:
            if st.button("Cancel", width='stretch', key="coach_setpin_cancel"):
                st.session_state.auth_step = "input_email"
                st.rerun()

    elif st.session_state.auth_step == "coach_enter_pin":
        st.info(f"Welcome back, **{st.session_state.coach_name}**! Enter your 4-digit PIN.")
        coach_pin_input = st.text_input("4-Digit PIN", type="password", max_chars=4, key="coach_pin_input_text").strip()

        cc1, cc2 = st.columns([2, 1])
        with cc1:
            if st.button("Log In", type="primary", width='stretch', key="coach_pin_login_btn"):
                if coach_pin_input and coach_pin_input == st.session_state.get("coach_pin_hash"):
                    _finish_coach_login(st.session_state.coach_email, st.session_state.coach_name)
                else:
                    st.error("❌ Incorrect PIN.")
        with cc2:
            if st.button("Cancel", width='stretch', key="coach_pin_cancel"):
                st.session_state.auth_step = "input_email"
                st.rerun()
        st.caption("Forgot your PIN? Ask your admin to reset it for you.")

    elif st.session_state.auth_step == "verify_otp":
        st.info(f"Enter the 6-digit code sent to **{st.session_state.auth_email}** for **{st.session_state.auth_sub} - Villa {st.session_state.auth_villa}**.")
        st.caption("Check your spam/junk folder if the email does not appear in your inbox within a minute.")
        token_input = st.text_input("Enter 6-digit code", max_chars=6, key="otp_token_text").strip()
        
        st.write("")
        st.markdown("##### 🔐 Set a Static PIN")
        st.caption("Create a 4-digit PIN so you can log in instantly next time without waiting for an OTP.")
        new_pin_input = st.text_input("Set your 4-Digit PIN", type="password", max_chars=4, key="new_pin_text").strip()
        
        c1, c2, c3 = st.columns([1.5, 1.2, 1.2])
        with c1:
            if st.button("Verify & Save PIN", type="primary", width='stretch'):
                if not token_input or len(token_input) != 6:
                    st.error("Please enter a 6-digit verification code.")
                elif not new_pin_input or len(new_pin_input) != 4 or not new_pin_input.isdigit():
                    st.error("Please set a valid 4-digit PIN for future use (numbers only).")
                else:
                    with st.spinner("Verifying code..."):
                        try:
                            res = supabase.auth.verify_otp({
                                "email": st.session_state.auth_email,
                                "token": token_input,
                                "type": "email"
                            })
                            if res and res.session:
                                target_sub = st.session_state.auth_sub
                                target_villa = st.session_state.auth_villa
                                verified_email = st.session_state.auth_email
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
                                        "verified_at": now_ts,
                                        "pin": new_pin_input
                                    }))
                                    add_log("Villa Claim", f"{target_sub} Villa {target_villa} claimed by {verified_email}", fingerprint=resolved_uuid)
                                else:
                                    run_query(supabase.table("villa_claims").update({
                                        "verified_at": now_ts,
                                        "fingerprint": resolved_uuid,
                                        "status": "approved",
                                        "pin": new_pin_input
                                    }).eq("id", existing["id"]))

                                fallback_choice = f"{target_sub}-{target_villa}"
                                claim_bundle = f"{target_sub}::{target_villa}"
                                
                                st_javascript(f"""
                                    localStorage.setItem('court_villa_lock', '{fallback_choice}');
                                    localStorage.setItem('court_verified_email', '{verified_email}');
                                    localStorage.setItem('verified_claim_info', '{claim_bundle}');
                                    localStorage.setItem('supabase_refresh_token', '{refresh_tok}');
                                    localStorage.setItem('court_device_uuid', '{resolved_uuid}');
                                """, key=f"js_set_storage_{target_sub}_{target_villa}_{verified_email}")

                                st.query_params["auth"] = encode_auth_token(target_sub, target_villa, verified_email)
                                st.session_state.sub_community = target_sub
                                st.session_state.villa = target_villa
                                st.session_state.verified_email = verified_email
                                st.session_state.authenticated = True
                                st.session_state.is_coach = False
                                st.session_state.auth_step = "input_email"
                                
                                st.balloons()
                                st.success("✅ Verified and PIN saved successfully! Logging you in...")
                                time.sleep(1.2)
                                st.rerun()
                            else:
                                st.error("Verification failed. Please check the OTP code.")
                        except Exception as e:
                            st.error(f"Invalid code or verification error: {str(e)}")
        with c2:
            if st.button("🔄 Resend Code", width='stretch'):
                with st.spinner("Resending code..."):
                    try:
                        supabase.auth.sign_in_with_otp({"email": st.session_state.auth_email})
                        st.toast(f"A new 6-digit code has been sent to {st.session_state.auth_email}!")
                    except Exception as e:
                        st.error(f"Could not resend code: {str(e)}")
        with c3:
            if st.button("Cancel / Change", width='stretch'):
                st.session_state.auth_step = "input_email"
                st.rerun()
        
        st.write("")
        if st.button("🚪 Reset / Clear Details", width='stretch', key="reg_logout_postsend"):
            logout_action()

    st.write("")
    with st.expander("🛠️ Admin Emergency Console", expanded=(st.query_params.get("admin") == "true")):
        st.caption("Unlock accounts, reset cooldowns, or clear restrictions if locked out.")
        login_admin_pwd = st.text_input("Enter Admin Password", type="password", key="login_screen_admin_pwd")
        if login_admin_pwd:
            if login_admin_pwd == st.secrets.get("ADMIN_PASSWORD", "admin123"):
                st.success("Admin Access Granted")
                rst_email = st.text_input("Resident Email Address to Restore", placeholder="resident@example.com", key="login_rst_email").strip().lower()
                if st.button("🔓 Clear Restrictions & Restore Clean Access", type="primary", key="login_rst_btn", use_container_width=True):
                    if not rst_email or "@" not in rst_email:
                        st.error("Please enter a valid email address.")
                    else:
                        now_ts = get_utc_plus_4().isoformat()
                        claims = get_all_villas_for_email(rst_email)
                        for c in claims:
                            run_query(supabase.table("villa_claims").update({
                                "verified_at": now_ts,
                                "status": "approved"
                            }).eq("id", c["id"]))

                        add_log(
                            "Admin Reset",
                            f"Admin cleared restrictions and reset cooldown for {rst_email} across {len(claims)} villas via emergency console"
                        )
                        st.success(f"✅ Restrictions cleared for {rst_email}! Cooldown reset for all {len(claims)} associated villas.")
                        time.sleep(1.2)
                        st.rerun()
            else:
                st.error("Incorrect Password")
    
    st.stop()

# --- SHARED TAB RENDERERS (used by both resident & coach dashboards) ---
# ==========================================
def render_court_maintenance_tab(reporter_label, current_device):
    """Shared Court Maintenance tab body, used by both the resident and coach dashboards."""
    import base64
    st.subheader("🛠️ Court Maintenance")
    st.markdown(f"""
    <div style="background-color:#0d5384; padding:25px; border-radius:15px; border-left: 8px solid #ccff00;">
        <h2 style="color:#ccff00; margin-top:0;">Power in Numbers</h2>
        <p style="font-size:1.1em; line-height:1.6;">
            This hub centralizes every court issue to facilitate <b>mass maintenance requests</b>. By reporting collectively, we ensure 
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
                if img.mode in ("RGBA", "P"): img = img.convert("RGB")
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
                        "reported_by": reporter_label,
                        "is_fixed": False
                    }))
                    add_log("Maintenance Reported", f"Issue reported for {m_court} by {reporter_label}", fingerprint=current_device)
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
                    display: block; text-align: center; padding: 12px; background-color: #28a745; color: white; 
                    border-radius: 8px; text-decoration: none; font-family: 'Audiowide', cursive; margin-bottom: 20px; border: 2px solid #ccff00;
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
    st.markdown("### 🛠️ Admin Maintenance Controls")
    admin_maint_pwd = st.text_input("Enter Admin Password to Unlock Controls", type="password", key="tab4_admin_pass")

    if admin_maint_pwd:
        if admin_maint_pwd == st.secrets.get("ADMIN_PASSWORD", "admin123"):
            st.success("Admin Access Granted")

            with st.expander("🔑 Admin Resident Bypass (Authorize & Switch Active Resident)", expanded=False):
                st.caption("Directly authorize a resident email without OTP and immediately switch this session to them.")
                b_col1, b_col2 = st.columns(2)
                with b_col1:
                    bypass_sub = st.selectbox("Sub-Community", options=sub_community_list, key="tab4_bypass_sub")
                with b_col2:
                    bypass_villa_raw = st.text_input("Villa Number", key="tab4_bypass_villa").strip()
                    bypass_villa = "".join(filter(str.isdigit, bypass_villa_raw))
                bypass_email = st.text_input("Resident Email Address", placeholder="resident@example.com", key="tab4_bypass_email").strip().lower()

                if st.button("Authorize & Switch Session to Resident", type="primary", width='stretch', key="tab4_bypass_btn"):
                    max_allowed_bypass = SUB_COMMUNITY_VILLA_LIMITS.get(bypass_sub, 9999)
                    if not bypass_sub or not bypass_villa or not bypass_email or "@" not in bypass_email:
                        st.error("Please specify a valid Sub-Community, Villa, and Email Address.")
                    elif not bypass_villa.isdigit() or not (1 <= int(bypass_villa) <= max_allowed_bypass):
                        st.error(f"Invalid villa number for {bypass_sub}. Must be between 1 and {max_allowed_bypass}.")
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
                        """, key=f"js_set_storage_bypass_{bypass_sub}_{bypass_villa}_{bypass_email}")

                        st.session_state.sub_community = bypass_sub
                        st.session_state.villa = bypass_villa
                        st.session_state.verified_email = bypass_email
                        st.session_state.authenticated = True
                        st.session_state.is_coach = False
                        st.query_params["auth"] = encode_auth_token(bypass_sub, bypass_villa, bypass_email)
                        st.success(f"Granted access! Switched active session to {bypass_sub} Villa {bypass_villa} ({bypass_email}).")
                        time.sleep(1.0)
                        st.rerun()
        else:
            st.error("Incorrect Password")


def render_coach_admin_panel(key_prefix="cam"):
    """Comprehensive coach account admin panel: create/deactivate/delete coaches, reset PINs,
    and manage each coach's villa pool (pick a coach, see their villas as a radio list to
    delete one, or add a new villa via sub-community + villa-number dropdowns).
    Coach identity is keyed off email (guaranteed unique and always present) rather than a
    numeric 'id' column, since not every coach_accounts table has one.
    """
    with st.expander("🎾 Coach & Pool Management", expanded=True):
        st.markdown("### 1. Add New Coach")
        col_c1, col_c2 = st.columns(2)
        with col_c1:
            new_c_email = st.text_input("New Coach Email", key=f"{key_prefix}_new_coach_email_input").strip().lower()
        with col_c2:
            new_c_name = st.text_input("Coach Name", key=f"{key_prefix}_new_coach_name_input").strip()
        if st.button("Create Coach Profile", type="primary", use_container_width=True):
            if new_c_email and new_c_name:
                run_query(supabase.table("coach_accounts").insert({"email": new_c_email, "coach_name": new_c_name, "is_active": True}))
                st.success(f"Coach {new_c_name} created successfully. They can log in from the normal login screen using this email.")
                time.sleep(1)
                st.rerun()
            else:
                st.error("Please provide both email and name.")

        st.divider()
        st.markdown(f"### 2. Manage Villas for a Coach (max {MAX_VILLAS_PER_COACH} villas per coach)")

        all_coaches_res = run_query(supabase.table("coach_accounts").select("*").order("coach_name"))
        all_coaches = all_coaches_res.data if all_coaches_res and all_coaches_res.data else []

        if not all_coaches:
            st.info("No coach accounts created yet. Add one above first.")
        else:
            coach_options = ["-- Select a Coach --"] + [f"{c.get('coach_name', 'Coach')} ({c['email']})" for c in all_coaches]
            selected_coach_label = st.selectbox("Select Coach", options=coach_options, key=f"{key_prefix}_villa_coach_select")

            if selected_coach_label != "-- Select a Coach --":
                selected_idx = coach_options.index(selected_coach_label) - 1
                selected_coach = all_coaches[selected_idx]
                selected_coach_email = selected_coach["email"]

                c_villas_res = run_query(supabase.table("coach_villas").select("*").eq("coach_email", selected_coach_email))
                c_villas = c_villas_res.data if c_villas_res and c_villas_res.data else []

                st.caption(f"{len(c_villas)}/{MAX_VILLAS_PER_COACH} villas currently assigned to {selected_coach.get('coach_name', 'this coach')}")

                if c_villas:
                    villa_radio_labels = [f"{v['sub_community']} - Villa {v['villa']}" for v in c_villas]
                    villa_to_delete_label = st.radio("Existing villas (select one to remove)", options=villa_radio_labels, key=f"{key_prefix}_villa_radio_{selected_coach_email}")
                    if st.button("🗑️ Delete Selected Villa", key=f"{key_prefix}_delete_villa_btn_{selected_coach_email}", use_container_width=True):
                        villa_to_delete = c_villas[villa_radio_labels.index(villa_to_delete_label)]
                        if villa_to_delete.get("id") is not None:
                            run_query(supabase.table("coach_villas").delete().eq("id", villa_to_delete["id"]))
                        else:
                            run_query(
                                supabase.table("coach_villas").delete()
                                .eq("coach_email", selected_coach_email)
                                .eq("sub_community", villa_to_delete["sub_community"])
                                .eq("villa", villa_to_delete["villa"])
                            )
                        st.success(f"Removed {villa_to_delete_label} from {selected_coach.get('coach_name', 'this coach')}.")
                        time.sleep(1)
                        st.rerun()
                else:
                    st.caption("No villas assigned to this coach yet.")

                st.divider()
                st.markdown("**Add a Villa to this Coach**")
                col_m1, col_m2 = st.columns(2)
                with col_m1:
                    assign_sub = st.selectbox("Sub-Community", options=sub_community_list, key=f"{key_prefix}_map_sub_input_{selected_coach_email}")
                with col_m2:
                    map_villa_limit = SUB_COMMUNITY_VILLA_LIMITS.get(assign_sub, 500)
                    assign_villa = st.selectbox("Villa Number", options=[str(n) for n in range(1, map_villa_limit + 1)], key=f"{key_prefix}_map_villa_input_{selected_coach_email}")

                if st.button("➕ Add Villa to Coach", type="primary", use_container_width=True, key=f"{key_prefix}_add_villa_btn_{selected_coach_email}"):
                    if len(c_villas) >= MAX_VILLAS_PER_COACH:
                        st.error(f"🚫 This coach already has {len(c_villas)} villas assigned — the maximum is {MAX_VILLAS_PER_COACH}. Remove one above before adding another.")
                    else:
                        run_query(supabase.table("coach_villas").insert({
                            "coach_email": selected_coach_email,
                            "sub_community": assign_sub,
                            "villa": assign_villa
                        }))
                        st.success(f"Successfully mapped {assign_sub} Villa {assign_villa} to {selected_coach_email}.")
                        time.sleep(1)
                        st.rerun()

        st.divider()
        st.markdown("### 3. Manage Existing Coaches")

        if not all_coaches:
            st.info("No coach accounts created yet.")
        else:
            for c in all_coaches:
                c_email = c["email"]
                c_villas_count_res = run_query(supabase.table("coach_villas").select("id", count="exact").eq("coach_email", c_email))
                c_villas_count = c_villas_count_res.count if c_villas_count_res and c_villas_count_res.count is not None else 0
                status_label = "🟢 Active" if c.get("is_active", True) else "🔴 Deactivated"
                pin_label = "PIN set" if c.get("pin") else "No PIN yet (will be asked to set one on next login)"

                with st.container(border=True):
                    st.markdown(f"**{c.get('coach_name', 'Coach')}** — `{c_email}` — {status_label}")
                    st.caption(f"{pin_label} • {c_villas_count}/{MAX_VILLAS_PER_COACH} villas assigned")

                    cc1, cc2, cc3 = st.columns(3)
                    with cc1:
                        if st.button("🔑 Reset PIN", key=f"{key_prefix}_reset_pin_{c_email}", use_container_width=True):
                            run_query(supabase.table("coach_accounts").update({"pin": None}).eq("email", c_email))
                            add_log("Admin Reset", f"Admin reset PIN for coach {c_email}")
                            st.success(f"PIN cleared for {c.get('coach_name', 'this coach')}. They'll set a new one on next login.")
                            time.sleep(1)
                            st.rerun()
                    with cc2:
                        toggle_label = "⏸️ Deactivate" if c.get("is_active", True) else "▶️ Reactivate"
                        if st.button(toggle_label, key=f"{key_prefix}_toggle_active_{c_email}", use_container_width=True):
                            run_query(supabase.table("coach_accounts").update({"is_active": not c.get("is_active", True)}).eq("email", c_email))
                            st.success(f"{c.get('coach_name', 'Coach')} {'deactivated' if c.get('is_active', True) else 'reactivated'}.")
                            time.sleep(1)
                            st.rerun()
                    with cc3:
                        if st.button("🗑️ Delete Coach", key=f"{key_prefix}_delete_coach_{c_email}", use_container_width=True):
                            run_query(supabase.table("coach_villas").delete().eq("coach_email", c_email))
                            run_query(supabase.table("coach_accounts").delete().eq("email", c_email))
                            add_log("Admin Reset", f"Admin deleted coach {c_email} and their villa mappings")
                            st.success(f"Deleted {c.get('coach_name', 'this coach')} and their villa assignments.")
                            time.sleep(1)
                            st.rerun()

                    with st.expander(f"✏️ Edit Profile ({c.get('coach_name', 'Coach')} — {c_email})"):
                        edit_col1, edit_col2 = st.columns(2)
                        with edit_col1:
                            edited_email = st.text_input(
                                "Email", value=c_email, key=f"{key_prefix}_edit_email_{c_email}"
                            ).strip().lower()
                        with edit_col2:
                            edited_name = st.text_input(
                                "Coach Name", value=c.get("coach_name", ""), key=f"{key_prefix}_edit_name_{c_email}"
                            ).strip()

                        if st.button("💾 Save Changes", key=f"{key_prefix}_save_edit_{c_email}", type="primary", use_container_width=True):
                            if not edited_email or "@" not in edited_email:
                                st.error("Please enter a valid email address (must contain '@').")
                            elif not edited_name:
                                st.error("Coach name cannot be empty.")
                            else:
                                email_changed = edited_email != c_email
                                duplicate = False
                                if email_changed:
                                    dup_check = run_query(
                                        supabase.table("coach_accounts").select("email").eq("email", edited_email)
                                    )
                                    if dup_check and dup_check.data:
                                        duplicate = True

                                if duplicate:
                                    st.error(f"Another coach account already uses {edited_email}. Choose a different email.")
                                elif not email_changed:
                                    # Name-only change: safe to update the existing row in place.
                                    run_query(
                                        supabase.table("coach_accounts")
                                        .update({"coach_name": edited_name})
                                        .eq("email", c_email)
                                    )
                                    add_log("Admin Edit", f"Admin renamed coach {c_email} to '{edited_name}'")
                                    st.success(f"Profile updated: {edited_name} ({edited_email})")
                                    time.sleep(1.2)
                                    st.rerun()
                                else:
                                    # email is the primary key here and coach_villas / bookings reference it
                                    # via a foreign key, so we can't UPDATE it in place (Postgres rejects
                                    # changing a parent key while child rows still point at the old value).
                                    # Instead: insert a new parent row with the corrected email, re-point the
                                    # children at it, then remove the old parent row.
                                    try:
                                        run_query(
                                            supabase.table("coach_accounts").insert({
                                                "email": edited_email,
                                                "coach_name": edited_name,
                                                "pin": c.get("pin"),
                                                "is_active": c.get("is_active", True),
                                            })
                                        )
                                        run_query(
                                            supabase.table("coach_villas")
                                            .update({"coach_email": edited_email})
                                            .eq("coach_email", c_email)
                                        )
                                        run_query(
                                            supabase.table("bookings")
                                            .update({"coach_email": edited_email})
                                            .eq("coach_email", c_email)
                                        )
                                        run_query(
                                            supabase.table("coach_accounts").delete().eq("email", c_email)
                                        )
                                        add_log(
                                            "Admin Edit",
                                            f"Admin updated coach profile {c_email} -> {edited_email} (name: {edited_name})",
                                        )
                                        st.success(f"Profile updated: {edited_name} ({edited_email})")
                                        st.info("This coach's email changed — if they're logged in on a device, they'll need to log in again with the new email.")
                                        time.sleep(1.2)
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"Failed to update email — no changes were left partially applied beyond what's shown here. Details: {e}")

def render_activity_log_tab(current_device):
    """Shared Community Activity Log tab body, used by both the resident and coach dashboards."""
    if COACH_FEATURE_ENABLED:
        with st.expander("🎾 Coach Account Set Up"):
            st.markdown("""
Coach accounts exist for tennis coaches who train residents across **several villas**, so they can manage all their sessions from one login instead of juggling separate villa credentials.

**Why a coach account works differently from a normal resident login:**

1. **No need to log out and log in for several villas.** A coach logs in once with their own email and PIN, and their account is linked to a *pool* of the villas they coach for. They can book a court for any of those villas without switching accounts.
2. **The actual quota of each villa remains unchanged.** A coach account does not create extra bookings capacity out of thin air — every booking a coach makes is drawn from that specific villa's own existing allowance (6 active bookings, or 8 for donor villas, with a 2-per-day cap). The coach is simply using the villa owner's quota on their behalf, with the owner's consent.

**How it works:**
- Each coach is assigned a pool of up to **10 villas** by the admin.
- When a coach books a slot, the app automatically finds a villa in their pool that still has room under its normal active/daily limits.
- For a 2-hour session, if one villa doesn't have enough quota left, the app will **intelligently split the booking** — e.g. 1 hour drawn from one villa's quota and the 2nd hour from another villa in the pool — so the session still gets booked without breaching any individual villa's limits.
- The villa owner receives an email notification whenever a coach books or cancels a session using their quota, so they always know when their allowance has been used.
- On the schedule/availability grid, coach bookings appear exactly like any other booking for that villa — but a coach's own "My Bookings" list only shows sessions *they* booked, and an owner's "My Bookings" list only shows sessions *they personally* booked (coach bookings are kept separate so the two views don't mix).

**Limitations & rules:**
- A coach cannot exceed a villa's normal active-bookings limit or its 2-per-day limit — the same fair-use rules that apply to residents apply to every villa in a coach's pool.
- A coach account can be linked to a maximum of **10 villas**.
- First login requires the coach to set a 4-digit PIN; on every login after that, they just enter their PIN.
- Coaches cannot reset their own PIN — **only the admin can reset a coach's PIN** if it's forgotten.
- Cancelling a coach-booked session immediately returns that quota to the villa it was drawn from, and the owner is notified.
- A coach cannot book, view, or cancel anything for a villa that isn't in their assigned pool.

**Setting up a coach account:** Coach accounts are not self-service. If you're a coach who needs an account, or a resident who wants to authorize a coach to book on your villa's behalf, please **contact Dev** with the coach's name, email address, and the sub-community + villa number(s) to link — the admin will create the account and map the villas for you.
            """)

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
            (log_df['event_type'] != "Auto-Book Ledger") &
            (~log_df['details'].str.contains("System-Synced", case=False, na=False))
        )
        if not is_admin:
            filters &= (log_df['event_type'] != "Limit Enforcement")
            # Hide anything coach-related from the resident-facing view entirely — not just
            # "Coach Login", but coach profile admin actions (Admin Edit, PIN resets, deletions)
            # and any booking/cancellation made using a coach's pooled quota.
            is_coach_related = (
                (log_df['event_type'] == "Coach Login") |
                (log_df['event_type'] == "Admin Edit") |
                (log_df['details'].str.contains(r'\bcoach\b', case=False, na=False, regex=True))
            )
            filters &= ~is_coach_related

        display_df = log_df[filters].copy()        
        display_df['details'] = display_df['details'].str.replace(r'⟦FP:.*?⟧⟦IP:.*?⟧ ', '', regex=True)
        if not is_admin:
            display_df['details'] = display_df['details'].apply(mask_emails_in_text)

        cols = ['timestamp', 'event_type', 'details']
        display_df['timestamp'] = pd.to_datetime(display_df['timestamp'], format='ISO8601').dt.strftime('%b %d, %H:%M')

        def style_rows(row):
            styles = [''] * len(row)
            if row.event_type in ["Booking Created", "Villa Claim"]: styles[1] = 'background-color: #d4edda; color: #155724; font-weight: bold;'
            elif row.event_type in ["Booking Deleted", "Booking Cancelled", "Villa Claim Removed"]: styles[1] = 'background-color: #f8d7da; color: #721c24; font-weight: bold;'
            elif row.event_type in ["Access Denied", "Claim Held for Review", "Sniping Warning"]: styles[1] = 'background-color: #ffcc00; color: black; font-weight: bold;'
            elif row.event_type in ["Sniping Penalty", "Sniping Lockout"]: styles[1] = 'background-color: #ff4d4d; color: white; font-weight: bold;'
            return styles

        st.dataframe(display_df[cols].style.apply(style_rows, axis=1), hide_index=True, width="stretch")
    else: st.info("No activity.")

    st.divider()
    st.subheader("🛠️ Admin Tools")
    admin_pass = st.text_input("Admin Password", type="password", key="log_admin_pass")

    if is_admin:
        col_adm1, col_adm2 = st.columns([3, 1])
        with col_adm1: st.success("Admin Access Granted")
        with col_adm2:
            if st.button("🔒 Exit Admin Mode", type="secondary", use_container_width=True):
                st.session_state.pop("log_admin_pass", None)
                st.rerun()

        admin_tabs = st.tabs([
            "📊 Overview",
            "👥 Residents & Access",
            "🚫 Security & Lockouts",
            "📅 Bookings & Villas",
            "🎾 Coach Facility",
            "🗄️ Backup & Housekeeping",
        ])

        with admin_tabs[0]:
            st.markdown("### At a Glance")
            ov_c1, ov_c2, ov_c3 = st.columns(3)
            with ov_c1:
                _ov_blacklist = get_blacklisted_accounts()
                st.metric("Active Lockouts", len(_ov_blacklist))
            with ov_c2:
                _ov_old_logs_res = run_query(
                    supabase.table("logs").select("id", count="exact")
                    .lt("timestamp", (get_utc_plus_4() - timedelta(days=90)).isoformat())
                )
                _ov_old_logs = _ov_old_logs_res.count if _ov_old_logs_res and _ov_old_logs_res.count is not None else 0
                st.metric("Logs Due for Purge (90d+)", _ov_old_logs)
            with ov_c3:
                _ov_live_users = get_live_active_users_count()
                st.metric("Live Active Users", f"{_ov_live_users:,}" if _ov_live_users else "—")

            ov_c4, ov_c5, ov_c6 = st.columns(3)
            with ov_c4:
                if COACH_FEATURE_ENABLED:
                    st.metric("Coach Facility", "Enabled")
                else:
                    _ov_pending_res = run_query(
                        supabase.table("bookings").select("id", count="exact").not_.is_("coach_email", "null")
                    )
                    _ov_pending = _ov_pending_res.count if _ov_pending_res and _ov_pending_res.count is not None else 0
                    st.metric("Coach Bookings Pending Migration", _ov_pending)
            with ov_c5:
                _ov_today_str = get_today().strftime("%Y-%m-%d")
                _ov_today_bookings_res = run_query(supabase.table("bookings").select("id", count="exact").eq("date", _ov_today_str))
                _ov_today_bookings = _ov_today_bookings_res.count if _ov_today_bookings_res and _ov_today_bookings_res.count is not None else 0
                st.metric("Bookings Today", _ov_today_bookings)
            with ov_c6:
                _ov_open_maint_res = run_query(supabase.table("court_maintenance").select("id", count="exact").eq("is_fixed", False))
                _ov_open_maint = _ov_open_maint_res.count if _ov_open_maint_res and _ov_open_maint_res.count is not None else 0
                st.metric("Open Maintenance Issues", _ov_open_maint)

            st.divider()
            _ov_days_left = (DONOR_PERK_END_DATE - get_today()).days
            if get_today() >= DONOR_PERK_END_DATE:
                st.info("🏆 Legends of Mira donor perk has ended — all villas are back to the standard 6-slot limit.")
            else:
                st.info(f"🏆 Legends of Mira donor perk (8 active slots) is running — {_ov_days_left} day(s) left, ends {DONOR_PERK_END_DATE.strftime('%-d %b %Y')}.")

            st.divider()
            st.caption(
                "**Not finding a tool here?** A couple of admin actions live elsewhere because they need a live "
                "session to act on:\n"
                "- **Switch your own session to a resident without OTP** — Court Maint. tab → Admin Maintenance Controls → Admin Resident Bypass.\n"
                "- **Unlock a resident from the login screen itself** (before anyone's signed in) — the 🛠️ Admin Emergency Console at the bottom of the login page."
            )

        with admin_tabs[1]:
            with st.expander("✅ Manually Authorize a Claim (Without OTP)", expanded=True):
                st.markdown("### Manually Authorize Resident Claim (Without OTP)")
                with st.form("manual_claim_form"):
                    man_sub = st.selectbox("Sub-Community", options=sub_community_list, key="admin_man_sub")
                    max_m_limit = SUB_COMMUNITY_VILLA_LIMITS.get(man_sub, 500)
                    man_villa = st.text_input(f"Villa Number (1 - {max_m_limit})", key="admin_man_villa").strip()
                    man_email = st.text_input("Resident Email Address", key="admin_man_email").strip().lower()
                    submitted_claim = st.form_submit_button("Authorize Claim Now", type="primary")
                    if submitted_claim:
                        max_allowed_manual = SUB_COMMUNITY_VILLA_LIMITS.get(man_sub, 9999)
                        if not man_villa or not man_email or "@" not in man_email:
                            st.error("Please provide valid villa and email details.")
                        elif not man_villa.isdigit() or not (1 <= int(man_villa) <= max_allowed_manual):
                            st.error(f"Invalid villa number for {man_sub}. Must be between 1 and {max_allowed_manual}.")
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
                                add_log("Villa Claim", f"Admin manually authorized {man_sub} Villa {man_villa}")
                                st.success(f"Claim created for {man_sub} Villa {man_villa}!")
                                time.sleep(1.5)
                                st.rerun()

            with st.expander("🔍 Inspect & Release Villa Claims", expanded=False):
                st.markdown("### Inspect & Release Claimed Villas")
                all_claimed = get_all_claimed_villas()
                claim_inspect_villa = st.selectbox("Select Claimed Villa to Inspect / Reset", options=["-- Select --"] + all_claimed, key="admin_claimed_villa_select")
                if claim_inspect_villa != "-- Select --":
                    try:
                        c_sub, c_villa = claim_inspect_villa.split(" - ")
                        claims = get_claims_for_villa(c_sub, c_villa)
                        if claims:
                            st.write(f"Active Verified Emails for **{claim_inspect_villa}** ({len(claims)} / 2):")
                            for claim in claims:
                                c_box1, c_box2 = st.columns([3, 1])
                                with c_box1:
                                    st.info(f"📧 **{claim['email']}**  \n*Status:* `{claim['status']}`")
                                with c_box2:
                                    st.write("")
                                    if st.button(f"🔓 Release Claim", key=f"del_claim_{claim['id']}", type="secondary", width="stretch"):
                                        run_query(supabase.table("villa_claims").delete().eq("id", claim['id']))
                                        add_log("Villa Claim Removed", f"Admin released claim for {c_sub} Villa {c_villa}")
                                        st.success(f"Released {claim['email']}!")
                                        time.sleep(1.2)
                                        st.rerun()
                        else:
                            st.info("No active claims found for this villa.")
                    except Exception as e:
                        st.error(f"Error inspecting claims: {str(e)}")

            with st.expander("🔓 Reset Access by Email (Cooldown / Lockout)", expanded=False):
                st.markdown("### Email-Based Cooldown & Lockout Reset")
                col_rst1, col_rst2 = st.columns([3, 1])
                with col_rst1:
                    reset_email_input = st.text_input("Enter Resident Email Address", placeholder="resident@example.com", key="admin_rst_email").strip().lower()
                with col_rst2:
                    st.write(""); st.write("")
                    lookup_pressed = st.button("Search Account", type="primary", use_container_width=True)

                if reset_email_input:
                    email_claims = get_all_villas_for_email(reset_email_input)
                    if email_claims:
                        st.markdown(f"**Properties Linked to `{reset_email_input}` ({len(email_claims)} total):**")
                        for c in email_claims:
                            st.info(f"🏡 **{c['sub_community']} - Villa {c['villa']}** | *Verified:* `{c.get('verified_at', 'Unverified')}`")
                        st.write("")
                        col_e1, col_e2 = st.columns(2)
                        with col_e1:
                            if st.button(f"🔓 Reset Cooldown & Restore Clean Access", type="primary", use_container_width=True, key="email_rst_btn_1"):
                                now_ts = get_utc_plus_4().isoformat()
                                for c in email_claims:
                                    run_query(supabase.table("villa_claims").update({"verified_at": now_ts, "status": "approved"}).eq("id", c["id"]))
                                add_log("Admin Reset", f"Admin reset cooldown for email {reset_email_input}")
                                st.success(f"✅ Successfully cleared lockout for {reset_email_input}!")
                                time.sleep(1.5)
                                st.rerun()
                        with col_e2:
                            if st.button(f"🔄 Reset Ownership (Wrong Villa Mistake)", type="secondary", use_container_width=True, key="email_rst_btn_2"):
                                for c in email_claims:
                                    run_query(supabase.table("villa_claims").delete().eq("id", c["id"]))
                                add_log("Admin Reset", f"Admin deleted all villa claims for email {reset_email_input}")
                                st.success(f"🔄 All villa claims deleted for {reset_email_input}!")
                                time.sleep(1.5)
                                st.rerun()
                    else:
                        st.warning(f"No active property claims found for `{reset_email_input}`.")

        with admin_tabs[2]:
            with st.expander("🚨 Manually Apply Sniping Lockout by Email", expanded=True):
                st.markdown("### Search Associated Villas & Enforce Lockout")
                lockout_email_input = st.text_input("Enter Resident Email Address", placeholder="resident@example.com", key="admin_lockout_email_input").strip().lower()

                if lockout_email_input:
                    associated_claims = get_all_villas_for_email(lockout_email_input)
                    if associated_claims:
                        st.markdown(f"**Villas Associated with `{lockout_email_input}` ({len(associated_claims)} total):**")
                        villa_descriptions = []
                        for c in associated_claims:
                            v_label = f"{c['sub_community']} - Villa {c['villa']}"
                            villa_descriptions.append(v_label)
                            st.info(f"🏡 **{v_label}** | *Verified At:* `{c.get('verified_at', 'N/A')}`")

                        st.write("")
                        if st.button("🚫 Apply Sniping Lockout to Email & Associated Villas", type="primary", use_container_width=True, key="apply_admin_lockout_btn"):
                            villas_str = ", ".join(villa_descriptions)
                            log_msg = f"4-day penalty active for email {lockout_email_input} across properties: {villas_str}"
                            add_log("Sniping Penalty", log_msg, fingerprint="admin_manual_lockout")
                            st.success(f"✅ Sniping lockout successfully applied and logged for `{lockout_email_input}` and associated properties ({villas_str}).")
                            time.sleep(1.5)
                            st.rerun()
                    else:
                        st.warning(f"No villas found associated with email `{lockout_email_input}`.")

            with st.expander("🚫 Active Lockouts & Quota Abuse", expanded=True):
                st.markdown("### Active 4-Day Sniping Lockouts & Quota Abuse")
                blacklisted = get_blacklisted_accounts()
                if not blacklisted:
                    st.success("✅ No active account restrictions or blacklisted devices found.")
                else:
                    options = ["-- Select Blacklisted User --"] + [
                        f"{b['email']} | Villas: ({', '.join(b['villas']) if b['villas'] else 'No linked villa'}) | {b['hours_left']}h left"
                        for b in blacklisted
                    ]
                    selected_item = st.selectbox("Select Restricted Account to Inspect:", options=options, key="admin_blacklist_sel")
                    if selected_item != "-- Select Blacklisted User --":
                        idx = options.index(selected_item) - 1
                        target = blacklisted[idx]
                        with st.container(border=True):
                            st.markdown(f"#### 👤 Account Details: `{target['email']}`")
                            c_info1, c_info2 = st.columns(2)
                            with c_info1:
                                st.write(f"**Lockout Issued:** {target['penalized_at']}")
                                st.write(f"**Remaining Cooldown:** `{target['hours_left']} hours`")
                            with c_info2:
                                st.write(f"**Device Fingerprint:** `{target['fingerprint'][:16]}...`")
                                st.write(f"**Reason:** *{target['details']}*")
                            st.markdown("##### 🏡 Associated Villas:")
                            if target["villas"]:
                                for v_name in target["villas"]:
                                    st.info(f"📍 **{v_name}** (Booking and registration blocked)")
                            else:
                                st.warning("No registered villas currently tied to this email in `villa_claims`.")
                            st.write("")
                            col_r1, col_r2 = st.columns(2)
                            with col_r1:
                                if st.button(f"🔓 Clear Restrictions & Wipe Clean", type="primary", use_container_width=True, key=f"clear_rest_{idx}"):
                                    now_ts = get_utc_plus_4().isoformat()
                                    if target["claims"]:
                                        for c in target["claims"]:
                                            run_query(supabase.table("villa_claims").update({"verified_at": now_ts, "status": "approved"}).eq("id", c["id"]))
                                    add_log("Admin Reset", f"Admin cleared restrictions for {target['email']}", fingerprint=target["fingerprint"])
                                    st.success(f"🎉 Restrictions cleared for {target['email']}.")
                                    time.sleep(1.5)
                                    st.rerun()
                            with col_r2:
                                if st.button(f"🔄 Reset Ownership (Wrong Villa Mistake)", type="secondary", use_container_width=True, key=f"wrong_villa_rst_{idx}"):
                                    if target["claims"]:
                                        for c in target["claims"]:
                                            run_query(supabase.table("villa_claims").delete().eq("id", c["id"]))
                                    add_log("Admin Reset", f"Admin deleted claims for {target['email']}", fingerprint=target["fingerprint"])
                                    st.success(f"🔄 Villa ownership claims deleted for {target['email']}.")
                                    time.sleep(1.5)
                                    st.rerun()

        with admin_tabs[3]:
            with st.expander("📋 Manage Bookings for a Villa", expanded=True):
                st.markdown("### Villa Booking Management")
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
                            edited_df = st.data_editor(
                                df_bookings[["Select", "id", "date", "Time", "court"]],
                                column_config={
                                    "Select": st.column_config.CheckboxColumn("Delete?", default=False),
                                    "id": "ID", "date": "Date", "Time": "Time", "court": "Court"
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
                                            delete_booking(row['id'], villa_num, sub_comm, fingerprint=current_device)
                                    st.success(f"Successfully deleted {len(to_delete)} bookings for {selected_villa}.")
                                    time.sleep(1.5)
                                    st.rerun()
                                else:
                                    st.warning("Please select at least one booking to delete.")
                        else:
                            st.info(f"No bookings found for {selected_villa}.")
                    except Exception as e:
                        st.error(f"Error loading bookings: {str(e)}")

            with st.expander("🔁 Re-open a Date for Legends of Mira Auto-Booking", expanded=False):
                st.caption(
                    "The concealed Legends of Mira auto-booking feature (Mira 1 Villas 229/231/233) "
                    "marks a date as 'handled' the moment it creates a slot for it — and remembers "
                    "that even after the slot is cancelled, so it never recreates a slot someone "
                    "deliberately removed. If you ever want the feature to reconsider a specific "
                    "date (e.g. it was cancelled by mistake, or you want a fresh attempt), clear it here."
                )
                from database_cleanup import get_synced_dates_history
                _ledger_dates = sorted(get_synced_dates_history(supabase, lookback_days=30))
                _today_str_ledger = get_today().strftime("%Y-%m-%d")
                _upcoming_locked = [d for d in _ledger_dates if d >= _today_str_ledger]
                if not _upcoming_locked:
                    st.info("No upcoming dates are currently locked in the auto-book ledger.")
                else:
                    _ledger_pick = st.selectbox("Select a locked date to re-open", options=_upcoming_locked, key="admin_reopen_ledger_date")
                    st.caption(
                        "This only clears the internal marker — it never touches an existing booking. "
                        "If a booking still exists for this date, cancel it separately first if you "
                        "actually want the slot to free up."
                    )
                    if st.button(f"🔓 Re-open {_ledger_pick} for auto-booking", type="primary", use_container_width=True, key="admin_reopen_ledger_btn"):
                        from database_cleanup import clear_auto_book_ledger_date
                        if clear_auto_book_ledger_date(supabase, _ledger_pick):
                            st.success(f"Cleared — {_ledger_pick} will be reconsidered by the auto-book feature on its next run.")
                        else:
                            st.error("Could not clear the ledger entry — please try again.")
                        time.sleep(1.2)
                        st.rerun()

        with admin_tabs[4]:
            if COACH_FEATURE_ENABLED:
                render_coach_admin_panel(key_prefix="activitylog")
            else:
                with st.expander("🔧 Coach Facility (currently disabled) — one-time booking migration"):
                    st.caption(
                        "The coach pooling facility is switched off (`COACH_FEATURE_ENABLED = False` in the code). "
                        "All coach login, coach dashboard, and coach admin tools are hidden from everyone, including here. "
                        "Use the button below once to convert any bookings that were previously made by a coach into "
                        "plain bookings owned by their villa (this just clears the internal coach tag — the booking "
                        "itself, its court, date and time are untouched)."
                    )
                    pending_res = run_query(
                        supabase.table("bookings").select("id", count="exact").not_.is_("coach_email", "null")
                    )
                    pending_count = pending_res.count if pending_res and pending_res.count is not None else 0
                    if pending_count > 0:
                        st.warning(f"{pending_count} booking(s) are still tagged to a coach.")
                        if st.button(f"↩️ Transfer {pending_count} coach booking(s) to villa ownership", type="primary", use_container_width=True):
                            run_query(supabase.table("bookings").update({"coach_email": None}).not_.is_("coach_email", "null"))
                            add_log("Admin Reset", f"Admin migrated {pending_count} coach booking(s) to plain villa ownership (coach facility disabled)")
                            st.success(f"Done — {pending_count} booking(s) now belong to their villa like any other booking.")
                            time.sleep(1.2)
                            st.rerun()
                    else:
                        st.info("No coach-tagged bookings remain — nothing to migrate.")

        with admin_tabs[5]:
            with st.expander("🧹 Activity Log Retention (auto-purges logs older than 3 months)"):
                st.caption(
                    "To keep the logs table small and this tab fast to load, activity log entries older than "
                    "3 months are automatically deleted in the background (checked at most once every few hours). "
                    "This only affects the log table shown above — it never touches bookings or villa claims."
                )
                old_logs_res = run_query(
                    supabase.table("logs").select("id", count="exact")
                    .lt("timestamp", (get_utc_plus_4() - timedelta(days=90)).isoformat())
                )
                old_logs_count = old_logs_res.count if old_logs_res and old_logs_res.count is not None else 0
                st.caption(f"{old_logs_count} log entr{'y is' if old_logs_count == 1 else 'ies are'} currently older than 3 months.")
                if old_logs_count > 0 and st.button(f"🗑️ Purge {old_logs_count} log entr{'y' if old_logs_count == 1 else 'ies'} now", use_container_width=True):
                    purge_old_logs(days=90)
                    st.success("Old log entries purged.")
                    time.sleep(1)
                    st.rerun()

            with st.expander("💾 Full Database Backup", expanded=True):
                st.markdown("### Database Backup (ZIP)")
                st.caption(
                    "Exports every table Supabase holds for this app — not just bookings and logs — as both "
                    "CSV and JSON, plus a single consolidated SQLite file (`full_backup.sqlite`) containing all "
                    "tables together. CSV/JSON are here because you asked for them and they're easy to open in "
                    "Excel/Sheets; the SQLite file is the one worth keeping if you only grab one thing — it's a "
                    "single portable file you can open with any SQL tool (or Python's built-in `sqlite3`) and "
                    "query or join across tables, which a folder of CSVs can't do. Note: this captures table "
                    "*data*, not Supabase's internal schema/constraints (column types, indexes, RLS policies) — "
                    "for a true point-in-time Postgres-level backup including schema, Supabase's own project "
                    "backups (Settings → Database → Backups) or running `pg_dump` against your connection string "
                    "is the gold standard; this in-app export is the fast, no-setup option for everyday safety."
                )

                BACKUP_TABLES = ["bookings", "logs", "villa_claims", "coach_accounts", "coach_villas", "court_maintenance"]

                def _fetch_all_rows(table_name):
                    data = []
                    chunk_size = 1000
                    offset = 0
                    while True:
                        res = run_query(supabase.table(table_name).select("*").range(offset, offset + chunk_size - 1))
                        if not res or res.data is None:
                            break
                        data.extend(res.data)
                        if len(res.data) < chunk_size:
                            break
                        offset += chunk_size
                    return data

                def get_zip_data():
                    import sqlite3, tempfile, os as _os
                    tmp_db_path = None
                    try:
                        today_str = get_today()
                        buf = io.BytesIO()
                        tmp_fd, tmp_db_path = tempfile.mkstemp(suffix=".sqlite")
                        _os.close(tmp_fd)
                        sconn = sqlite3.connect(tmp_db_path)

                        manifest_lines = [
                            f"Court Booking App — Full Database Backup",
                            f"Generated: {get_utc_plus_4().isoformat()} (UTC+4)",
                            "",
                        ]

                        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as vz:
                            for table_name in BACKUP_TABLES:
                                rows = _fetch_all_rows(table_name)
                                df = pd.DataFrame(rows)
                                vz.writestr(f"{table_name}.csv", df.to_csv(index=False))
                                vz.writestr(
                                    f"{table_name}.json",
                                    df.to_json(orient="records", indent=2) if not df.empty else "[]",
                                )
                                if not df.empty:
                                    df.to_sql(table_name, sconn, if_exists="replace", index=False)
                                manifest_lines.append(f"{table_name}: {len(df)} row(s)")
                            sconn.close()

                            with open(tmp_db_path, "rb") as f:
                                vz.writestr(f"full_backup_{today_str}.sqlite", f.read())

                            vz.writestr("MANIFEST.txt", "\n".join(manifest_lines))

                        return buf.getvalue()
                    except Exception as e:
                        st.error(f"Backup Error: {str(e)}")
                        return None
                    finally:
                        if tmp_db_path and _os.path.exists(tmp_db_path):
                            _os.remove(tmp_db_path)

                if st.button("Generate Backup Link"):
                    with st.spinner("Fetching every table from Supabase — this may take a moment..."):
                        data = get_zip_data()
                    if data: st.download_button(label="Click here to Download ZIP", data=data, file_name=f"court_booking_full_backup_{get_today()}.zip", mime="application/zip")
                    else: st.error("Failed to fetch data for backup.")

    elif admin_pass:
        st.error("Incorrect Password")



def render_availability_tab(is_coach, current_device, resident_ctx=None, coach_ctx=None, logout_label="🚪 Logout / Change Villa"):
    """Shared Court Availability tab. The schedule grid, history lookup, community
    insights and villa lookup are common to residents and coaches; only the Quick Book
    action differs (single villa vs. pool booking), so it is branched via is_coach.
    """
    st.subheader("Court Availability")
    date_options = [f"{d.strftime('%Y-%m-%d')} ({d.strftime('%A')})" for d in get_next_14_days()]
    selected_date_full = st.selectbox("Select Date:", date_options, key="tab1_date_select")
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
    st.dataframe(pd.DataFrame(data, index=courts).style.map(color_cell), width="stretch")

    curr_auth = st.query_params.get("auth")
    full_url = f"/?view=full&auth={curr_auth}" if curr_auth else "/?view=full"
    st.link_button("🌐 View Full 14-Day Schedule (Full Page)", url=full_url)

    st.divider()
    st.subheader("🔍 Court Status & Booking History")
    st.caption("Check exactly who currently holds a slot, and see its full booking/cancellation history — useful for avoiding conflicts when a slot changed hands.")
    hist_col1, hist_col2 = st.columns([1, 1])
    with hist_col1:
        hist_court = st.selectbox("Select Court", options=courts, key="hist_court_select")
    with hist_col2:
        hist_hours = get_start_hours_for_date(selected_date)
        if hist_hours:
            hist_hour_labels = [f"{h:02d}:00 - {h+1:02d}:00" for h in hist_hours]
            hist_time_label = st.selectbox("Select Time Slot", options=hist_hour_labels, key="hist_time_select")
            hist_hour = hist_hours[hist_hour_labels.index(hist_time_label)]
        else:
            hist_hour = None
            st.warning("No time slots available for this date.")

    if hist_hour is not None:
        hist_key = (hist_court, hist_hour)
        if hist_key in bookings_with_details:
            st.error(f"🔒 Currently **BOOKED** — {bookings_with_details[hist_key]}")
        else:
            st.success("✅ Currently **AVAILABLE**")

        slot_history = get_slot_history(hist_court, selected_date, hist_hour)
        if slot_history:
            st.write(f"**📜 History for {hist_court} on {selected_date}, {hist_time_label}:**")
            for entry in slot_history:
                if entry["action"] == "booked":
                    st.markdown(f"- 🟢 **Booked** by {entry['who']} — _{entry['display_time']}_")
                else:
                    st.markdown(f"- 🔴 **Cancelled** by {entry['who']} — _{entry['display_time']}_")
            if len(slot_history) > 1:
                st.caption("⚠️ This slot has changed hands more than once — please confirm on-court before assuming exclusive access.")
        else:
            st.caption("No prior booking activity recorded for this slot.")

    st.divider()

    if is_coach:
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
                    start_h = int(q_time.split(":")[0])
                    hours_to_book = list(range(start_h, start_h + q_slots))
                    success, result = process_coach_booking(coach_ctx["coach_email"], coach_ctx["coach_name"], q_court, selected_date, hours_to_book, fingerprint=current_device)
                    if success:
                        st.balloons()
                        st.success("Booked successfully using allocations from: " + ", ".join([f"Villa {r['villa']}" for r in result]))
                        time.sleep(2)
                        st.rerun()
                    else:
                        st.error(result)

        st.divider()
    else:
        villa = resident_ctx["villa"]
        sub_community = resident_ctx["sub_community"]
        verified_user_email = resident_ctx["verified_user_email"]
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
                    active_limit = get_active_booking_limit(sub_community, villa, for_date=selected_date)

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
                    elif active_count + q_slots > active_limit:
                        st.error(f"Limit Reached (Max {active_limit} active). You can book {max(0, active_limit-active_count)} more.")
                        add_log("Access Denied", f"{sub_community} Villa {villa} reached active booking limit ({active_limit})", fingerprint=current_device)
                    elif daily_count + q_slots > 2:
                        st.error(f"Daily Limit Reached (Max 2 per day). You can book {max(0, 2-daily_count)} more today.")
                        add_log("Access Denied", f"{sub_community} Villa {villa} reached daily limit (2) for {selected_date}", fingerprint=current_device)
                    else:
                        success = True
                        booked_slots = []
                        for h in slots_to_book:
                            if book_slot(villa, sub_community, q_court, selected_date, h, fingerprint=current_device):
                                booked_slots.append(h)
                            else:
                                success = False
                                break
                        if success:
                            send_booking_notification_once("created", villa, sub_community, q_court, selected_date, booked_slots, verified_user_email)
                            st.balloons()
                            st.success(f"Booked {q_slots} slot(s) for {q_court} starting at {q_time}")
                            if verified_user_email and "@" in verified_user_email:
                                st.info(f"📧 A confirmation email has been sent to **{verified_user_email}** from **miracourtbooking@gmail.com**. If you don't see it, please check your spam/junk folder.")
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
        look_villa = st.selectbox("Select Villa to see details:", options=["-- Select --"] + villas_active, key="lookup_villa_select")
        if look_villa != "-- Select --":
            active_list = get_active_bookings_for_villa_display(look_villa)
            if active_list: st.selectbox("Active bookings for this villa:", options=active_list, key="lookup_villa_bookings_select")
            else: st.write("No active bookings found for this villa.")

    st.divider()
    if st.button(logout_label, width='stretch', key="tab1_logout"):
        logout_action()

# ==========================================
# --- ROUTING: COACH VIEW VS RESIDENT VIEW ---
# ==========================================

if COACH_FEATURE_ENABLED and st.session_state.get('is_coach'):
    coach_email = st.session_state.coach_email
    coach_name = st.session_state.coach_name
    current_device = st.session_state.get("device_uuid")
    st.success(f"✅ Logged in as Coach: **{coach_name}** (`{coach_email}`)")

    assigned_villas, total_allowed, total_active = get_coach_dashboard_stats(coach_email)

    if assigned_villas:
        villa_summary_bits = []
        for v in assigned_villas:
            v_active = get_active_bookings_count(v['villa'], v['sub_community'])
            v_limit = get_active_booking_limit(v['sub_community'], v['villa'])
            villa_summary_bits.append(f"{v['sub_community']} Villa {v['villa']} ({v_active}/{v_limit})")
        st.caption(" • ".join(villa_summary_bits))
    else:
        st.caption("No villas assigned to your pool yet. Contact your admin.")

    coach_ctx = {"coach_email": coach_email, "coach_name": coach_name}

    c_tab1, c_tab2, c_tab3, c_tab4, c_tab5 = st.tabs(["📅 Availability", "➕ Book", "📋 My Bookings", "🛠️ Court Maint.", "📜 Activity Log"])

    with c_tab1:
        render_availability_tab(
            is_coach=True,
            current_device=current_device,
            coach_ctx=coach_ctx,
            logout_label="🚪 Logout",
        )

    with c_tab2:
        st.subheader("Book using your Villa Pool")
        col1, col2, col3 = st.columns(3)
        col1.metric("Villas in Pool", len(assigned_villas))
        col2.metric("Total Allowed Quota", total_allowed)
        col3.metric("Currently Active in Pool", f"{total_active} / {total_allowed}")

        date_options = [f"{d.strftime('%Y-%m-%d')} ({d.strftime('%A')})" for d in get_next_14_days()]
        date_choice = st.selectbox("Date:", date_options, key="coach_date_select").split(" (")[0]
        court_choice = st.selectbox("Court:", courts, key="coach_court_select")

        all_bookings = run_query(supabase.table("bookings").select("start_hour").eq("court", court_choice).eq("date", date_choice))
        booked_hours = [r['start_hour'] for r in all_bookings.data] if all_bookings and all_bookings.data else []
        free_hours = [h for h in get_start_hours_for_date(date_choice) if h not in booked_hours and not is_slot_in_past(date_choice, h)]

        if not free_hours:
            st.warning("No slots available.")
            time_choice = None
        else:
            time_choice = st.selectbox("Time Slot:", [f"{h:02d}:00 - {h+1:02d}:00" for h in free_hours], key="coach_time_select")

        slots_2_hours = st.checkbox("Book for 2 hours", disabled=(not time_choice or int(time_choice.split(":")[0])+1 not in free_hours))
        st.caption("If a 2-hour session doesn't fit inside one villa's remaining quota, we'll automatically split it across two of your assigned villas.")

        if st.button("🚀 Book as Coach", type="primary"):
            if not time_choice:
                st.error("Select time.")
            else:
                start_h = int(time_choice.split(":")[0])
                hours_to_book = [start_h, start_h+1] if slots_2_hours else [start_h]
                success, result = process_coach_booking(coach_email, coach_name, court_choice, date_choice, hours_to_book, fingerprint=current_device)
                if success:
                    st.balloons()
                    st.success("Booked successfully using allocations from: " + ", ".join([f"Villa {r['villa']}" for r in result]))
                    time.sleep(2)
                    st.rerun()
                else:
                    st.error(result)

        st.divider()
        if st.button("🚪 Logout", width="stretch", key="coach_book_logout"):
            logout_action()

    with c_tab3:
        st.subheader("📋 My Coach Bookings")
        my_coach_b = get_coach_bookings(coach_email)

        merged_coach_bookings = []
        if my_coach_b:
            df_c = pd.DataFrame(my_coach_b).sort_values(['date', 'court', 'sub_community', 'villa', 'start_hour'])
            current_b = None
            for _, row in df_c.iterrows():
                if current_b is None:
                    current_b = {'court': row['court'], 'date': row['date'], 'start_hours': [row['start_hour']], 'ids': [row['id']], 'v': row['villa'], 'sc': row['sub_community']}
                else:
                    if (row['date'] == current_b['date'] and row['court'] == current_b['court'] and row['villa'] == current_b['v'] and row['sub_community'] == current_b['sc'] and row['start_hour'] == max(current_b['start_hours']) + 1):
                        current_b['start_hours'].append(row['start_hour']); current_b['ids'].append(row['id'])
                    else:
                        merged_coach_bookings.append(current_b)
                        current_b = {'court': row['court'], 'date': row['date'], 'start_hours': [row['start_hour']], 'ids': [row['id']], 'v': row['villa'], 'sc': row['sub_community']}
            merged_coach_bookings.append(current_b)

        col_c1, col_c2 = st.columns(2)
        with col_c1:
            st.metric("Active Bookings in Pool", f"{total_active} / {total_allowed}")
        with col_c2:
            st.metric("Villas in Pool", len(assigned_villas))

        if merged_coach_bookings:
            b_col1, b_col2 = st.columns(2)
            with b_col1:
                df_export = pd.DataFrame(my_coach_b)
                df_export['Time'] = df_export['start_hour'].apply(lambda x: f"{x:02d}:00")
                csv_data = convert_df_to_csv(df_export[['id', 'date', 'Time', 'court', 'sub_community', 'villa']])
                st.download_button(label="📥 Export to CSV", data=csv_data, file_name=f"coach_{coach_name}_bookings.csv", mime="text/csv", use_container_width=True)
            with b_col2:
                if st.button("📧 Email Me All My Bookings", use_container_width=True, key="coach_email_summary_btn"):
                    with st.spinner("Sending summary email..."):
                        sent_ok = send_all_bookings_summary(None, None, merged_coach_bookings, coach_email, coach_label=coach_name)
                        if sent_ok:
                            st.success(f"✅ Summary sent to `{coach_email}`!")
                        else:
                            st.error("❌ Failed to send summary email.")
            st.divider()

        if not merged_coach_bookings:
            st.info("No active coach bookings.")
        else:
            for i, b in enumerate(merged_coach_bookings):
                b_date = datetime.strptime(b['date'], '%Y-%m-%d')
                day_name = b_date.strftime('%A')
                formatted_date = b_date.strftime('%b %d, %Y')
                start_time = min(b['start_hours'])
                end_time = max(b['start_hours']) + 1
                time_display = f"{start_time:02d}:00 - {end_time:02d}:00"
                id_list = sorted(b['ids'])
                id_display = f"#{id_list[0]}" if len(id_list) == 1 else f"#{id_list[0]}-{id_list[-1]}"

                with st.container(border=True):
                    st.write(f"**🎾 {b['court']}** | {day_name}, {formatted_date} | ⏰ {time_display}")
                    st.caption(f"Utilizing Quota: {b['sc']} - Villa {b['v']} | Ref: {id_display}")
                    if st.button("❌ Cancel & Refund Quota", key=f"c_can_{i}_{id_display}"):
                        for bid in b['ids']:
                            delete_booking(bid, b['v'], b['sc'], fingerprint=current_device, coach_email=coach_email)
                        for h in b['start_hours']:
                            notify_owner_of_coach_booking(coach_name, b['v'], b['sc'], b['court'], b['date'], h, action="cancelled")
                        st.success("Cancelled. Quota returned to owner.")
                        time.sleep(1)
                        st.rerun()

    with c_tab4:
        render_court_maintenance_tab(f"Coach {coach_name}", current_device)

    with c_tab5:
        render_activity_log_tab(current_device)

else:
    # ----------------------------------------
    # STANDARD RESIDENT DASHBOARD
    # ----------------------------------------
    sub_community, villa = st.session_state.sub_community, st.session_state.villa
    verified_user_email = st.session_state.get("verified_email", "Verified")
    st.success(f"✅ Logged in as: **{sub_community} - Villa {villa}** (`{verified_user_email}`)")

    if is_donor_villa(sub_community, villa):
        render_donor_legend_banner()

    # --- VILLA SNIPING INTERCEPTOR & ENFORCEMENT ---
    current_device = st.session_state.get("device_uuid")
    sniping_level, hopping_villas, cooldown_hrs = check_device_sniping_status(
        current_device, verified_user_email, sub_community, villa
    )

    if sniping_level == 2:
        add_log(
            "Sniping Penalty",
            f"4-day penalty active for {sub_community} Villa {villa} (Device: {current_device}, Email: {verified_user_email})",
            fingerprint=current_device
        )
        show_sniping_lockout_dialog(cooldown_hrs)
        st.stop()
    elif sniping_level == 1 and not st.session_state.get("seen_sniping_warning", False):
        add_log(
            "Sniping Warning",
            f"Cross-villa warning triggered for {sub_community} Villa {villa}. Prior activity on: {', '.join(hopping_villas)}",
            fingerprint=current_device
        )
        show_sniping_warning_dialog(hopping_villas)

    tab1, tab2, tab3, tab4, tab5 = st.tabs(["📅 Availability", "➕ Book", "📋 My Bookings", "🛠️ Court Maint.", "📜 Activity Log"])

    with tab1:
        render_availability_tab(
            is_coach=False,
            current_device=current_device,
            resident_ctx={"villa": villa, "sub_community": sub_community, "verified_user_email": verified_user_email},
        )

    with tab2:
        st.subheader("Book a New Slot")
        date_options = [f"{d.strftime('%Y-%m-%d')} ({d.strftime('%A')})" for d in get_next_14_days()]
        selected_date_full = st.selectbox("Date:", date_options, key="tab2_date_select")
        date_choice = selected_date_full.split(" (")[0]
        
        if date_choice <= "2026-03-22":
            timing_msg = "7AM to 12AM slots."
        else:
            timing_msg = "7AM to 10PM slots."
        
        tab2_active_limit = get_active_booking_limit(sub_community, villa, for_date=date_choice)
        st.info(f"App allows {tab2_active_limit} Active bookings spanning 14 days, A maximum of 2 active bookings per day. Current date choice timing: **{timing_msg}**")
        court_choice = st.selectbox("Court:", courts, key="tab2_court_select")
        free_hours = get_available_hours(court_choice, date_choice)
        if not free_hours:
            st.warning(f"😔 Sorry, no slots available for {court_choice} on {date_choice}."); time_choice = None
        else:
            time_options = [f"{h:02d}:00 - {h+1:02d}:00" for h in free_hours]
            time_choice = st.selectbox("Time Slot:", time_options, key="tab2_time_select")
        
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
        with col_status1: st.info(f"Total active bookings: **{active_count} / {tab2_active_limit}**")
        with col_status2: st.info(f"Bookings for {date_choice}: **{daily_count} / 2**")
        
        if st.button("Book This Slot", type="primary"):
            active_count_latest = get_active_bookings_count(villa, sub_community)
            active_limit_latest = get_active_booking_limit(sub_community, villa, for_date=date_choice)
            
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
                elif active_count_latest + slots_choice > active_limit_latest: 
                    st.error(f"🚫 Overall limit reached. You can book {max(0, active_limit_latest-active_count_latest)} more slots.")
                    add_log("Access Denied", f"{sub_community} Villa {villa} reached active booking limit ({active_limit_latest})", fingerprint=current_device)
                elif daily_count_latest + slots_choice > 2:
                    st.error(f"🚫 Daily limit reached. You can book {max(0, 2-daily_count_latest)} more on {date_choice}.")
                    add_log("Access Denied", f"{sub_community} Villa {villa} reached daily limit (2) for {date_choice}", fingerprint=current_device)
                else:
                    success = True
                    booked_slots = []
                    for h in slots_to_book:
                        if book_slot(villa, sub_community, court_choice, date_choice, h, fingerprint=current_device):
                            booked_slots.append(h)
                        else:
                            success = False
                            break
                    if success:
                        send_booking_notification_once("created", villa, sub_community, court_choice, date_choice, booked_slots, verified_user_email)
                        st.balloons()
                        st.success(f"✅ SUCCESS! {court_choice} booked for {date_choice} starting at {start_h:02d}:00 ({slots_choice} slot(s))")
                        if verified_user_email and "@" in verified_user_email:
                            st.info(f"📧 A confirmation email has been sent to **{verified_user_email}** from **miracourtbooking@gmail.com**. If you don't see it, please check your spam/junk folder.")
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
            limit_val = get_active_booking_limit(sub_community, villa)
        else:
            my_b = get_user_bookings(villa, sub_community)
            for b in my_b: b['orig_v'] = villa; b['orig_sc'] = sub_community
            limit_val = get_active_booking_limit(sub_community, villa)

        today_str = get_today().strftime('%Y-%m-%d')
        real_total_active = len(my_b)
        total_active = real_total_active 
        
        today_bookings = len([b for b in my_b if b['date'] == today_str])

        col_sum1, col_sum2 = st.columns(2)
        with col_sum1:
            st.metric("Total Active Bookings", f"{total_active} / {limit_val}")
        with col_sum2:
            st.metric("Today's Bookings", f"{today_bookings} / 2")
        st.divider()

        merged_bookings = []
        if my_b:
            df_my_b = pd.DataFrame(my_b).sort_values(['date', 'court', 'start_hour'])
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

        if merged_bookings:
            if st.button("📧 Email Me All My Bookings", type="primary", use_container_width=True, key="email_all_bookings_btn"):
                with st.spinner("Sending summary email..."):
                    success_sent = send_all_bookings_summary(villa, sub_community, merged_bookings, verified_user_email)
                    if success_sent:
                        st.success(f"✅ Summary email containing all your active bookings has been sent to `{verified_user_email}`!")
                    else:
                        st.error("❌ Failed to send summary email. Please check your API configuration.")
            st.divider()

        if not my_b: 
            st.info("You have no active bookings.")
        else:
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
                            background-color: #0d5384; padding: 18px; border-radius: 12px 12px 0px 0px; 
                            border-left: 6px solid #4CAF50; color: white; box-shadow: 0px 4px 10px rgba(0,0,0,0.4); margin-top: 15px;
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
                            <div><span style="font-size: 1.0rem; opacity: 0.9;">{day_name}, {formatted_date}</span></div>
                            <div style="font-size: 1.5rem; font-weight: bold; margin-top: 5px; font-family: 'Audiowide'; color: white;">
                                ⏰ {time_display}
                            </div>
                        </div>
                    """, unsafe_allow_html=True)
                    
                    jpg_bytes = generate_booking_card_jpg(id_display, b['court'], b['sc'], b['v'], f"{day_name}, {formatted_date}", time_display)
                    clean_ref_filename = id_display.replace('#', '').replace('-', '_')
                    render_share_or_download_button(jpg_bytes, f"{clean_ref_filename}.jpg", id_display, key=i)
                    
                    if st.button(f"❌ Cancel Booking {id_display}", key=f"cancel_{i}", width='stretch'):
                        for bid in b['ids']: delete_booking(bid, b['v'], b['sc'], fingerprint=current_device)
                        send_booking_notification_once("deleted", b['v'], b['sc'], b['court'], b['date'], b['start_hours'], verified_user_email)
                        st.success(f"Successfully cancelled booking {id_display}")
                        if verified_user_email and "@" in verified_user_email:
                            st.info(f"📧 A confirmation email has been sent to **{verified_user_email}** from **miracourtbooking@gmail.com**. If you don't see it, please check your spam/junk folder.")
                        time.sleep(1.5); st.rerun()
                    st.markdown('<div style="margin-bottom: 25px;"></div>', unsafe_allow_html=True)
            
            st.divider()
            if st.button("🚪 Logout / Change Villa", width='stretch'):
                logout_action()

    with tab4:
        render_court_maintenance_tab(f"{sub_community} Villa {villa}", current_device)

    with tab5:
        render_activity_log_tab(current_device)

col1, col2 = st.columns([1, 5])
with col1: st.markdown(f'<img src="https://raw.githubusercontent.com/mahadevbk/courtbooking/main/qr-code.miracourtbooking.streamlit.app.png" height="100">', unsafe_allow_html=True)
with col2: st.markdown("""
    <div style='background-color: #0d5384; padding: 1rem; border-left: 5px solid #fff500; border-radius: 0.5rem; color: white;'>
    Built with ❤️ using <a href='https://streamlit.io/' style='color: #ccff00;'>Streamlit</a> — free and open source.
    <a href='https://devs-scripts.streamlit.app/' style='color: #ccff00;'>Other Scripts by dev</a> on Streamlit.
    </div>
    """, unsafe_allow_html=True)
