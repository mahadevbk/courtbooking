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
from PIL import Image, ImageDraw, ImageFont # For dynamic JPG card rendering
from streamlit_javascript import st_javascript
import urllib.parse
import resend

# Set page configuration to wide mode by default
st.set_page_config(
    page_title="Mira Court Booking",
    page_icon="🎾",
    layout="wide",
)

# ==========================================
# --- DONOR NAMES & TICKER (EDIT HERE) ---
# ==========================================
DONOR_NAMES = [
    "Abhisek", "Adam", "Adebayo", "Arlan", "Alesia", "Ameen", "Angelo", "Carlos", "Charbel", "Dev", "Elie",
    "Farheen", "Hana", "Harith", "Hisham", "Katya", "Khaled", "Leina", "Marko", "Mei",
    "Melissa", "Mustafa", "Nick", "Nikki", "Rena", "Riin", "Saket", "Sheila", "Sofia", "Vik", "Yousef",
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


# --- ICS & SQUARE JPG CARD GENERATOR HELPERS ---
def generate_ics_content(court, date_str, start_hours, sub_community, villa):
    """Generates standard iCalendar (.ics) bytes for universal calendar integration."""
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
    """Generates a direct web link to add an event to Google Calendar."""
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

def generate_booking_card_jpg(id_display, court, sub_community, villa, formatted_date, time_display):
    """Generates an exact replica square JPG card matching the on-screen UI styling."""
    width, height = 600, 600
    image = Image.new("RGB", (width, height), color="#0d5384") # On-screen deep blue card background
    draw = ImageDraw.Draw(image)

    # Left accent green border stripe (matching #4CAF50 on-screen)
    draw.rectangle([0, 0, 10, height], fill="#4CAF50")

    try:
        font_large = ImageFont.truetype("DejaVuSans-Bold.ttf", 34)
        font_medium = ImageFont.truetype("DejaVuSans-Bold.ttf", 24)
        font_small = ImageFont.truetype("DejaVuSans.ttf", 16)
        font_time = ImageFont.truetype("DejaVuSans-Bold.ttf", 40)
    except Exception:
        font_large = font_medium = font_small = font_time = ImageFont.load_default()

    # Content layout matching the UI structure
    draw.text((35, 40), f"BOOKING CONF.: {id_display}", fill="rgba(255,255,255,0.6)", font=font_small)
    draw.text((35, 85), f"🎾  {court}", fill="#ccff00", font=font_large)
    draw.text((370, 92), f"{sub_community} - {villa}", fill="#ffffff", font=font_medium)

    # Location pin link text
    draw.text((35, 145), "📍  View Location Pin", fill="#ccff00", font=font_small)

    # Divider line matching UI border
    draw.line([(35, 195), (565, 195)], fill="rgba(255,255,255,0.15)", width=2)

    # Date and Time block
    draw.text((35, 230), formatted_date, fill="#ffffff", font=font_medium)
    draw.text((35, 290), f"⏰  {time_display}", fill="#ffffff", font=font_time)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


# --- ELEGANT EMAIL NOTIFICATION HELPER ---
def send_booking_notification(action_type, villa, sub_community, court, date_str, start_hours, recipient_email):
    """Sends an elegant, neatly formatted HTML email confirmation with ICS attachment and web links via Resend API."""
    if not recipient_email or "@" not in recipient_email:
        return
    try:
        resend.api_key = st.secrets.get("RESEND_API_KEY")
        if not resend.api_key:
            return
        
        sorted_hours = sorted(start_hours)
        start_h = sorted_hours[0]
        end_h = sorted_hours[-1] + 1
        duration = len(sorted_hours)
        time_display = f"{start_h:02d}:00 - {end_h:02d}:00"
        
        b_date = datetime.strptime(date_str, '%Y-%m-%d')
        formatted_date = b_date.strftime('%A, %b %d, %Y')
        
        google_cal_url = get_google_calendar_url(court, date_str, start_hours, sub_community, villa)
        
        attachments_list = []
        if action_type == "created":
            ics_bytes = generate_ics_content(court, date_str, start_hours, sub_community, villa)
            attachments_list.append({
                "filename": f"tennis-{court.lower().replace(' ', '-')}-{date_str}.ics",
                "content": list(ics_bytes)
            })
            
            subject = f"🎾 Booking Confirmed: {court} ({formatted_date})"
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
                .info-card {{ background-color: #f8fafc; border-radius: 8px; border-left: 5px solid #4CAF50; padding: 20px; margin: 20px 0; border: 1px solid #e2e8f0; border-left: 5px solid #4CAF50; }}
                .info-row {{ margin: 8px 0; font-size: 15px; color: #2d3748; }}
                .btn-container {{ text-align: center; margin: 25px 0 10px 0; }}
                .btn {{ background-color: #0d5384; color: #ffffff !important; padding: 12px 24px; border-radius: 6px; text-decoration: none; font-weight: bold; font-size: 14px; display: inline-block; }}
                .footer {{ background-color: #f8fafc; padding: 20px; text-align: center; font-size: 12px; color: #718096; border-top: 1px solid #e2e8f0; }}
              </style>
            </head>
            <body>
              <div class="email-wrapper">
                <div class="email-header">
                  <h1>🎾 Court Booking Confirmation</h1>
                </div>
                <div class="email-body">
                  <p>Hello Resident,</p>
                  <p>Your court reservation has been successfully booked and confirmed!</p>
                  
                  <div class="info-card">
                    <div class="info-row"><b>Court:</b> {court}</div>
                    <div class="info-row"><b>Date:</b> {formatted_date}</div>
                    <div class="info-row"><b>Time Slot:</b> {time_display}</div>
                    <div class="info-row"><b>Duration:</b> {duration} hour(s)</div>
                    <div class="info-row"><b>Residence:</b> {sub_community} - Villa {villa}</div>
                  </div>
                  
                  <p style="font-size: 14px; color: #4a5568;">An iCalendar (.ics) invite is attached to this email for instant syncing with Apple Calendar, Outlook, or mobile devices. You can also click below to add it directly to Google Calendar:</p>
                  
                  <div class="btn-container">
                    <a href="{google_cal_url}" class="btn" target="_blank">📅 Add to Google Calendar</a>
                  </div>
                </div>
                <div class="footer">
                  Mira Court Booking App • Community Fair-Use Solution
                </div>
              </div>
            </body>
            </html>
            """
        else:
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

        resend.Emails.send({
            "from": "Mira Court Booking <onboarding@resend.dev>",
            "to": [recipient_email],
            "subject": subject,
            "html": html_content,
            "attachments": attachments_list
        })
    except Exception as e:
        print(f"Error sending email: {e}")

def send_all_bookings_summary(villa, sub_community, bookings_list, recipient_email):
    """Sends an elegant summary email containing all active bookings for the user."""
    if not recipient_email or "@" not in recipient_email or not bookings_list:
        return False
    try:
        resend.api_key = st.secrets.get("RESEND_API_KEY")
        if not resend.api_key:
            return False
        
        items_html = ""
        for b in bookings_list:
            b_date = datetime.strptime(b['date'], '%Y-%m-%d')
            formatted_date = b_date.strftime('%A, %b %d, %Y')
            start_time = min(b['start_hours'])
            end_time = max(b['start_hours']) + 1
            time_display = f"{start_time:02d}:00 - {end_time:02d}:00"
            duration = len(b['start_hours'])
            id_list = sorted(b['ids'])
            id_display = f"#{id_list[0]}" if len(id_list) == 1 else f"#{id_list[0]}-{id_list[-1]}"
            g_url = get_google_calendar_url(b['court'], b['date'], b['start_hours'], sub_community, villa)
            
            items_html += f"""
            <div style="background: #f8fafc; padding: 18px; border-radius: 8px; border-left: 5px solid #0d5384; margin-bottom: 15px; border: 1px solid #e2e8f0; border-left: 5px solid #0d5384;">
                <p style="margin: 4px 0; color: #718096; font-size: 0.8rem;"><b>Reference:</b> {id_display}</p>
                <p style="margin: 4px 0; font-size: 1.1rem; color: #0d5384;"><b>🎾 {b['court']}</b></p>
                <p style="margin: 4px 0; color: #2d3748;"><b>Date:</b> {formatted_date}</p>
                <p style="margin: 4px 0; color: #2d3748;"><b>Time:</b> {time_display} ({duration} hour(s))</p>
                <p style="margin: 8px 0 4px 0;"><a href="{g_url}" target="_blank" style="color: #0d5384; font-size: 13px; text-decoration: none; font-weight: bold;">📅 Add to Google Calendar</a></p>
            </div>
            """

        subject = f"📋 Summary of All Active Court Bookings ({sub_community} Villa {villa})"
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
              <p>Hello Resident,</p>
              <p>Here is the complete overview of all your active court reservations for <b>{sub_community} - Villa {villa}</b>:</p>
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

        resend.Emails.send({
            "from": "Mira Court Booking <onboarding@resend.dev>",
            "to": [recipient_email],
            "subject": subject,
            "html": html_content
        })
        return True
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
    "Mira 1": 322,
    "Mira 2": 334,
    "Mira 3": 402,
    "Mira 4": 516,
    "Mira 5": 316,
    "Mira Oasis 1": 483,
    "Mira Oasis 2": 427,
    "Mira Oasis 3": 483
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

def get_next_14_days():
    today = get_today()
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
            log_entry["Fingerprint"] = fingerprint
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
                        add_log("Purge Out-of-Range", f"Deleted invalid claim for {sub} Villa {v_num}")

        bookings_res = run_query(supabase.table("bookings").select("id, sub_community, villa"))
        if bookings_res and bookings_res.data:
            for booking in bookings_res.data:
                sub = booking.get("sub_community")
                v_str = str(booking.get("villa", ""))
                max_v = SUB_COMMUNITY_VILLA_LIMITS.get(sub)
                if max_v and v_str.isdigit():
                    v_num = int(v_str)
                    if not (1 <= v_num <= max_v):
                        run_query(supabase.table("bookings").delete().eq("id", booking["id"]))
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
            .select("timestamp, event_type, details, Fingerprint")
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
        fp = entry.get("Fingerprint") or ""
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
        if entry.get("event_type") == "Sniping Penalty" and ts >= (now - timedelta(hours=96)):
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
        supabase.table("logs").select("timestamp, event_type, details, Fingerprint")
        .gte("timestamp", cutoff_96h)
        .in_("event_type", ["Sniping Penalty", "Admin Reset"])
        .order("timestamp", desc=True)
    )
    logs = res.data if res and res.data else []
    cleared_entities = set()
    active_penalties = {}
    for entry in logs:
        ts = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).replace(tzinfo=None)
        details = entry.get("details", "")
        fp = entry.get("Fingerprint")
        if entry.get("event_type") == "Admin Reset":
            m_email = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', details)
            if m_email:
                cleared_entities.add((m_email.group(0).lower(), ts))
            if fp:
                cleared_entities.add((fp, ts))
            continue
        if entry.get("event_type") == "Sniping Penalty":
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

def book_slot(villa, sub_community, court, date_str, start_hour, fingerprint=None):
    try:
        run_query(supabase.table("bookings").insert({
            "villa": villa,
            "sub_community": sub_community,
            "court": court,
            "date": date_str,
            "start_hour": start_hour
        }))
        log_detail = f"{sub_community} Villa {villa} booked {court} for {date_str} at {start_hour:02d}:00"
        add_log("Booking Created", log_detail, fingerprint=fingerprint)
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

def delete_booking(booking_id, villa, sub_community, fingerprint=None):
    record = run_query(supabase.table("bookings").select("court, date, start_hour").eq("id", booking_id).single())
    if record and record.data:
        b = record.data
        log_detail = f"{sub_community} Villa {villa} cancelled {b['court']} for {b['date']} at {b['start_hour']:02d}:00"
        add_log("Booking Deleted", log_detail, fingerprint=fingerprint)
    run_query(supabase.table("bookings").delete().eq("id", booking_id).eq("villa", villa).eq("sub_community", sub_community))

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

def logout_action():
    st_javascript("""
        localStorage.removeItem('court_villa_lock');
        localStorage.removeItem('court_verified_email');
        localStorage.removeItem('verified_claim_info');
        localStorage.removeItem('supabase_refresh_token');
        setTimeout(() => { window.location.href = window.location.origin + window.location.pathname; }, 150);
    """)
    for key in [
        "authenticated", "sub_community", "villa", "verified_email", 
        "otp_sent", "otp_email", "otp_target_villa", "otp_target_sub", 
        "prefill_sub", "prefill_villa", "seen_sniping_warning"
    ]:
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

js_device_fetch = st_javascript("""
    (function() {
        let devId = localStorage.getItem('court_device_uuid');
        if (!devId) {
            devId = 'dev_' + Math.floor(Math.random() * 89999999 + 10000000) + '_' + Math.floor(Date.now() / 1000);
            localStorage.setItem('court_device_uuid', devId);
        }
        return devId;
    })();
""")

if isinstance(js_device_fetch, str) and js_device_fetch.startswith("dev_"):
    st.session_state.device_uuid = js_device_fetch
elif "device_uuid" not in st.session_state:
    st.session_state.device_uuid = f"dev_{random.randint(10000000, 99999999)}_{int(time.time())}"

url_token = st.query_params.get("auth")
if url_token and not st.session_state.authenticated:
    verified_claim = decode_auth_token(url_token)
    if verified_claim and verified_claim.get("email"):
        st.session_state.sub_community = verified_claim["sub_community"]
        st.session_state.villa = verified_claim["villa"]
        st.session_state.verified_email = verified_claim["email"]
        st.session_state.authenticated = True

if not st.session_state.authenticated:
    stored_bundle = st_javascript("(localStorage.getItem('court_villa_lock') || 'no_lock') + ':::' + (localStorage.getItem('court_verified_email') || '') + ':::' + (localStorage.getItem('verified_claim_info') || '');")
    
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
            max_limit = SUB_COMMUNITY_VILLA_LIMITS.get(otp_sub, 500)
            otp_villa_raw = st.text_input(f"Villa Number (1 - {max_limit})", value=default_villa, key="otp_villa_text").strip()
            otp_villa = "".join(filter(str.isdigit, otp_villa_raw))

        otp_email_input = st.text_input("Email Address", placeholder="name@example.com", key="otp_email_text").strip().lower()

        if st.button("Send 6-Digit Code", type="primary", width='stretch'):
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
                                    localStorage.setItem('court_device_uuid', '{resolved_uuid}');
                                """)

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

sub_community, villa = st.session_state.sub_community, st.session_state.villa
verified_user_email = st.session_state.get("verified_email", "Verified")
st.success(f"✅ Logged in as: **{sub_community} - Villa {villa}** (`{verified_user_email}`)")

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
                real_active_count = get_active_bookings_count(villa, sub_community)
                active_count = real_active_count * 3  # DELIBERATE MULTIPLIER BUG
                
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
                    add_log("Access Denied", f"{sub_community} Villa {villa} reached active booking limit (6)", fingerprint=current_device)
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
                        send_booking_notification("created", villa, sub_community, q_court, selected_date, booked_slots, verified_user_email)
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

    real_active_count = get_active_bookings_count(villa, sub_community)
    active_count = real_active_count * 3  # DELIBERATE MULTIPLIER BUG
    
    daily_count = get_daily_bookings_count(villa, sub_community, date_choice)
    col_status1, col_status2 = st.columns(2)
    with col_status1: st.info(f"Total active bookings: **{active_count} / 6**")
    with col_status2: st.info(f"Bookings for {date_choice}: **{daily_count} / 2**")
    
    if st.button("Book This Slot", type="primary"):
        real_active_count_latest = get_active_bookings_count(villa, sub_community)
        active_count_latest = real_active_count_latest * 3  # DELIBERATE MULTIPLIER BUG
        
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
                add_log("Access Denied", f"{sub_community} Villa {villa} reached active booking limit (6)", fingerprint=current_device)
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
                    send_booking_notification("created", villa, sub_community, court_choice, date_choice, booked_slots, verified_user_email)
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
    real_total_active = len(my_b)
    total_active = real_total_active * 3  # DELIBERATE MULTIPLIER BUG
    
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
                
                # Download Square JPG Card Button
                jpg_bytes = generate_booking_card_jpg(id_display, b['court'], b['sc'], b['v'], f"{day_name}, {formatted_date}", time_display)
                st.download_button(
                    label=f"📥 Download Booking Card (Square JPG) {id_display}",
                    data=jpg_bytes,
                    file_name=f"booking-{b['court'].lower().replace(' ', '-')}-{b['date']}.jpg",
                    mime="image/jpeg",
                    key=f"download_jpg_{i}",
                    use_container_width=True
                )
                
                if st.button(f"❌ Cancel Booking {id_display}", key=f"cancel_{i}", width='stretch'):
                    for bid in b['ids']: delete_booking(bid, b['v'], b['sc'], fingerprint=current_device)
                    send_booking_notification("deleted", b['v'], b['sc'], b['court'], b['date'], b['start_hours'], verified_user_email)
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
                        "reported_by": f"{sub_community} Villa {villa}",
                        "is_fixed": False
                    }))
                    add_log("Maintenance Reported", f"Issue reported for {m_court} by {sub_community} Villa {villa}", fingerprint=current_device)
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
        if not is_admin:
            display_df['details'] = display_df['details'].apply(mask_emails_in_text)

        cols = ['timestamp', 'event_type', 'details']
        display_df['timestamp'] = pd.to_datetime(display_df['timestamp'], format='ISO8601').dt.strftime('%b %d, %H:%M')
        
        def style_rows(row):
            styles = [''] * len(row)
            if row.event_type in ["Booking Created", "Villa Claim"]: styles[1] = 'background-color: #d4edda; color: #155724; font-weight: bold;'
            elif row.event_type in ["Booking Deleted", "Booking Cancelled", "Villa Claim Removed"]: styles[1] = 'background-color: #f8d7da; color: #721c24; font-weight: bold;'
            elif row.event_type in ["Access Denied", "Claim Held for Review", "Sniping Warning"]: styles[1] = 'background-color: #ffcc00; color: black; font-weight: bold;'
            elif row.event_type in ["Sniping Penalty"]: styles[1] = 'background-color: #ff4d4d; color: white; font-weight: bold;'
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

        with st.expander("🚨 Security, Blacklist & Anti-Abuse Management", expanded=True):
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

            st.divider()
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

        with st.expander("🛡️ Resident & Villa Verification Management", expanded=False):
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

            st.divider()
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

        with st.expander("🏘️ Villa Bookings & Data Backup Management", expanded=False):
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

            st.divider()
            st.markdown("### Database Backup (ZIP)")
            def get_zip_data():
                try:
                    b_data, l_data = [], []
                    chunk_size = 1000
                    offset = 0
                    while True:
                        res = run_query(supabase.table("bookings").select("*").range(offset, offset + chunk_size - 1))
                        if not res or res.data is None: break
                        b_data.extend(res.data)
                        if len(res.data) < chunk_size: break
                        offset += chunk_size
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

    elif admin_pass:
        st.error("Incorrect Password")

col1, col2 = st.columns([1, 5])
with col1: st.markdown(f'<img src="https://raw.githubusercontent.com/mahadevbk/courtbooking/main/qr-code.miracourtbooking.streamlit.app.png" height="100">', unsafe_allow_html=True)
with col2: st.markdown("""
    <div style='background-color: #0d5384; padding: 1rem; border-left: 5px solid #fff500; border-radius: 0.5rem; color: white;'>
    Built with ❤️ using <a href='https://streamlit.io/' style='color: #ccff00;'>Streamlit</a> — free and open source.
    <a href='https://devs-scripts.streamlit.app/' style='color: #ccff00;'>Other Scripts by dev</a> on Streamlit.
    </div>
    """, unsafe_allow_html=True)
