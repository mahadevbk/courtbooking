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
import urllib.parse
import urllib.request
import os
import csv
import html
import threading
from concurrent.futures import ThreadPoolExecutor

# Set page configuration to wide mode by default
st.set_page_config(
    page_title="Mira Court Booking",
    page_icon="🎾",
    layout="wide",
)

# ==========================================
# --- LEGENDS OF MIRA — DONOR NAMES & VILLAS ---
# ==========================================
# Names are loaded from donors.csv, a single-column CSV (header "Name") kept in the same
# folder as this script/repo. This list is purely for recognition in the thank-you ticker and
# can be freely edited — adding a name here does NOT grant the "Legends of Mira" 8-active-
# booking perk. DONOR_VILLAS below is the separate, frozen, closed list that actually grants
# that perk, and is intentionally NOT read from any file — it only changes via a direct code
# edit, so a routine edit to donors.csv can never accidentally hand out the perk.
_FALLBACK_DONOR_NAMES = [
    "Abhishek", "Adam", "Adebayo", "Alesia", "Ameen", "Anastasia", "Angelo", "Arlan", "Asim", "Carlos",
    "Charbel", "Dev", "Elie", "Farheen", "Francois", "Goncalo", "Guru", "Hana", "Harith", "Hatem",
    "Hisham", "Katya", "KD", "Khaled", "Laurent", "Leina", "Lisa", "Marko", "Matthieu", "Mei",
    "Melissa", "Mostafa", "Mustafa", "Nick", "Nikki", "Phillip", "Rena", "Ricardo", "Riin", "Saket",
    "SAS", "Sheila", "Sofia", "Teresa", "Timo", "Vik", "Wael", "Yann", "Yousef",
]

@st.cache_data(show_spinner=False)
def _read_donor_names(csv_path, file_mtime):
    """Parses donors.csv. file_mtime is only part of the cache key: saving the file busts the
    cache, so an edited donors.csv still shows up on the very next interaction. Raises if the
    file is missing/empty/unreadable (exceptions are never cached)."""
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        raise ValueError("donors.csv is empty")
    header = [h.strip().lower() for h in rows[0]]
    data_rows = rows[1:] if header and header[0] == "name" else rows
    names = [row[0].strip() for row in data_rows if row and row[0].strip()]
    if not names:
        raise ValueError("donors.csv has no names")
    return sorted({n.upper() for n in names})

def load_donor_names():
    """Reads donor names from donors.csv next to this script — one name per row under a
    "Name" header. Returns them upper-cased and alphabetically sorted for the ticker. Falls
    back to the last-known hardcoded list if the file is missing, empty, or unreadable, so a
    deploy without the file (or a temporary file hiccup) never breaks the ticker.
    (This module-level call runs on every script rerun, so the parse is cached per file version
    instead of re-reading and re-parsing the CSV each time.)"""
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "donors.csv")
    try:
        return _read_donor_names(csv_path, os.path.getmtime(csv_path))
    except Exception:
        return sorted({n.upper() for n in _FALLBACK_DONOR_NAMES})

DONOR_NAMES = load_donor_names()

# Each tuple is (sub_community, villa) — sub_community lowercased and whitespace-collapsed, villa
# as a plain string — matching the normalization is_donor_villa() applies when checking a booking.
# This is the final, closed list of villas that receive the 8-active-booking "Legends of Mira"
# ---------------------------------------------------------------------------
# RESOURCES TAB — WhatsApp groups & racket-stringing contacts.
# Static, admin-maintained data kept OUTSIDE this file so it can be updated without a redeploy:
# a JSON file in the same GitHub folder as this script. JSON (not YAML/CSV) because `json` is
# already imported here — no extra package to install — and it still edits cleanly in any text
# editor or GitHub's own web editor.
#
# Expected shape of resources.json:
# {
#   "whatsapp_groups": [
#     {"name": "Mira Tennis Players", "image_url": "https://.../pic.jpg", "description": "Open to all residents. Link: https://chat.whatsapp.com/xxxxxxxx"}
#   ],
#   "stringers": [
#     {"name": "Ahmed's Stringing", "phone": "+971501234567", "description": "Same-day stringing, drops off at your villa."}
#   ]
# }
# "image_url" and "description" are optional per entry; everything else is required for that
# entry to render. A group's WhatsApp link, if it has one, just goes inside its description text
# (as a plain URL, e.g. "Join here: https://chat.whatsapp.com/xxxx") — the app pulls it out of the
# text automatically and shows a short "🔗 Join Group" button instead of the raw link.
# ---------------------------------------------------------------------------
RESOURCES_JSON_URL = "https://raw.githubusercontent.com/mahadevbk/courtbooking/main/resources.json"

@st.cache_data(ttl=3600, show_spinner=False)
def load_resources_data():
    """Fetches resources.json from GitHub. Cached for an hour, so a normal visit never waits on
    GitHub and an edit to the file shows up within the hour (or immediately after the "🔄 Refresh"
    button on the tab, which clears this cache). Returns {} — an empty result, not an error — if
    the file is missing, malformed, or GitHub can't be reached, so the tab always renders."""
    with urllib.request.urlopen(RESOURCES_JSON_URL, timeout=6) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    groups = [g for g in raw.get("whatsapp_groups", []) if isinstance(g, dict) and g.get("name")]
    stringers = [s for s in raw.get("stringers", []) if isinstance(s, dict) and s.get("name") and s.get("phone")]
    return {"whatsapp_groups": groups, "stringers": stringers}

_URL_RE = re.compile(r'https?://\S+')
_TRAILING_LINK_LABEL_RE = re.compile(r'(?i)\b(join(\s+group)?(\s+here)?|link)\s*:?\s*$')

def _split_description_link(text):
    """A group description can have its join link typed right into the text (e.g. "Join here:
    https://chat.whatsapp.com/..."). Those links, especially WhatsApp's, are long and full of
    tracking parameters, so showing the raw URL wastes space. This pulls the first http(s) link
    out and returns (description_without_the_link, link_or_None); the caller shows a short
    "🔗 Join Group" button for the link instead of the URL text. A leading label like "Join:",
    "Join here:" or "Link:" right before the link is dropped too, since the button says that now.
    Text with no link in it, or no description at all, passes through unchanged."""
    if not text:
        return "", None
    m = _URL_RE.search(text)
    if not m:
        return text.strip(), None
    raw_url = m.group(0)
    link = raw_url.rstrip('.,)]}>"\'')                 # trailing sentence punctuation isn't part of the URL
    trailing_punct = raw_url[len(link):]
    before = _TRAILING_LINK_LABEL_RE.sub('', text[:m.start()].rstrip()).rstrip()
    after = (trailing_punct + text[m.end():]).strip()
    remaining = f"{before} {after}".strip() if (before and after) else (before or after)
    remaining = re.sub(r'\s+([.,;:!?])', r'\1', remaining).strip()  # e.g. "residents ." -> "residents."
    return remaining, link

def render_resources_tab():
    st.markdown("### 📚 Community Resources")

    try:
        data = load_resources_data()
    except Exception:
        data = {}
    groups, stringers = data.get("whatsapp_groups", []), data.get("stringers", [])

    st.markdown(f"#### {_whatsapp_svg(20)} WhatsApp Groups", unsafe_allow_html=True)
    if groups:
        cols = st.columns(2)
        for i, g in enumerate(groups):
            with cols[i % 2]:
                with st.container(border=True):
                    # Built as one HTML block, not st.columns: on a narrow phone Streamlit stacks
                    # columns vertically, which made the "icon" column render as a full-width image.
                    # A fixed-size (44px) round icon sits inline with the group name here instead, so
                    # it stays small at any screen width; the description spans the full card below it.
                    icon_html = (
                        f'<img src="{html.escape(g["image_url"], quote=True)}" '
                        'style="width:44px;height:44px;border-radius:50%;object-fit:cover;flex-shrink:0;">'
                        if g.get("image_url") else
                        '<div style="width:44px;height:44px;border-radius:50%;background:rgba(255,255,255,0.08);'
                        f'display:flex;align-items:center;justify-content:center;flex-shrink:0;">{_whatsapp_svg(24)}</div>'
                    )
                    desc_text, join_link = _split_description_link(g.get("description", ""))
                    desc_html = (
                        f'<div style="margin-top:8px; font-size:0.92rem; line-height:1.4;">{html.escape(desc_text)}</div>'
                        if desc_text else
                        '<div style="margin-top:8px; font-size:0.92rem; color:rgba(255,255,255,0.6);">Contact the admin to join.</div>'
                        if not join_link else ''
                    )
                    st.markdown(
                        '<div style="display:flex; align-items:center; gap:10px;">'
                        f'{icon_html}<span style="font-weight:bold; font-size:1.05rem;">{html.escape(g["name"])}</span>'
                        f'</div>{desc_html}',
                        unsafe_allow_html=True,
                    )
                    if join_link:
                        st.link_button("🔗 Join Group", join_link, width='stretch')
    else:
        st.caption("No WhatsApp groups listed yet.")

    st.divider()
    st.markdown("#### 🎾 Racket Stringing Services")
    if stringers:
        for s in stringers:
            with st.container(border=True):
                sc1, sc2 = st.columns([3, 2])
                with sc1:
                    st.markdown(f"**{s['name']}**")
                    if s.get("description"):
                        st.caption(s["description"])
                with sc2:
                    st.write("")
                    wa_number = re.sub(r"[^\d]", "", s["phone"])
                    st.link_button(f"📞 {s['phone']}", f"https://wa.me/{wa_number}", width='stretch')
    else:
        st.caption("No stringing services listed yet.")

# allowance (instead of the standard 6). It will not grow with future donors.
DONOR_VILLAS = {
    ("mira oasis 1", "148"),
    ("mira oasis 3", "188"),
    ("mira 1", "307"),
    ("mira 2", "223"),
    ("mira 2", "128"),
    ("mira 1", "229"),
    ("mira oasis 1", "476"),
    ("mira 2", "321"),
    ("mira 2", "66"),
    ("mira oasis 3", "359"),
    ("mira oasis 3", "231"),
    ("mira 1", "177"),
    ("mira oasis 3", "482"),
    ("mira 1", "157"),
    ("mira 4", "115"),
    ("mira 5", "83"),
    ("mira 4", "138"),
    ("mira oasis 3", "139"),
    ("mira oasis 3", "408"),
    ("mira 2", "250"),
    ("mira oasis 3", "11"),
    ("mira 2", "186"),
    ("mira 4", "84"),
    ("mira 5", "92"),
    ("mira oasis 1", "417"),
    ("mira 4", "459"),
    ("mira 3", "142"),
    ("mira 3", "159"),
    ("mira 3", "92"),
    ("mira oasis 2", "64"),
    ("mira 3", "39"),
}

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

def is_donor_villa(sub_community, villa):
    """True if this Sub Community + Villa belongs to a recorded donor (whitespace and case normalized)."""
    norm_sub = " ".join(str(sub_community).lower().split())
    norm_villa = str(villa).strip()
    return (norm_sub, norm_villa) in DONOR_VILLAS

# "Legends of Mira" donor perk window: the elevated 8-slot quota runs for 6 months from
# 1 Sept 2026 and reverts to the normal 6-slot quota afterwards.
DONOR_PERK_START_DATE = datetime(2026, 9, 1).date()

# Villas exempt from the villa-sniping warning/lockout system entirely — currently the same
# three Mira 1 villas (229, 231, 249) used for the concealed Legends of Mira auto-booking
# feature in database_cleanup.py. These are shared/community-purpose villas, not a single
# resident's own property, so cross-villa "hopping" enforcement doesn't apply to them.
SNIPING_EXEMPT_VILLAS = {("mira 1", "229"), ("mira 1", "231"), ("mira 1", "249")}

def is_sniping_exempt_villa(sub_community, villa):
    norm_sub = " ".join(str(sub_community).lower().split())
    norm_villa = str(villa).strip()
    return (norm_sub, norm_villa) in SNIPING_EXEMPT_VILLAS

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

# Reused wherever the app shows the WhatsApp brand mark (also used in render_whatsapp_banner) —
# an inline SVG so it renders crisply at any size with no extra image request.
_WHATSAPP_SVG_PATH = (
    '<path fill="#25D366" d="M380.9 97.1C339 55.1 283.2 32 223.9 32c-122.4 0-222 99.6-222 222 '
    '0 39.1 10.2 77.3 29.6 111L0 480l117.7-30.9c32.4 17.7 68.9 27 106.1 27h.1c122.3 0 224.1-99.6 '
    '224.1-222 0-59.3-25.2-115-67.1-157zm-157 341.6c-33.2 0-65.7-8.9-94-25.7l-6.7-4-69.8 18.3L72 '
    '359.2l-4.4-7c-18.5-29.4-28.2-63.3-28.2-98.2 0-101.7 82.8-184.5 184.6-184.5 49.3 0 95.6 19.2 '
    '130.4 54.1 34.9 34.9 56.2 81.2 56.1 130.5 0 101.8-84.9 184.6-186.6 184.6zm101.2-138.2c-5.5-2.8 '
    '-32.8-16.2-37.9-18-5.1-1.9-8.8-2.8-12.5 2.8-3.7 5.6-14.3 18-17.6 21.8-3.2 3.7-6.5 4.2-12 1.4 '
    '-32.6-16.3-54-29.1-75.5-66-5.7-9.8 5.7-9.1 16.3-30.3 1.8-3.7.9-6.9-.5-9.7-1.4-2.8-12.5-30.1 '
    '-17.1-41.2-4.5-10.8-9.1-9.3-12.5-9.5-3.2-.2-6.9-.2-10.6-.2-3.7 0-9.7 1.4-14.8 6.9-5.1 5.6 '
    '-19.4 19-19.4 46.3 0 27.3 19.9 53.7 22.6 57.4 2.8 3.7 39.1 59.7 94.8 83.8 35.2 15.2 49 16.5 '
    '66.6 13.9 10.7-1.6 32.8-13.4 37.4-26.4 4.6-13 4.6-24.1 3.2-26.4-1.3-2.5-5-3.9-10.5-6.6z"/>'
)

def _whatsapp_svg(size):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 448 512" width="{size}" height="{size}" '
            f'style="vertical-align:-{int(size*0.15)}px;">{_WHATSAPP_SVG_PATH}</svg>')

def render_whatsapp_banner():
    """Bold 'join the WhatsApp group' link with the WhatsApp logo, shown at the top of every tab."""
    st.markdown(
        '<div style="margin: 2px 0 14px 0;">'
        '<a href="https://chat.whatsapp.com/CbIV9EV53PLBz2HvqamA7V" target="_blank" '
        'style="text-decoration:none; font-weight:700; color:#128C7E; font-size:15px; '
        'display:inline-flex; align-items:center; gap:6px;">'
        f'{_whatsapp_svg(18)}'
        "Click here to Join the App's WhatsApp group for bugs & Features"
        '</a>'
        '</div>',
        unsafe_allow_html=True,
    )

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

# ==========================================
# --- ANNOUNCEMENTS (announcements.csv) ---
# ==========================================
# To post an announcement, add a row to announcements.csv (same folder as this script) with a
# Date and the Announcement text. It appears in the News tab (📢), newest first — newest meaning
# simply the last row in the CSV file, so add new announcements at the BOTTOM of the file. No
# code change needed. The date can be written like "1 Sep 2026", "1. Sep. 2026",
# "01 September 2026", "2026-09-01" or "01/09/2026" (day first) — it's shown as a label only and
# no longer affects ordering. If the file is missing or unreadable the tab simply says there are
# no announcements; it never breaks the app.
_ANNOUNCEMENT_DATE_FORMATS = ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%b %d %Y", "%B %d %Y")

def _parse_announcement_date(raw):
    """Best-effort date from the CSV's Date cell, or None."""
    txt = re.sub(r"[.,]", " ", (raw or "").replace("\xa0", " "))
    txt = re.sub(r"\bsept\b", "sep", " ".join(txt.split()), flags=re.I)
    for fmt in _ANNOUNCEMENT_DATE_FORMATS:
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    return None

def _clean_announcement_text(text):
    """Tidy spreadsheet artefacts (non-breaking spaces, doubled spaces) but keep intentional line
    breaks, and escape '$' so two of them can't turn a stretch of text into a maths formula."""
    lines = [" ".join(l.replace("\xa0", " ").split()) for l in (text or "").splitlines()]
    return "  \n".join(l for l in lines if l).replace("$", "\\$")

@st.cache_data(show_spinner=False)
def _read_announcements(csv_path, file_mtime):
    """file_mtime is only part of the cache key: saving the file busts the cache, so a new row
    shows up on the very next interaction instead of after some timeout."""
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.reader(f) if any(c.strip() for c in r)]
    if not rows:
        return []
    head = [h.strip().lower() for h in rows[0]]
    if head and head[0] == "date":
        i_date = 0
        i_text = head.index("announcement") if "announcement" in head else 1
        rows = rows[1:]
    else:
        i_date, i_text = 0, 1
    items = []
    for order, row in enumerate(rows):
        raw_date = row[i_date].strip() if len(row) > i_date else ""
        # Everything from the text column onward, so an unquoted comma in the text can't cut it short.
        text = _clean_announcement_text(", ".join(c.strip() for c in row[i_text:] if c.strip()))
        if not text:
            continue
        d = _parse_announcement_date(raw_date)
        items.append({
            "order": order,
            "iso": d.isoformat() if d else "",
            "label": d.strftime("%-d %b %Y") if d else raw_date,
            "text": text,
        })
    # Newest first = simply the reverse of the CSV's own row order (last row in the file is
    # treated as the most recent announcement), regardless of what's in the Date column.
    return list(reversed(items))

def load_announcements():
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "announcements.csv")
    try:
        return _read_announcements(csv_path, os.path.getmtime(csv_path))
    except Exception:
        return []

# The News tab (📢) shows a red dot for this many days after an announcement's date (the day
# itself plus this many days after it), so a new post is hard to miss without needing any
# per-user "read" tracking. Change the number to lengthen or shorten it.
ANNOUNCEMENT_NEW_DAYS = 3

def has_recent_announcement():
    """True if any announcement is dated from today back to ANNOUNCEMENT_NEW_DAYS days ago.
    Future-dated or undated rows never trigger the dot (so a typo'd date can't leave it on)."""
    today = get_today()
    for it in load_announcements():
        if not it["iso"]:
            continue
        age = (today - datetime.strptime(it["iso"], "%Y-%m-%d").date()).days
        if 0 <= age <= ANNOUNCEMENT_NEW_DAYS:
            return True
    return False

def announcements_tab_label():
    return "📢 News" + (" 🔴" if has_recent_announcement() else "")

def render_announcements_tab():
    items = load_announcements()
    if not items:
        st.info("No announcements yet.")
        return
    for it in items:
        with st.container(border=True):
            st.caption(f"📅 {it['label']}")
            st.markdown(it["text"])

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

def generate_multi_ics_content(bookings_list, sub_community=None, villa=None):
    """One .ics file containing a VEVENT for every booking in bookings_list — used for the 'My
    Bookings' summary email so a single attachment adds every active booking to Apple/Google/
    Outlook calendars at once, rather than needing one link per booking."""
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    events = []
    for b in bookings_list:
        item_villa = b.get('v', villa)
        item_sub = b.get('sc', sub_community)
        court = b['court']
        date_str = b['date']
        sorted_hours = sorted(b['start_hours'])
        start_h = sorted_hours[0]
        end_h = sorted_hours[-1] + 1
        date_clean = date_str.replace("-", "")
        events.append(f"""BEGIN:VEVENT
UID:mira-booking-{date_str}-{start_h}-{court.replace(' ', '')}-{item_villa}@miracourtbooking
DTSTAMP:{now_stamp}
DTSTART:{date_clean}T{start_h:02d}0000
DTEND:{date_clean}T{end_h:02d}0000
SUMMARY:🎾 Tennis at {court}
DESCRIPTION:Court reservation at {court} for {item_sub} - Villa {item_villa}.
LOCATION:{court} Tennis Court, Mira, Dubai, UAE
END:VEVENT""")

    ics_text = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Mira Court Booking//EN\nCALSCALE:GREGORIAN\nMETHOD:PUBLISH\n" \
        + "\n".join(events) + "\nEND:VCALENDAR"
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

@st.cache_data(ttl=3600, max_entries=256, show_spinner=False)
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
    # Always attempt native sharing first, on every device/OS/browser — no more relying on
    # user-agent sniffing to decide. The JS itself feature-detects at click time: if
    # navigator.canShare(files) isn't supported (most desktop browsers, some older mobile
    # browsers), it automatically falls back to a plain file download instead — so this is
    # safe everywhere and never leaves the button doing nothing.
    b64_data = base64.b64encode(jpg_bytes).decode()
    html = f"""
    <style>
        html, body {{ margin: 0; padding: 0; }}
    </style>
    <button id="share_btn_{key}" style="
        box-sizing: border-box; display: block;
        width:100%; height:2.5rem; margin:0; padding:0 0.4rem;
        background-color:#06b6d4; color:#ffffff;
        border:1px solid rgba(255,255,255,0.35); border-radius:0.5rem;
        font-size:0.82rem; font-family: 'Source Sans Pro', sans-serif; font-weight:400; cursor:pointer;
        line-height:1;">
        📤&nbsp;Share
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
                let shared = false;
                if (navigator.canShare && navigator.canShare({{ files: [file] }})) {{
                    try {{
                        await navigator.share({{ files: [file], title: "Tennis Court Booking" }});
                        shared = true;
                    }} catch (shareErr) {{
                        // AbortError means the user just cancelled the share sheet — don't
                        // fall back to a download in that case, that would be surprising.
                        if (shareErr && shareErr.name === "AbortError") {{ shared = true; }}
                        else {{ console.error("Share failed, falling back to download:", shareErr); }}
                    }}
                }}
                if (!shared) {{
                    const url = URL.createObjectURL(file);
                    const a = document.createElement('a');
                    a.href = url; a.download = filename;
                    document.body.appendChild(a); a.click(); document.body.removeChild(a);
                    URL.revokeObjectURL(url);
                }}
            }} catch (err) {{
                console.error("Share/download failed:", err);
            }}
        }});
    }})();
    </script>
    """
    st.iframe(html, height=42)

# --- GMAIL SMTP EMAIL HELPER ---
def send_gmail_smtp(recipient_email, subject, html_content, ics_content=None, ics_filename="invite.ics",
                    bcc_emails=None):
    """Send an HTML email via Gmail SMTP.

    - recipient_email: primary To address.
    - bcc_emails: optional list of addresses delivered via BCC (envelope only — no Bcc
      header is written, so recipients cannot see each other). When bcc_emails is used for
      a multi-recipient broadcast, pass the system mailbox as recipient_email
      (e.g. miracourtbooking@gmail.com) so the visible To is not any resident.
    """
    g_user = st.secrets.get("GMAIL_USER", "devkrea@gmail.com")
    g_pass = st.secrets.get("GMAIL_PASSWORD", "").replace(" ", "")
    bcc_list = []
    if bcc_emails:
        seen = set()
        for e in bcc_emails:
            e_clean = (e or "").strip().lower()
            if e_clean and "@" in e_clean and e_clean not in seen:
                seen.add(e_clean)
                bcc_list.append(e_clean)

    to_addr = (recipient_email or "").strip()
    if not g_pass:
        return False
    if not to_addr and not bcc_list:
        return False
    if to_addr and "@" not in to_addr:
        return False
    if not to_addr:
        to_addr = g_user

    try:
        # Appended to every outgoing email, regardless of template, since every email in the
        # app funnels through this one function. Handles both full HTML-document emails (which
        # have a closing </body>) and the simpler one-off snippet emails (which don't).
        whatsapp_footer = (
            '<div style="text-align:center; padding:16px 20px; margin-top:8px; font-size:13px; '
            'color:#4a5568; background-color:#eafaf1; border-top:1px solid #d4f4e2; '
            'font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, Helvetica, Arial, sans-serif;">'
            'Join the WhatsApp group for this App using this Link: '
            '<a href="https://chat.whatsapp.com/CbIV9EV53PLBz2HvqamA7V" style="color:#128C7E; font-weight:600; text-decoration:none;">'
            'https://chat.whatsapp.com/CbIV9EV53PLBz2HvqamA7V</a>'
            '</div>'
        )
        if "</body>" in html_content:
            html_content = html_content.replace("</body>", whatsapp_footer + "</body>", 1)
        else:
            html_content = html_content + whatsapp_footer

        # An .ics attachment is what makes "Add to Calendar" actually work on iOS/Apple Mail —
        # a plain Google Calendar web link just opens the Google Calendar website in Safari.
        # Attaching a calendar file (with method=PUBLISH) makes Apple Mail show its native
        # "Add to Calendar" banner that opens straight into the Apple Calendar app, while still
        # working fine as a normal attachment in Gmail/Outlook/etc.
        if ics_content:
            msg = MIMEMultipart("mixed")
            alt_part = MIMEMultipart("alternative")
            alt_part.attach(MIMEText(html_content, "html"))
            msg.attach(alt_part)

            ics_bytes = ics_content if isinstance(ics_content, bytes) else ics_content.encode("utf-8")
            cal_part = MIMEText(ics_bytes.decode("utf-8"), "calendar; method=PUBLISH")
            cal_part.add_header("Content-Disposition", "attachment", filename=ics_filename)
            msg.attach(cal_part)
        else:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(html_content, "html"))

        msg["Subject"] = subject
        msg["From"] = f"Mira Court Booking <{g_user}>"
        msg["To"] = to_addr
        # Intentionally omit Bcc header so recipient clients never see other addresses;
        # delivery to BCC list is handled only via the SMTP envelope below.

        envelope_recipients = [to_addr] + [e for e in bcc_list if e != to_addr.lower()]

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(g_user, g_pass)
            server.sendmail(g_user, envelope_recipients, msg.as_string())
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
                  <p style="font-size: 13px; color: #718096;">📅 A calendar invite is attached to this email — open it to add this booking straight to your phone's calendar (works with Apple Calendar, Google Calendar, and Outlook).</p>
                </div>
                <div class="footer">
                  Mira Court Booking App • Community Fair-Use Solution
                </div>
              </div>
            </body>
            </html>
            """
            ics_content = generate_ics_content(court, date_str, start_hours, sub_community, villa)
            send_gmail_smtp(recipient_email, subject, html_content, ics_content=ics_content, ics_filename="booking.ics")
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
    # SMTP connect + login + send takes 1-3s. Nothing here needs its result (failures are only
    # printed), so send it from a background thread and let the click return immediately.
    # The once-only bookkeeping above stays on the request thread (it uses session_state).
    threading.Thread(
        target=send_booking_notification,
        args=(action_type, villa, sub_community, court, date_str, list(start_hours), recipient_email),
        daemon=True,
    ).start()

def report_booked_not_used(sub_community, villa, court, date_str, hour, reporter_label):
    """Notifies the villa's registered resident email(s) that their booking was flagged as
    sitting unused, and writes a log entry that always shows the offending villa and a running
    30-day report count to everyone (a deliberate name-and-shame deterrent). The reporter's own
    identity is appended separately and stripped out of the resident-facing log view — visible
    to admins only, so no one is publicly named for reporting a neighbor."""
    slot_tag = f"⟦SLOT:{court}|{date_str}|{hour}⟧"

    since = (get_utc_plus_4() - timedelta(days=30)).isoformat()
    prior_res = run_query(
        supabase.table("logs").select("id", count="exact")
        .eq("event_type", "Booked but not used")
        .ilike("details", f"%{sub_community} Villa {villa}%")
        .gte("timestamp", since)
    )
    prior_count = prior_res.count if prior_res and prior_res.count is not None else 0
    new_count = prior_count + 1

    formatted_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %b %d, %Y")
    time_display = f"{hour:02d}:00 - {hour+1:02d}:00"

    owner_res = run_query(
        supabase.table("villa_claims").select("email")
        .eq("sub_community", sub_community).eq("villa", villa).eq("status", "approved")
    )
    owner_emails = [r["email"] for r in owner_res.data] if owner_res and owner_res.data else []
    for email in owner_emails:
        subject = "⚠️ Your Court Booking Was Reported as Unused"
        html_content = f"""
        <html><body style="font-family: Arial, sans-serif; color: #222;">
        <h3>Booking Reported as Unused</h3>
        <p>A fellow resident reported that the booking below appears to be sitting empty right now:</p>
        <p><b>Court:</b> {court}<br><b>Date:</b> {formatted_date}<br><b>Time:</b> {time_display}<br>
        <b>Residence:</b> {sub_community} - Villa {villa}</p>
        <p>If you're not able to use a slot you've booked, please cancel it in the app as early as
        possible so someone else can enjoy the court. Thanks for being considerate of your
        fellow residents — courts are a shared, limited resource!</p>
        </body></html>
        """
        send_gmail_smtp(email, subject, html_content)

    public_part = (
        f"Booked but not used reported for {sub_community} Villa {villa} on {court} "
        f"({time_display}, {formatted_date}) — {new_count} report(s) in the last 30 days."
    )
    add_log("Booked but not used", f"{public_part} Reported by {reporter_label}. {slot_tag}")
    invalidate_booking_caches()

def is_slot_already_reported(court, date_str, hour):
    """True if this exact court/date/hour slot has already had a 'Booked but not used' report
    filed against it, so a second person can't report the same slot twice."""
    slot_tag = f"⟦SLOT:{court}|{date_str}|{hour}⟧"
    res = run_query(
        supabase.table("logs").select("id")
        .eq("event_type", "Booked but not used")
        .ilike("details", f"%{slot_tag}%")
        .limit(1)
    )
    return bool(res and res.data)

@st.cache_data(ttl=30, show_spinner=False)
def is_slot_reported_display(court, date_str, hour):
    """Display-only cached twin of is_slot_already_reported(). The confirm-report button still
    calls the uncached original so two people can't file the same report."""
    return is_slot_already_reported(court, date_str, hour)

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
              <p style="font-size: 13px; color: #718096; margin-top: 4px;">📅 A calendar file with all of the above bookings is attached — open it to add them straight to your phone's calendar (works with Apple Calendar, Google Calendar, and Outlook). Prefer to add just one? Use its "Add to Google Calendar" link above instead.</p>
            </div>
            <div class="footer">
              Mira Court Booking App • Community Fair-Use Solution
            </div>
          </div>
        </body>
        </html>
        """
        multi_ics = generate_multi_ics_content(bookings_list, sub_community=sub_community, villa=villa)
        return send_gmail_smtp(recipient_email, subject, html_content, ics_content=multi_ics, ics_filename="my_bookings.ics")
    except Exception as e:
        print(f"Error sending summary email: {e}")
        return False

def _send_broadcast_with_personal_bookings(recipient_email, subject, body_html_inner, merged_bookings):
    """One individual email: admin notice body + that recipient's own active bookings
    (same card layout as 'Email Me All My Bookings') + multi-event .ics attachment.
    Never includes anyone else's bookings."""
    if not recipient_email or "@" not in recipient_email:
        return False
    try:
        items_html = ""
        for b in merged_bookings:
            item_villa = b.get("v", "")
            item_sub = b.get("sc", "")
            b_date = datetime.strptime(b["date"], "%Y-%m-%d")
            formatted_date = b_date.strftime("%A, %b %d, %Y")
            start_time = min(b["start_hours"])
            end_time = max(b["start_hours"]) + 1
            time_display = f"{start_time:02d}:00 - {end_time:02d}:00"
            duration = len(b["start_hours"])
            id_list = sorted(b["ids"])
            id_display = f"#{id_list[0]}" if len(id_list) == 1 else f"#{id_list[0]}-{id_list[-1]}"
            g_url = get_google_calendar_url(b["court"], b["date"], b["start_hours"], item_sub, item_villa)
            villa_line = (
                f'<p style="margin: 4px 0; color: #2d3748;"><b>Villa:</b> {item_sub} - Villa {item_villa}</p>'
                if item_sub or item_villa else ""
            )
            items_html += f"""
            <div style="background: #f8fafc; padding: 18px; border-radius: 8px; border-left: 5px solid #0d5384; margin-bottom: 15px; border: 1px solid #e2e8f0;">
                <p style="margin: 4px 0; color: #718096; font-size: 0.8rem;"><b>Reference:</b> {id_display}</p>
                <p style="margin: 4px 0; font-size: 1.1rem; color: #0d5384;"><b>🎾 {b['court']}</b></p>
                <p style="margin: 4px 0; color: #2d3748;"><b>Date:</b> {formatted_date}</p>
                <p style="margin: 4px 0; color: #2d3748;"><b>Time:</b> {time_display} ({duration} hour(s))</p>
                {villa_line}
                <p style="margin: 8px 0 4px 0;"><a href="{g_url}" target="_blank" style="color: #0d5384; font-size: 13px; text-decoration: none; font-weight: bold;">📅 Add to Google Calendar</a></p>
            </div>
            """

        bookings_section = ""
        ics_content = None
        if merged_bookings:
            bookings_section = f"""
              <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 24px 0;">
              <p style="font-weight: 700; color: #0d5384;">Your active bookings</p>
              {items_html}
              <p style="font-size: 13px; color: #718096;">📅 A calendar file with the bookings above is attached — open it to add them to your phone's calendar.</p>
            """
            ics_content = generate_multi_ics_content(merged_bookings)

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"></head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f4f7f6; margin: 0; padding: 0; color: #222;">
          <div style="max-width: 600px; margin: 30px auto; background: #ffffff; border-radius: 12px; overflow: hidden; border: 1px solid #e1e8ed;">
            <div style="background: linear-gradient(135deg, #0d5384, #052134); padding: 24px; text-align: center; color: #ffffff;">
              <h1 style="margin: 0; font-size: 20px;">Mira Court Booking — Community Notice</h1>
            </div>
            <div style="padding: 28px; line-height: 1.6;">
              <div style="white-space: normal;">{body_html_inner}</div>
              {bookings_section}
            </div>
            <div style="background: #f8fafc; padding: 16px; text-align: center; font-size: 12px; color: #718096; border-top: 1px solid #e2e8f0;">
              Mira Court Booking App • Community Fair-Use Solution
            </div>
          </div>
        </body>
        </html>
        """
        return send_gmail_smtp(
            recipient_email,
            subject,
            html_content,
            ics_content=ics_content,
            ics_filename="my_bookings.ics" if ics_content else "invite.ics",
        )
    except Exception as e:
        print(f"Error sending broadcast with personal bookings: {e}")
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

# Shared admin key-value settings table (also used by Supporter-Only Access Mode below). Defined
# here, early, since get_window_today() — used everywhere dates are picked — needs it too.
APP_SETTINGS_TABLE = "app_settings"
BOOKING_WINDOW_RELEASE_HOUR_KEY = "booking_window_release_hour"
DEFAULT_BOOKING_WINDOW_RELEASE_HOUR = 21  # 9 PM — the original hardcoded behavior, kept as the fallback

@st.cache_data(ttl=60, show_spinner=False)
def get_booking_window_release_hour():
    """The hour (0-23, in the app's UTC+4 clock) at which the newest day at the far edge of the
    rolling 15-day booking window becomes bookable, admin-configurable in Bookings & Villas.
    0 (midnight) means no early release at all — the window advances exactly at the natural
    calendar rollover. Cached for 60s; falls back to the original 9 PM default if `app_settings`
    doesn't exist yet, has no row for this key, or the read fails for any reason — a missing
    setting can never break the booking window."""
    try:
        res = supabase.table(APP_SETTINGS_TABLE).select("value").eq("key", BOOKING_WINDOW_RELEASE_HOUR_KEY).limit(1).execute()
        if res and res.data:
            hour = int(res.data[0].get("value"))
            if 0 <= hour <= 23:
                return hour
    except Exception:
        pass
    return DEFAULT_BOOKING_WINDOW_RELEASE_HOUR

def set_booking_window_release_hour(hour):
    try:
        hour = int(hour)
        if not (0 <= hour <= 23):
            return False
        supabase.table(APP_SETTINGS_TABLE).delete().eq("key", BOOKING_WINDOW_RELEASE_HOUR_KEY).execute()
        supabase.table(APP_SETTINGS_TABLE).insert({"key": BOOKING_WINDOW_RELEASE_HOUR_KEY, "value": str(hour)}).execute()
        get_booking_window_release_hour.clear()
        return True
    except Exception:
        return False

def get_window_today(is_supporter=False):
    """Like get_today(), but the booking window rolls over to the next day at the admin-configured
    release hour (default 21:00 / 9 PM) instead of at midnight, so a new day's slots become
    bookable at a specific clock time each day rather than right at 12 AM. A release hour of 0
    means no early release — the window then advances exactly at the natural midnight boundary,
    which is why the `if release_hour and ...` check below skips the shift entirely for hour 0
    (0 is falsy in Python) rather than incorrectly treating "hour 0" as "always past the
    threshold".

    Supporters (is_supporter=True) get that same release time two hours earlier — e.g. 11 PM for
    everyone becomes 9 PM for supporters. Clamped to a floor of 1 (never 0) so the arithmetic can
    never accidentally land on 0, which is a different, special meaning ("disabled") rather than
    "as early as this same-day model can represent"; an admin release hour of 1 or 2 already
    leaves supporters getting the earliest a same-calendar-day release can express."""
    release_hour = get_booking_window_release_hour()
    if is_supporter and release_hour:
        release_hour = max(1, release_hour - 2)
    now = get_utc_plus_4()
    if release_hour and now.hour >= release_hour:
        return now.date() + timedelta(days=1)
    return now.date()

def get_next_14_days(is_supporter=False):
    today = get_window_today(is_supporter=is_supporter)
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

try:
    from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
except Exception:  # unexpected Streamlit layout -> run_parallel quietly falls back to sequential
    add_script_run_ctx = get_script_run_ctx = None

def run_parallel(*calls):
    """Runs independent, zero-argument callables (typically Supabase lookups) at the same time
    and returns their results in the same order. Waiting on N network round-trips one after
    another costs N x latency; running them together costs roughly the slowest one.
    Streamlit's script context is handed to each worker thread so st.* calls made inside a
    lookup (e.g. run_query's error banner) still work. Falls back to plain sequential
    execution if that API isn't available. Exceptions propagate exactly as they would have
    if the callables had been run one by one."""
    if len(calls) < 2 or get_script_run_ctx is None or add_script_run_ctx is None:
        return [c() for c in calls]
    ctx = get_script_run_ctx()

    def _with_ctx(fn):
        def _inner():
            if ctx is not None:
                add_script_run_ctx(threading.current_thread(), ctx)
            return fn()
        return _inner

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [pool.submit(_with_ctx(c)) for c in calls]
        return [f.result() for f in futures]

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
        any_booking_deleted = False
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
                        any_booking_deleted = True
                        log_detail = f"{sub} Villa {v_num} cancelled {booking['court']} for {booking['date']} at {booking['start_hour']:02d}:00"
                        add_log("Booking Deleted", log_detail)
                        add_log("Purge Out-of-Range", f"Deleted invalid booking for {sub} Villa {v_num}")
        # This deletes straight from the bookings table (it needs to sweep the WHOLE table by
        # villa-number range, not one villa at a time like delete_booking()), so it has to clear
        # the shared display cache itself — otherwise a freed slot from here keeps showing as
        # booked until the cache's own TTL happens to expire.
        if any_booking_deleted:
            invalidate_booking_caches()
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

# ==============================================================================
# --- ONE EMAIL PER VILLA (abuse & false-claiming prevention) ---
# ==============================================================================
# Historically a villa could have up to 2 verified resident emails on file (partners/housemates
# sharing a residence). To reduce abuse and false claiming, only ONE verified email is now
# allowed per villa going forward. This constant is the single source of truth for that cap —
# every check or message about "how many emails can a villa have" should read it instead of a
# hardcoded number.
#
# Existing villas that already have 2 approved emails from before this change are migrated
# lazily rather than all at once: the first time anyone signed in for that villa interacts with
# the app, show_email_consolidation_dialog() forces a one-time choice of which email stays
# before the rest of the app is usable again (see the resident-dashboard entry point, where
# get_approved_email_claims_for_villa() is checked against this constant).
MAX_EMAILS_PER_VILLA = 1

def get_villa_claims_count(sub_community, villa):
    res = run_query(supabase.table("villa_claims").select("id", count="exact")
                    .eq("sub_community", sub_community)
                    .eq("villa", villa)
                    .eq("status", "approved"))
    return res.count if res and res.count is not None else 0

@st.cache_data(ttl=60, show_spinner=False)
def get_approved_email_claims_for_villa(sub_community, villa):
    """Full villa_claims rows (not just a count) for this villa's currently APPROVED claims —
    used by the 1-email-per-villa migration check, which needs each row's id (to delete the
    discarded claim) and email, not just a count.

    Cached for 60s (shared across all sessions) because this runs on EVERY resident-dashboard
    rerun for every logged-in user — an uncached query here would run continuously all day.
    A minute of staleness is harmless for this check (worst case: the consolidation dialog
    appears up to ~60s later than the villa actually hit 2 emails, or the "your access changed"
    warning takes up to ~60s to show after another device resolves it) — see
    show_email_consolidation_dialog(), which explicitly clears this cache right after it writes,
    so the resident who just resolved it sees the change on their own next rerun immediately."""
    res = run_query(supabase.table("villa_claims").select("*")
                    .eq("sub_community", sub_community)
                    .eq("villa", villa)
                    .eq("status", "approved")
                    .order("created_at"))
    return res.data if res and res.data else []

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

# ==============================================================================
# --- GREYLIST (admin-only): known abusers capped at ONE villa per email ---
# ==============================================================================
# Needs a `greylisted_emails` table (columns: id bigint generated always as identity primary key,
# email text not null unique, reason text, added_at timestamptz default now()). Until it exists,
# every function below fails open — is_email_greylisted() is simply always False and
# get_max_villas_for_email() always returns the normal 3-villa cap — so a missing table can never
# block ordinary registration; it just means this specific extra restriction isn't active yet.
GREYLIST_TABLE = "greylisted_emails"
DEFAULT_MAX_VILLAS_PER_EMAIL = 3
GREYLIST_MAX_VILLAS_PER_EMAIL = 1

@st.cache_data(ttl=60, show_spinner=False)
def get_greylisted_emails():
    """Lowercased set of every greylisted email, cached for 60s — short enough that an admin
    change takes effect almost immediately, long enough not to query on every keystroke."""
    try:
        res = supabase.table(GREYLIST_TABLE).select("email").execute()
        return {(r["email"] or "").strip().lower() for r in (res.data or []) if r.get("email")}
    except Exception:
        return set()

def is_email_greylisted(email):
    return (email or "").strip().lower() in get_greylisted_emails()

def get_max_villas_for_email(email):
    """The one place that decides how many villas an email may hold. Every spot that creates a
    NEW villa claim (self-service registration, the admin's manual-authorize tool) checks this
    instead of a hardcoded number, so a greylisted email is capped at 1 villa everywhere a claim
    is created through those paths — with no override except removing the email from the greylist
    first. Returns the normal 3-villa cap, unchanged, for every email that isn't greylisted."""
    return GREYLIST_MAX_VILLAS_PER_EMAIL if is_email_greylisted(email) else DEFAULT_MAX_VILLAS_PER_EMAIL

def get_greylist_entries():
    """Full greylist rows for the admin UI, newest first — or None if the table doesn't exist yet
    (same 'feature not switched on' convention as slot_watches/tournament_requests), so the panel
    can tell 'not set up' apart from 'set up but empty'."""
    try:
        res = supabase.table(GREYLIST_TABLE).select("*").order("added_at", desc=True).execute()
        return res.data or []
    except Exception:
        return None

def add_to_greylist(email, reason=""):
    email_clean = (email or "").strip().lower()
    if not email_clean or "@" not in email_clean:
        return False
    try:
        supabase.table(GREYLIST_TABLE).insert({"email": email_clean, "reason": (reason or "").strip() or None}).execute()
        get_greylisted_emails.clear()
        return True
    except Exception:
        return False

def remove_from_greylist(email):
    try:
        supabase.table(GREYLIST_TABLE).delete().eq("email", (email or "").strip().lower()).execute()
        get_greylisted_emails.clear()
        return True
    except Exception:
        return False

# ==============================================================================
# --- SUPPORTER-ONLY ACCESS MODE (admin-only emergency toggle) ---
# ==============================================================================
# A circuit breaker for genuinely high-traffic moments: when ON, only emails on the supporter
# list can get past login (or stay logged in — this is re-checked on every rerun, not just at
# login, so flipping it ON also cuts off anyone already using the app on their very next
# interaction; that immediacy is the point of an emergency control). Everyone else sees a plain
# "we're experiencing high traffic" message — never anything that reveals a supporter list or an
# admin toggle exists. See render_supporter_gate_screen() for the block screen and its built-in
# admin password override (so a forgotten own-email whitelist can never cause a true lockout).
# Needs two tables:
#   app_settings (key text primary key, value text, updated_at timestamptz default now())
#   supporters   (id bigint generated always as identity primary key, email text not null unique,
#                 note text, added_at timestamptz default now())
# Both helpers below fail OPEN — mode reads as OFF and the supporter list reads as empty — if
# either table doesn't exist yet or a read fails, so a missing/broken setup can never accidentally
# lock everyone out of the app. APP_SETTINGS_TABLE itself is defined earlier, alongside
# get_window_today(), which is the other feature sharing this same settings table.
SUPPORTERS_TABLE = "supporters"
SUPPORTER_MODE_KEY = "supporter_only_mode"

@st.cache_data(ttl=20, show_spinner=False)
def is_supporter_mode_enabled():
    """Whether Supporter-Only Access Mode is currently ON. Cached for only 20s — short enough
    that flipping the admin toggle takes effect almost immediately across every active session
    (the whole point of an emergency control), long enough not to query on every single rerun of
    every open session."""
    try:
        res = supabase.table(APP_SETTINGS_TABLE).select("value").eq("key", SUPPORTER_MODE_KEY).limit(1).execute()
        if res and res.data:
            return str(res.data[0].get("value", "")).strip().lower() == "true"
        return False
    except Exception:
        return False

def set_supporter_mode_enabled(enabled):
    """Delete-then-insert rather than upsert, matching how this file handles every other
    single-row-by-key write (see villa_claims elsewhere) — no dependency on upsert semantics."""
    try:
        supabase.table(APP_SETTINGS_TABLE).delete().eq("key", SUPPORTER_MODE_KEY).execute()
        supabase.table(APP_SETTINGS_TABLE).insert({"key": SUPPORTER_MODE_KEY, "value": "true" if enabled else "false"}).execute()
        is_supporter_mode_enabled.clear()
        return True
    except Exception:
        return False

@st.cache_data(ttl=20, show_spinner=False)
def get_supporter_emails():
    """Lowercased set of every supporter email, cached for the same reason and duration as
    is_supporter_mode_enabled()."""
    try:
        res = supabase.table(SUPPORTERS_TABLE).select("email").execute()
        return {(r["email"] or "").strip().lower() for r in (res.data or []) if r.get("email")}
    except Exception:
        return set()

def is_supporter_email(email):
    return bool(email) and (email or "").strip().lower() in get_supporter_emails()

def get_supporter_entries():
    """Full supporter rows for the admin UI, newest first — or None if the table doesn't exist
    yet, same 'feature not switched on' convention as slot_watches/tournament_requests/greylist."""
    try:
        res = supabase.table(SUPPORTERS_TABLE).select("*").order("added_at", desc=True).execute()
        return res.data or []
    except Exception:
        return None

def add_supporter(email, note=""):
    email_clean = (email or "").strip().lower()
    if not email_clean or "@" not in email_clean:
        return False
    try:
        supabase.table(SUPPORTERS_TABLE).insert({"email": email_clean, "note": (note or "").strip() or None}).execute()
        get_supporter_emails.clear()
        return True
    except Exception:
        return False

def remove_supporter(email):
    try:
        supabase.table(SUPPORTERS_TABLE).delete().eq("email", (email or "").strip().lower()).execute()
        get_supporter_emails.clear()
        return True
    except Exception:
        return False

def render_supporter_gate_screen():
    """The screen shown INSTEAD OF the app to anyone blocked by Supporter-Only Access Mode. Says
    nothing about supporters, admins, or any toggle — just a plain high-traffic message — except
    for a small, unlabelled-by-default password override so this can never become a true lockout
    (of the admin's own making, or anyone else who legitimately needs in during the emergency)."""
    st.markdown("<div style='height: 8vh'></div>", unsafe_allow_html=True)
    _sg_c1, _sg_c2, _sg_c3 = st.columns([1, 3, 1])
    with _sg_c2:
        st.error(
            "🚦 **We're experiencing very high traffic right now.**\n\n"
            "The sheer number of people trying to use the app at the same time is pushing against "
            "our hosting limits. To keep the app running for everyone, access is temporarily "
            "limited during this peak period. Priority of Access to supporters who have helped pay for DB hosting costs.\n\n"
            "**Please try logging in again after some time.** Thank you for your patience!"
        )
        with st.expander("Admin"):
            _sg_pass = st.text_input("Password", type="password", key="supporter_gate_admin_pass", label_visibility="collapsed", placeholder="Admin password")
            if _sg_pass:
                if _sg_pass == st.secrets.get("ADMIN_PASSWORD"):
                    st.session_state.supporter_gate_bypass = True
                    st.success("Override granted for this session.")
                    time.sleep(0.6)
                    st.rerun()
                else:
                    st.error("Incorrect password.")

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
    return _cooldown_from_claims(get_claims_for_villa(sub_community, villa), requesting_email)

def _find_claim_for_email(claims, email):
    """First claim in an already-fetched list of a villa's claims that belongs to this email
    (same result as get_existing_claim(), without another database round-trip)."""
    email_clean = (email or "").strip().lower()
    for c in claims or []:
        if (c.get("email") or "").strip().lower() == email_clean:
            return c
    return None

def _cooldown_from_claims(claims, requesting_email):
    """Cooldown logic of get_recent_claim_cooldown(), working on an already-fetched list of the
    villa's claims so the login flow can reuse one query for several checks."""
    if not claims:
        return False, None
    req_email_clean = requesting_email.strip().lower()
    approved_claims = [c for c in claims if c.get("status") == "approved"]
    registered_emails = {c.get("email", "").strip().lower() for c in approved_claims if c.get("email")}
    if req_email_clean in registered_emails:
        return False, None
    if len(registered_emails) < MAX_EMAILS_PER_VILLA:
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

@st.cache_data(ttl=60, show_spinner=False)
def check_device_sniping_status(device_uuid, current_email, current_sub, current_villa):
    if not device_uuid or device_uuid in ("no_uuid", "device_pending"):
        return 0, [], 0
    now = get_utc_plus_4()
    cutoff_96h = (now - timedelta(hours=96)).isoformat()
    current_tag = f"{current_sub} - {current_villa}"
    req_email_clean = (current_email or "").strip().lower()
    try:
        # Same membership rule as before (fingerprint match OR email appears in details), but
        # push both filters to the DB in parallel so we never download the whole 96h log table.
        def _logs_by_fp():
            return run_query(
                supabase.table("logs")
                .select("timestamp, event_type, details, fingerprint")
                .gte("timestamp", cutoff_96h)
                .eq("fingerprint", device_uuid)
                .order("timestamp", desc=True)
            )

        def _logs_by_email():
            if not req_email_clean:
                return None
            return run_query(
                supabase.table("logs")
                .select("timestamp, event_type, details, fingerprint")
                .gte("timestamp", cutoff_96h)
                .ilike("details", f"%{req_email_clean}%")
                .order("timestamp", desc=True)
            )

        res_fp, res_email = run_parallel(_logs_by_fp, _logs_by_email)
        seen = set()
        all_logs = []
        for res in (res_fp, res_email):
            for entry in (res.data if res and res.data else []):
                key = (
                    entry.get("timestamp"),
                    entry.get("event_type"),
                    entry.get("details"),
                    entry.get("fingerprint"),
                )
                if key in seen:
                    continue
                seen.add(key)
                all_logs.append(entry)
    except Exception:
        return 0, [], 0

    recent_villas = set()
    penalized_until = None
    cooldown_cleared_at = None

    for entry in all_logs:
        details = entry.get("details") or ""
        fp = entry.get("fingerprint") or ""
        # Keep the original Python guard so email-ilike false positives are still dropped.
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

# --- BOOKING-WINDOW SNAPSHOT (the "prefetch") ---
# Nearly every screen needs the same thing: who has booked what over the coming days. Instead of
# each widget asking Supabase again on every rerun (and again for every date the user flips to),
# the whole window (yesterday onward, ~2 weeks) is fetched ONCE and shared by every session for
# a few seconds. The grid, date switching, court/time pickers, "My Bookings", the header stats
# and the 2-hour checks are all served from memory. Every WRITE-path check (booking limits,
# "is this slot still free") still queries the database directly, and the bookings table's
# unique constraint stays the final arbiter of a double booking, so a slightly stale display can
# never cause a double booking. The acting user's own changes appear instantly because writes
# call invalidate_booking_caches(); other people's changes appear within BOOKING_SNAPSHOT_TTL.
BOOKING_SNAPSHOT_TTL = 30

class _BookingSnapshot:
    """The fetched booking rows plus indexes built ONCE per fetch. It is stored with
    st.cache_resource, so every caller gets the very same object: st.cache_data would unpickle a
    private copy of the whole row list on each call (about ten times per rerun), and each helper
    then re-scanned every row just to pick out one day. It is strictly READ-ONLY: nobody may
    modify `rows`, the row dicts or the indexes (all consumers only read them or build new
    objects from them)."""
    __slots__ = ("rows", "since", "by_date", "day_maps", "_taken_by_day")

    def __init__(self, rows, since):
        self.rows = rows
        self.since = since
        by_date = {}
        for r in rows:
            by_date.setdefault(r['date'], []).append(r)       # keeps the original row order within a day
        self.by_date = by_date
        # {(court, start_hour): "Sub Community - Villa"} per day (same content the old per-day scan produced)
        self.day_maps = {
            d: {(r['court'], r['start_hour']): f"{r['sub_community']} - {r['villa']}" for r in day_rows}
            for d, day_rows in by_date.items()
        }
        self._taken_by_day = None

    @property
    def taken_by_day(self):
        """{date: {(court, int(start_hour))}} — only the slot-alert picker needs it, so built on first use."""
        t = self._taken_by_day
        if t is None:
            t = {d: {(r["court"], int(r["start_hour"])) for r in day_rows} for d, day_rows in self.by_date.items()}
            self._taken_by_day = t
        return t

@st.cache_resource(ttl=BOOKING_SNAPSHOT_TTL, show_spinner=False)
def get_booking_window_snapshot():
    since = (get_today() - timedelta(days=1)).strftime('%Y-%m-%d')
    rows, start, page = [], 0, 1000
    while True:
        res = run_query(
            supabase.table("bookings")
            .select("id, court, date, start_hour, villa, sub_community, coach_email")
            .gte("date", since).order("id").range(start, start + page - 1)
        )
        if res is None:
            raise RuntimeError("bookings snapshot failed")  # raising => NOT cached; callers fall back to direct queries
        chunk = res.data or []
        rows.extend(chunk)
        if len(chunk) < page:
            break
        start += page
    return _BookingSnapshot(rows, since)

def _snapshot():
    """The shared _BookingSnapshot (read-only!), or None if it couldn't be fetched."""
    try:
        return get_booking_window_snapshot()
    except Exception:
        return None

def _snapshot_rows():
    """(rows, since_date) from the shared snapshot, or (None, None) if it couldn't be fetched."""
    snap = _snapshot()
    if snap is None:
        return None, None
    return snap.rows, snap.since

def _upcoming_rows(rows):
    """Rows that are still 'active': later than today, or today at/after the current hour.
    Evaluated against the live clock at call time, never frozen inside the cache."""
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    return [r for r in rows if r['date'] > today_str or (r['date'] == today_str and r['start_hour'] >= now_hour)]

def invalidate_booking_caches():
    """Called after any booking write so whoever just acted sees fresh data immediately.
    Everyone else picks the change up within the short cache TTLs.
    Sniping status is cleared because new Booking Created log lines feed cross-villa detection.
    Activity-log cache is left to its own TTL — a full log re-fetch on every book/cancel is unnecessary."""
    for name in ("get_booking_window_snapshot", "_get_home_stats_from_queries", "get_slot_history",
                 "is_slot_reported_display", "check_device_sniping_status"):
        try:
            globals()[name].clear()
        except Exception:
            pass

def refresh_after_maintenance_change():
    """What a maintenance report / 'Fixed' click actually needs refreshed: the issue list, the activity
    log (a report writes a log line) and the device-activity check that reads the log. This replaces
    st.cache_data.clear(), which also threw away EVERYTHING else — the booking cards, the announcements,
    and the 'run at most once per hour' markers, so the next visitor's page load re-ran the purge / cleanup /
    double-booking scans that download whole tables."""
    for name in ("get_maintenance_data", "get_logs_last_14_days", "check_device_sniping_status"):
        try:
            globals()[name].clear()
        except Exception:
            pass

def _fetch_bookings_for_day(date_str):
    response = run_query(
        supabase.table("bookings")
        .select("court, start_hour, sub_community, villa")
        .eq("date", date_str)
        .limit(500)
    )
    if not response or not response.data: return {}
    return {(row['court'], row['start_hour']): f"{row['sub_community']} - {row['villa']}" for row in response.data}

def _day_map(date_str):
    """{(court, start_hour): 'Sub Community - Villa'} for one day. May be the snapshot's own shared
    dict, so callers inside this module must treat it as READ-ONLY."""
    snap = _snapshot()
    if snap is not None and date_str >= snap.since:
        return snap.day_maps.get(date_str, {})
    return _fetch_bookings_for_day(date_str)

def get_bookings_for_day_with_details(date_str):
    return dict(_day_map(date_str))     # a private copy, exactly as the old per-call dict was

def is_slot_booked_display(court, date_str, start_hour):
    """DISPLAY-ONLY 'is this slot taken' (e.g. greying out the 2-hour option), answered from the
    snapshot. Anything that decides whether a booking is allowed uses is_slot_booked() instead."""
    return (court, start_hour) in _day_map(date_str)

def get_active_bookings_count_display(villa, sub_community):
    """Display-only twin of get_active_bookings_count(), served from the snapshot."""
    rows, _since = _snapshot_rows()
    if rows is None:
        return get_active_bookings_count(villa, sub_community)
    return sum(1 for r in _upcoming_rows(rows) if str(r['villa']) == str(villa) and r['sub_community'] == sub_community)

def get_daily_bookings_count_display(villa, sub_community, date_str):
    """Display-only twin of get_daily_bookings_count() (same Mira 1 shared-villa rule)."""
    snap = _snapshot()
    if snap is None or date_str < snap.since:
        return get_daily_bookings_count(villa, sub_community, date_str)
    day = snap.by_date.get(date_str, [])
    mira1_group = ["229", "231", "249"]
    if sub_community == "Mira 1" and villa in mira1_group:
        others = [v for v in mira1_group if v != villa]
        if any(r['sub_community'] == "Mira 1" and str(r['villa']) in others for r in day):
            return 99
        return sum(1 for r in day if r['sub_community'] == "Mira 1" and str(r['villa']) == str(villa))
    return sum(1 for r in day if r['sub_community'] == sub_community and str(r['villa']) == str(villa))

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
    def _future():
        q = supabase.table("bookings").select("id", count="exact").eq("villa", villa).eq("sub_community", sub_community)
        return run_query(q.gt("date", today_str))
    def _today():
        q = supabase.table("bookings").select("id", count="exact").eq("villa", villa).eq("sub_community", sub_community)
        return run_query(q.eq("date", today_str).gte("start_hour", now_hour))
    res_future, res_today = run_parallel(_future, _today)  # independent -> one round-trip instead of two
    count_future = res_future.count if res_future and res_future.count is not None else 0
    count_today = res_today.count if res_today and res_today.count is not None else 0
    return count_future + count_today

def get_daily_bookings_count(villa, sub_community, date_str):
    mira1_group = ["229", "231", "249"]
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
    """A slot is 'in the past' only once its whole hour has fully elapsed — the CURRENTLY running
    hour still counts as available for its entire duration (e.g. at 6:30 PM, the 6 PM slot is
    still bookable/usable until 7 PM), not just up to the exact minute someone happens to look."""
    now = get_utc_plus_4()
    if date_str < now.strftime('%Y-%m-%d'): return True
    if date_str == now.strftime('%Y-%m-%d') and start_hour < now.hour: return True
    return False

def _past_checker():
    """Same rule as is_slot_in_past() — the running hour stays available for its whole duration —
    but bound to ONE reading of the clock. Loops that test many slots (the schedule grids test
    ~135 cells per day, the full-page view ~2,000) used to read the clock and format two date
    strings on every single test. Use is_slot_in_past() for one-off checks and for the checks
    that guard a booking."""
    now = get_utc_plus_4()
    today = now.strftime('%Y-%m-%d')
    hour = now.hour
    def is_past(date_str, start_hour):
        if date_str < today:
            return True
        return date_str == today and start_hour < hour
    return is_past


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
        invalidate_booking_caches()
        return True
    except APIError as e:
        if e.code == "23505":
            return False
        raise e
    except Exception:
        return False

def delete_booking(booking_id, villa, sub_community, fingerprint=None, coach_email=None):
    record = run_query(supabase.table("bookings").select("court, date, start_hour").eq("id", booking_id).single())
    freed_slot = None
    if record and record.data:
        b = record.data
        freed_slot = (b['court'], b['date'], b['start_hour'])
        if coach_email:
            log_detail = f"Coach {coach_email} cancelled {b['court']} for {b['date']} at {b['start_hour']:02d}:00 (Quota returned to {sub_community} Villa {villa})"
        else:
            log_detail = f"{sub_community} Villa {villa} cancelled {b['court']} for {b['date']} at {b['start_hour']:02d}:00"
        add_log("Booking Deleted", log_detail, fingerprint=fingerprint)
    run_query(supabase.table("bookings").delete().eq("id", booking_id))
    invalidate_booking_caches()
    if freed_slot:
        try:
            spawn_slot_alert(freed_slot[0], freed_slot[1], freed_slot[2], sub_community, villa)   # never lets alerts break a cancellation
        except Exception as e:
            print(f"slot alert could not be started: {e}")

# ------------------------------------------------------------------------------
# DISPLAY vs WRITE PATH — which booking helpers are safe to use where
# ------------------------------------------------------------------------------
# DISPLAY ONLY (answered from the short-TTL `get_booking_window_snapshot()` cache — fine for
#   painting the grid/pickers fast, NEVER for deciding whether a write is allowed):
#     is_slot_booked_display, get_active_bookings_count_display, get_daily_bookings_count_display,
#     get_available_hours, _day_map, _snapshot / _snapshot_rows, get_user_bookings
# WRITE PATH ONLY (always hits the live DB; this is what may gate an insert/delete):
#     is_slot_booked, get_active_bookings_count, get_daily_bookings_count, get_active_booking_limit
# `validate_booking_attempt()` below is the one place that combines the write-path checks for a
# resident (or a single villa in a coach's pool) — the DB's unique constraint on
# (court, date, start_hour) still has the final word if two requests race.
# ------------------------------------------------------------------------------

def validate_booking_attempt(sub_community, villa, court, date_str, hours_to_book, fingerprint=None, log_denials=True):
    """LIVE-DB gate for a booking attempt covering `hours_to_book` (a list of 1 or 2 consecutive
    start hours) on `court`/`date_str` for `sub_community`/`villa`. Never uses a `*_display` /
    snapshot helper. Checks, in order: each hour is a valid hour for the date, not already booked,
    and not in the past; then the active-booking limit (get_active_booking_limit — donor-window
    aware, gated on the requested slot's own date); then the daily limit of 2/day (via
    get_daily_bookings_count, which already applies the Mira 1 229/231/249 shared-quota rule).
    Returns (ok, error_message) — error_message is None when ok is True. Only logs an "Access
    Denied" line for a limit breach (never for an unavailable-slot rejection), matching the
    original inline Plan & Book behavior; pass log_denials=False for callers (e.g. one villa in a
    coach's pool) that only want a yes/no answer without writing a log line."""
    valid_hours = get_start_hours_for_date(date_str)
    q_slots = len(hours_to_book)
    checked_hours = [h for h in hours_to_book if h in valid_hours]

    # Independent live reads -> one round-trip instead of several back to back.
    _pre = run_parallel(
        lambda: get_active_bookings_count(villa, sub_community),
        lambda: get_daily_bookings_count(villa, sub_community, date_str),
        *[(lambda _h=h: is_slot_booked(court, date_str, _h)) for h in checked_hours],
    )
    active_count, daily_count = _pre[0], _pre[1]
    already_booked = dict(zip(checked_hours, _pre[2:]))

    unavailable = [
        f"{h:02d}:00" for h in hours_to_book
        if h not in valid_hours or already_booked.get(h) or is_slot_in_past(date_str, h)
    ]
    if unavailable:
        return False, f"Slot(s) {', '.join(unavailable)} are unavailable."

    active_limit = get_active_booking_limit(sub_community, villa, for_date=date_str)
    if active_count + q_slots > active_limit:
        if log_denials:
            add_log("Access Denied", f"{sub_community} Villa {villa} reached active booking limit ({active_limit})", fingerprint=fingerprint)
        return False, f"Limit Reached (Max {active_limit} active). You can book {max(0, active_limit - active_count)} more."

    if daily_count + q_slots > 2:
        if log_denials:
            add_log("Access Denied", f"{sub_community} Villa {villa} reached daily limit (2) for {date_str}", fingerprint=fingerprint)
        return False, f"Daily Limit Reached (Max 2 per day). You can book {max(0, 2 - daily_count)} more today."

    return True, None

def merge_consecutive_bookings(rows):
    """Collapse booking rows into consecutive-hour blocks — the one implementation shared by the
    resident 'My Bookings' list, the coach 'My Bookings' list, and the cross-villa email summary.
    Each row needs at least: court, date, start_hour, id, v (villa), sc (sub_community). Rows are
    sorted here (by date, court, sc, v, start_hour) so the caller can pass them in any order.
    Returns a list of dicts: {court, date, start_hours: [...], ids: [...], v, sc}."""
    if not rows:
        return []
    # These lists are always small (one villa's or one coach's upcoming bookings), so a plain
    # sort + loop avoids the fixed overhead of building a DataFrame for a handful of rows —
    # same ordering, same grouping rule, same output shape as before.
    ordered = sorted(rows, key=lambda r: (r["date"], r["court"], r["sc"], r["v"], r["start_hour"]))
    merged = []
    current = None
    for row in ordered:
        if current is not None and (
            row["date"] == current["date"] and row["court"] == current["court"]
            and row["v"] == current["v"] and row["sc"] == current["sc"]
            and row["start_hour"] == max(current["start_hours"]) + 1
        ):
            current["start_hours"].append(row["start_hour"])
            current["ids"].append(row["id"])
        else:
            if current is not None:
                merged.append(current)
            current = {
                "court": row["court"], "date": row["date"],
                "start_hours": [row["start_hour"]], "ids": [row["id"]],
                "v": row["v"], "sc": row["sc"],
            }
    if current is not None:
        merged.append(current)
    return merged

# ==============================================================================
# --- SLOT ALERTS: "Notify me when this slot is available" ---
# ==============================================================================

# A resident picks a date, a start time and 1 or 2 hours (no court: any court will do). If a booking is
# cancelled and that leaves a court free for exactly that time, everyone watching it gets ONE email
# ("Mira 5B is free today from 18:00 to 20:00 for 2 hours"). Needs the `slot_watches` table (see
# slot_watches.sql). Until it exists the feature just says it isn't switched on; nothing else is affected.
SLOT_WATCH_TABLE = "slot_watches"
MAX_ACTIVE_WATCHES_PER_EMAIL = 5
# Wait a moment before checking, so cancelling a 2-hour booking (two deletions in quick succession)
# is seen as one freed 2-hour window and each person gets a single email, not one per hour.
SLOT_ALERT_SETTLE_SECONDS = 2.0
APP_PUBLIC_URL = "https://miracourtbooking.streamlit.app"

def _slot_when_text(date_str):
    """'today' or 'on Tuesday, 22 Sep'."""
    if date_str == get_today().strftime('%Y-%m-%d'):
        return "today"
    return "on " + datetime.strptime(date_str, '%Y-%m-%d').strftime('%A, %d %b')

def send_slot_available_email(recipient_email, court, date_str, start_hour, hours):
    """The alert itself. Returns True if the email was handed to the mail server."""
    try:
        end_hour = start_hour + hours
        when = _slot_when_text(date_str)
        d = datetime.strptime(date_str, '%Y-%m-%d')
        date_line = ("Today — " if when == "today" else "") + d.strftime('%A, %b %d, %Y')
        hrs_word = "hour" if hours == 1 else "hours"
        subject = f"🔔 {court} is free {when} at {start_hour:02d}:00"
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
          <meta charset="utf-8">
          <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f4f7f6; margin: 0; padding: 0; }}
            .email-wrapper {{ max-width: 600px; margin: 30px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 15px rgba(0,0,0,0.05); border: 1px solid #e1e8ed; }}
            .email-header {{ background: linear-gradient(135deg, #e67e22, #b85c0f); padding: 30px; text-align: center; color: #ffffff; }}
            .email-header h1 {{ margin: 0; font-size: 22px; font-weight: 700; letter-spacing: 0.5px; }}
            .email-body {{ padding: 30px; color: #333333; line-height: 1.6; }}
            .info-card {{ background-color: #f8fafc; border-radius: 8px; padding: 20px; margin: 20px 0; border: 1px solid #e2e8f0; border-left: 5px solid #e67e22; }}
            .info-row {{ margin: 8px 0; font-size: 15px; color: #2d3748; }}
            .btn {{ display: inline-block; background-color: #0d5384; color: #ffffff !important; text-decoration: none; font-weight: 700; padding: 12px 26px; border-radius: 8px; }}
            .footer {{ background-color: #f8fafc; padding: 20px; text-align: center; font-size: 12px; color: #718096; border-top: 1px solid #e2e8f0; }}
          </style>
        </head>
        <body>
          <div class="email-wrapper">
            <div class="email-header">
              <h1>🔔 A Court Just Opened Up</h1>
            </div>
            <div class="email-body">
              <p>Hello Resident,</p>
              <p>Good news! <b>{court}</b> is free <b>{when}</b> from <b>{start_hour:02d}:00 to {end_hour:02d}:00</b> for <b>{hours} {hrs_word}</b> — the slot you asked us to watch.</p>
              <div class="info-card">
                <div class="info-row"><b>Court:</b> {court}</div>
                <div class="info-row"><b>Date:</b> {date_line}</div>
                <div class="info-row"><b>Time Slot:</b> {start_hour:02d}:00 - {end_hour:02d}:00</div>
                <div class="info-row"><b>Duration:</b> {hours} {hrs_word}</div>
              </div>
              <p style="text-align:center; margin: 26px 0;"><a class="btn" href="{APP_PUBLIC_URL}">Book it now</a></p>
              <p style="font-size: 13px; color: #718096;">Slots go quickly, and it is first come, first served — other residents watching this slot were told too. This was a one-time alert; you can set another any time under Plan &amp; Book → 🔔 Notify me when a slot opens.</p>
            </div>
            <div class="footer">
              Mira Court Booking App • Community Fair-Use Solution
            </div>
          </div>
        </body>
        </html>
        """
        return bool(send_gmail_smtp(recipient_email, subject, html_content))
    except Exception as e:
        print(f"Error sending slot alert email: {e}")
        return False

def notify_slot_watchers(court, date_str, freeing_households=None):
    """Called after booking(s) on `court` were cancelled. Looks at everything people are watching on
    `date_str` and emails those whose window (1 or 2 hours) is now completely free on this court.
    Returns what was sent (for tests). Each alert is CLAIMED in the database before its email goes out
    (only one caller can win the claim), so nobody is ever emailed twice for the same alert. Because it
    looks at the court's state as a whole, one call handles a 2-hour cancellation in one go and each
    person gets a single email (for their longest matching alert)."""
    sent = []
    freeing_households = {(sub, str(v)) for sub, v in (freeing_households or set())}
    try:
        res = supabase.table(SLOT_WATCH_TABLE).select("*").eq("date", date_str).eq("status", "active").execute()
        candidates = res.data or []
        if not candidates:
            return sent
        booked_res = supabase.table("bookings").select("start_hour").eq("court", court).eq("date", date_str).execute()
        booked = {int(r["start_hour"]) for r in (booked_res.data or [])}
        valid_hours = set(get_start_hours_for_date(date_str))

        by_email = {}
        for w in candidates:
            start, n = int(w["start_hour"]), int(w["hours"])
            needed = list(range(start, start + n))
            if any(h not in valid_hours or h in booked for h in needed):
                continue                                   # part of the window is still taken on this court
            if is_slot_in_past(date_str, start):
                continue
            if (w.get("sub_community"), str(w.get("villa"))) in freeing_households:
                continue                                   # the household that just cancelled already knows
            by_email.setdefault((w.get("email") or "").strip().lower(), []).append(w)

        now_iso = get_utc_plus_4().isoformat()
        for email, watches in by_email.items():
            if "@" not in email:
                continue
            claimed = []
            for w in watches:
                r = (supabase.table(SLOT_WATCH_TABLE)
                     .update({"status": "notified", "notified_at": now_iso, "notified_court": court})
                     .eq("id", w["id"]).eq("status", "active").execute())
                if r.data:
                    claimed.append(w)
            if not claimed:
                continue                                   # someone else already claimed these
            best = max(claimed, key=lambda w: int(w["hours"]))      # one email per person: their longest ask
            if send_slot_available_email(email, court, date_str, int(best["start_hour"]), int(best["hours"])):
                sent.append({"email": email, "court": court, "date": date_str, "start_hour": int(best["start_hour"]), "hours": int(best["hours"])})
            else:
                for w in claimed:                          # email failed: put the alerts back so a later opening can retry
                    supabase.table(SLOT_WATCH_TABLE).update({"status": "active", "notified_at": None, "notified_court": None}) \
                        .eq("id", w["id"]).eq("status", "notified").execute()
    except Exception as e:
        print(f"slot alert error: {e}")
    finally:
        try:
            get_my_slot_watches.clear()
        except Exception:
            pass
    return sent

@st.cache_resource
def _slot_alert_state():
    """Process-wide bookkeeping shared by every session/thread: which (court, day) already has an alert
    job waiting, and a lock so two alert jobs never run at the same moment."""
    return {"pending": {}, "state_lock": threading.Lock(), "run_lock": threading.Lock()}

def spawn_slot_alert(court, date_str, freed_hour=None, freeing_sub=None, freeing_villa=None):
    """Start (in the background, so the person cancelling never waits) the check for who to tell.
    Cancelling a 2-hour booking frees two hours back to back; cancellations on the same court and day
    that arrive while a job is still waiting simply join it, and the job looks at the court as it is
    when it runs, so the whole burst is handled once."""
    state = _slot_alert_state()
    key = (court, date_str)
    with state["state_lock"]:
        if key in state["pending"]:
            state["pending"][key].add((freeing_sub, str(freeing_villa)))
            return
        state["pending"][key] = {(freeing_sub, str(freeing_villa))}

    def _run():
        try:
            if SLOT_ALERT_SETTLE_SECONDS:
                time.sleep(SLOT_ALERT_SETTLE_SECONDS)
            with state["state_lock"]:
                households = state["pending"].pop(key, set())
            with state["run_lock"]:
                notify_slot_watchers(court, date_str, households)
        except Exception as e:
            print(f"slot alert thread error: {e}")
    threading.Thread(target=_run, daemon=True, name="slot-alert").start()

@st.cache_data(ttl=30, show_spinner=False)
def get_my_slot_watches(email):
    """A person's active alerts, soonest first. None means the alerts table isn't available (yet).
    Cached briefly so a page full of reruns doesn't hit the database; create/remove clear it."""
    try:
        res = (supabase.table(SLOT_WATCH_TABLE).select("*").eq("email", (email or "").strip().lower())
               .eq("status", "active").execute())
        rows = res.data or []
    except Exception:
        return None
    return sorted(rows, key=lambda w: (w["date"], int(w["start_hour"]), int(w["hours"])))

def create_slot_watch(email, sub_community, villa, date_str, start_hour, hours):
    """Returns (status, message). status: ok | available | duplicate | limit | invalid | unavailable | error."""
    email = (email or "").strip().lower()
    needed = list(range(start_hour, start_hour + hours))
    valid_hours = get_start_hours_for_date(date_str)
    window = {d.strftime('%Y-%m-%d') for d in get_next_14_days(is_supporter=is_supporter_email(email))}
    if date_str not in window or any(h not in valid_hours for h in needed) or is_slot_in_past(date_str, start_hour):
        return "invalid", "That time isn't available to watch. Please pick another."
    try:
        day = supabase.table("bookings").select("court, start_hour").eq("date", date_str).limit(2000).execute()
        taken = {(r["court"], int(r["start_hour"])) for r in (day.data or [])}
    except Exception:
        return "error", "Couldn't check availability just now. Please try again."
    free = [c for c in courts if all((c, h) not in taken for h in needed)]
    if free:
        return "available", (f"{', '.join(free)} {'is' if len(free) == 1 else 'are'} free at that time right now — "
                             "you can book it above, no alert needed.")
    try:
        mine = (supabase.table(SLOT_WATCH_TABLE).select("id, date, start_hour, hours").eq("email", email)
                .eq("status", "active").execute()).data or []
    except Exception:
        return "unavailable", "Slot alerts aren't switched on yet — please check back soon."
    if any(w["date"] == date_str and int(w["start_hour"]) == start_hour and int(w["hours"]) == hours for w in mine):
        return "duplicate", "You're already watching that slot."
    if len([w for w in mine if not is_slot_in_past(w["date"], int(w["start_hour"]))]) >= MAX_ACTIVE_WATCHES_PER_EMAIL:
        return "limit", f"You can watch up to {MAX_ACTIVE_WATCHES_PER_EMAIL} slots at once. Remove one below to add another."
    try:
        supabase.table(SLOT_WATCH_TABLE).insert({
            "email": email, "sub_community": sub_community, "villa": (str(villa) if villa is not None else None),
            "date": date_str, "start_hour": start_hour, "hours": hours, "status": "active",
        }).execute()
    except Exception:
        return "error", "Couldn't save your alert. Please try again."
    get_my_slot_watches.clear()
    when = _slot_when_text(date_str)
    return "ok", (f"You're on the list! We'll email {email} , if, a court opens {when} "
                  f"from {start_hour:02d}:00 to {start_hour + hours:02d}:00.")

def remove_slot_watch(watch_id, email):
    try:
        supabase.table(SLOT_WATCH_TABLE).delete().eq("id", watch_id).eq("email", (email or "").strip().lower()).execute()
    except Exception:
        pass
    get_my_slot_watches.clear()

@st.cache_data(ttl=3600, show_spinner=False)
def _run_scheduled_watch_cleanup():
    """Alerts for days that are over are useless: drop them (at most hourly)."""
    try:
        supabase.table(SLOT_WATCH_TABLE).delete().lt("date", get_today().strftime('%Y-%m-%d')).execute()
    except Exception:
        pass
    return True

def _watch_columns(spec):
    """st.columns with vertically centred cells where this Streamlit version supports it."""
    try:
        return st.columns(spec, vertical_alignment="center")
    except TypeError:
        return st.columns(spec)

def _slot_window_label(date_str, start_hour, hours=1, show_hours=False):
    """'Today · 18:00 – 20:00', 'Tomorrow · ...' or 'Tue 22 Sep · ...' (+ ' · 2 hrs' when show_hours)."""
    d = datetime.strptime(date_str, '%Y-%m-%d').date()
    today = get_today()
    day = "Today" if d == today else "Tomorrow" if d == today + timedelta(days=1) else d.strftime('%a %d %b')
    label = f"{day} · {start_hour:02d}:00 – {start_hour + hours:02d}:00"
    if show_hours:
        label += f" · {hours} hr{'s' if hours > 1 else ''}"
    return label

def get_watchable_windows(hours, selected_date=None, exclude=(), is_supporter=False):
    """The only slots worth setting an alert for: inside the booking window, not in the past, and with
    NO court free for `hours` consecutive hours (if one is free you can simply book it). Answered from
    the shared booking snapshot, so it costs no database query. Slots on `selected_date` (the day being
    viewed in the table) are listed first. Returns None if the snapshot isn't available."""
    snap = _snapshot()
    if snap is None:
        return None
    taken_by_day = snap.taken_by_day
    is_past = _past_checker()
    windows = []
    for d in get_next_14_days(is_supporter=is_supporter):
        ds = d.strftime('%Y-%m-%d')
        taken = taken_by_day.get(ds, set())
        valid = get_start_hours_for_date(ds)
        for start in valid:
            needed = range(start, start + hours)
            if any(h not in valid for h in needed) or is_past(ds, start) or (ds, start, hours) in exclude:
                continue
            if any(all((c, h) not in taken for h in needed) for c in courts):
                continue                                   # some court is free for the whole window
            windows.append((ds, start))
    windows.sort(key=lambda w: (w[0] != selected_date, w[0], w[1]))
    return windows

def render_slot_watch_section(selected_date, watch_email, sub_community, villa):
    """'I'm looking for a slot': pick 1 or 2 hours, pick one of the FULLY BOOKED slots, tap Notify me.
    Only fully booked slots are offered, so there is nothing to get wrong; three compact rows, inside an
    expander so it takes a single line until opened."""
    ss = st.session_state
    with st.expander("🔔 I'm looking for a slot"):
        email = (watch_email or "").strip().lower()
        if "@" not in email:
            st.caption("Slot alerts need a verified email address.")
            return
        mine = get_my_slot_watches(email)
        if mine is None:
            st.caption("Slot alerts aren't switched on yet — please check back soon.")
            return

        if ss.pop("watch_reset", False):
            # After a saved alert, start from a blank picker. Just dropping the old value is not enough:
            # the browser keeps showing the previous choice in the dropdown. A new key = a brand-new widget.
            ss["watch_nonce"] = ss.get("watch_nonce", 0) + 1
        slot_key = f"watch_slot_{ss.get('watch_nonce', 0)}"
        flash = ss.pop("watch_flash", None)

        st.caption(f"Pick a fully booked slot and we'll email **{email}** , if, a court frees up.")
        w_dur = st.radio("For", ["1 hour", "2 hours"], horizontal=True, key="watch_hours", label_visibility="collapsed")
        w_n = 2 if w_dur == "2 hours" else 1

        watching = {(w["date"], int(w["start_hour"]), int(w["hours"])) for w in mine}
        windows = get_watchable_windows(w_n, selected_date, exclude=watching, is_supporter=is_supporter_email(email))
        if windows is None:
            st.caption("Couldn't load availability just now — please try again in a moment.")
        elif not windows:
            st.caption(f"No fully booked {w_n}-hour slots right now — a court is free at every time, so you can just book one above.")
        else:
            opts = [f"{d}|{h:02d}" for d, h in windows]
            if ss.get(slot_key) not in opts:
                ss.pop(slot_key, None)                       # e.g. the duration changed or the slot opened up
            w_slot = st.selectbox(
                "Slot", opts, index=None, placeholder="Choose a fully booked slot",
                format_func=lambda v: _slot_window_label(v.split("|")[0], int(v.split("|")[1]), w_n),
                key=slot_key, label_visibility="collapsed",
            )
            if st.button("🔔 Notify me", key="watch_submit", disabled=not w_slot, width='stretch'):
                d_str, h_str = w_slot.split("|")
                status, msg = create_slot_watch(email, sub_community, villa, d_str, int(h_str), w_n)
                ss["watch_flash"] = ("success" if status == "ok" else "info" if status in ("available", "duplicate", "invalid") else "warning", msg)
                if status in ("ok", "available", "duplicate", "invalid"):
                    ss["watch_reset"] = True
                st.rerun()

        if flash:
            (st.success if flash[0] == "success" else st.info if flash[0] == "info" else st.warning)(flash[1])

        live = [w for w in mine if not is_slot_in_past(w["date"], int(w["start_hour"]))]
        if live:
            st.caption("Your alerts — tap one to remove it")
            for w in live:
                if st.button(f"✕  {_slot_window_label(w['date'], int(w['start_hour']), int(w['hours']), show_hours=True)}",
                             key=f"watch_rm_{w['id']}", width='stretch'):
                    remove_slot_watch(w["id"], email)
                    st.rerun()

# --- COACH SPECIFIC HELPER FUNCTIONS ---

def get_coach_dashboard_stats(coach_email):
    """Calculates cumulative quota available across a coach's assigned villa pool."""
    villas_res = run_query(supabase.table("coach_villas").select("sub_community, villa").eq("coach_email", coach_email))
    assigned_villas = villas_res.data if villas_res and villas_res.data else []

    if not assigned_villas:
        return assigned_villas, 0, 0

    # Same two live queries per villa as get_active_bookings_count() (future date, or today at/after
    # the current hour) — just run for every villa in ONE batch of parallel round-trips instead of
    # looping villa-by-villa (each villa's own pair was already parallel, but not parallel *with*
    # the other villas' pairs). Same data source, same values, fewer round-trips.
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour

    def _future(v):
        q = supabase.table("bookings").select("id", count="exact").eq("villa", v['villa']).eq("sub_community", v['sub_community'])
        return run_query(q.gt("date", today_str))

    def _today(v):
        q = supabase.table("bookings").select("id", count="exact").eq("villa", v['villa']).eq("sub_community", v['sub_community'])
        return run_query(q.eq("date", today_str).gte("start_hour", now_hour))

    results = run_parallel(
        *[(lambda v=v: _future(v)) for v in assigned_villas],
        *[(lambda v=v: _today(v)) for v in assigned_villas],
    )
    n = len(assigned_villas)
    future_results, today_results = results[:n], results[n:]

    total_allowed = 0
    total_active = 0
    for v, res_f, res_t in zip(assigned_villas, future_results, today_results):
        limit = get_active_booking_limit(v['sub_community'], v['villa'])
        count_f = res_f.count if res_f and res_f.count is not None else 0
        count_t = res_t.count if res_t and res_t.count is not None else 0
        total_allowed += limit
        total_active += count_f + count_t

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
            
            # One live-DB check (active limit + daily limit) for this villa taking this single
            # hour — same helper the resident Book button uses, just for one hour at a time and
            # without writing an "Access Denied" log line (this is only choosing among a pool of
            # villas, not rejecting the coach's booking outright).
            can_take, _ = validate_booking_attempt(sub, villa_num, court, date_str, [hour], log_denials=False)

            # Check if this specific villa can take the slot
            if can_take:
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
def _fetch_user_bookings(villa, sub_community):
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

def get_user_bookings(villa, sub_community):
    """A villa's own active (non-coach) bookings, served from the shared snapshot."""
    rows, _since = _snapshot_rows()
    if rows is None:
        return _fetch_user_bookings(villa, sub_community)
    mine = [
        {"id": r["id"], "court": r["court"], "date": r["date"], "start_hour": r["start_hour"]}
        for r in _upcoming_rows(rows)
        if str(r["villa"]) == str(villa) and r["sub_community"] == sub_community and r.get("coach_email") is None
    ]
    mine.sort(key=lambda b: (b["date"], b["start_hour"]))
    return mine

@st.cache_data(ttl=30, show_spinner=False)
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
        res_future, res_today = run_parallel(
            lambda: run_query(supabase.table("bookings").select("villa, sub_community").gt("date", today_str)),
            lambda: run_query(supabase.table("bookings").select("villa, sub_community").eq("date", today_str).gte("start_hour", now_hour)),
        )
        all_rows = (res_future.data if res_future else []) + (res_today.data if res_today else [])
        unique_villas = sorted(list(set([f"{row['sub_community']} - {row['villa']}" for row in all_rows])))
        return unique_villas
    except Exception:
        return []

def get_emails_with_active_bookings():
    """Unique approved resident emails linked to any villa that currently has at least one
    active (future or remaining-today) booking. Used by the admin Broadcast Email tool."""
    villas = get_villas_with_active_bookings()
    if not villas:
        return []
    active_pairs = set()
    for v_str in villas:
        try:
            sub, villa = v_str.split(" - ", 1)
            active_pairs.add((sub, str(villa)))
        except Exception:
            continue
    if not active_pairs:
        return []
    # One approved-claims fetch, then filter to the active-booking villa set — same result as
    # calling get_claims_for_villa() once per villa, without N sequential round-trips.
    emails = set()
    try:
        claims_res = run_query(
            supabase.table("villa_claims")
            .select("sub_community, villa, email")
            .eq("status", "approved")
        )
        for c in (claims_res.data if claims_res and claims_res.data else []):
            if (c.get("sub_community"), str(c.get("villa"))) not in active_pairs:
                continue
            em = (c.get("email") or "").strip().lower()
            if em and "@" in em:
                emails.add(em)
    except Exception:
        return []
    return sorted(emails)

def get_merged_active_bookings_for_email(email):
    """Collect every active booking across all approved villas claimed by this email,
    merged into consecutive multi-hour blocks in the same shape expected by
    send_all_bookings_summary (list of dicts with court/date/start_hours/ids/v/sc).
    Mirrors what a resident would see if they clicked 'Email Me All My Bookings' for
    each of their villas, combined into one list."""
    claims = get_all_villas_for_email(email)
    approved = [c for c in claims if c.get("status") == "approved"]
    if not approved:
        return []

    pairs = []
    for c in approved:
        sub = c.get("sub_community")
        villa = c.get("villa")
        if not sub or villa is None:
            continue
        pairs.append((str(villa), sub))
    if not pairs:
        return []

    # Same get_user_bookings() results, fetched concurrently when the email holds multiple villas.
    booking_lists = run_parallel(*[lambda v=v, s=s: get_user_bookings(v, s) for v, s in pairs])

    raw_rows = []
    for (villa, sub), bookings in zip(pairs, booking_lists):
        for b in (bookings or []):
            raw_rows.append({
                "id": b["id"],
                "court": b["court"],
                "date": b["date"],
                "start_hour": b["start_hour"],
                "v": villa,
                "sc": sub,
            })

    return merge_consecutive_bookings(raw_rows)

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


def find_double_bookings():
    """Return groups where the same court + date + start_hour has more than one booking row.
    Each item: {court, date, start_hour, count, bookings: [{id, villa, sub_community, coach_email}, ...]}
    """
    try:
        rows = []
        chunk_size = 1000
        offset = 0
        while True:
            res = run_query(
                supabase.table("bookings")
                .select("id, court, date, start_hour, villa, sub_community, coach_email")
                .range(offset, offset + chunk_size - 1)
            )
            if not res or res.data is None:
                break
            rows.extend(res.data)
            if len(res.data) < chunk_size:
                break
            offset += chunk_size
        if not rows:
            return []

        groups = {}
        for r in rows:
            key = (r.get("court"), r.get("date"), r.get("start_hour"))
            groups.setdefault(key, []).append(r)

        duplicates = []
        for (court, date_str, hour), items in groups.items():
            if len(items) < 2:
                continue
            duplicates.append({
                "court": court,
                "date": date_str,
                "start_hour": hour,
                "count": len(items),
                "bookings": [
                    {
                        "id": b.get("id"),
                        "villa": b.get("villa"),
                        "sub_community": b.get("sub_community"),
                        "coach_email": b.get("coach_email"),
                    }
                    for b in items
                ],
            })
        duplicates.sort(key=lambda d: (d["date"] or "", d["court"] or "", d["start_hour"] or 0))
        return duplicates
    except Exception as e:
        print(f"find_double_bookings error: {e}")
        return []


def _double_booking_signature(dup):
    """Stable id for a duplicate group so we don't re-email the same conflict every hour."""
    ids = sorted(str(b.get("id")) for b in dup.get("bookings") or [])
    return f"{dup.get('court')}|{dup.get('date')}|{dup.get('start_hour')}|{','.join(ids)}"


def _already_notified_double_booking(signature, within_hours=24):
    """True if we already logged this exact double-booking signature recently."""
    try:
        cutoff = (get_utc_plus_4() - timedelta(hours=within_hours)).isoformat()
        res = run_query(
            supabase.table("logs")
            .select("id, details")
            .eq("event_type", "Double Booking Detected")
            .gte("timestamp", cutoff)
            .ilike("details", f"%⟦DUP:{signature}⟧%")
            .limit(1)
        )
        return bool(res and res.data)
    except Exception:
        return False


def notify_admin_of_double_bookings(duplicates, admin_email=None):
    """Email admin a summary of detected double bookings. Returns True if mail sent."""
    if not duplicates:
        return False
    admin_email = (admin_email or st.secrets.get("ADMIN_NOTIFY_EMAIL")
                   or st.secrets.get("GMAIL_USER") or "devkrea@gmail.com")
    if not admin_email or "@" not in admin_email:
        return False

    rows_html = ""
    for d in duplicates:
        holders = []
        for b in d["bookings"]:
            who = f"{b.get('sub_community')} Villa {b.get('villa')}"
            if b.get("coach_email"):
                who += f" (coach: {b['coach_email']})"
            holders.append(f"id={b.get('id')} · {who}")
        holders_list = "<br>".join(f"• {h}" for h in holders)
        hour = d.get("start_hour")
        time_disp = f"{int(hour):02d}:00 – {int(hour)+1:02d}:00" if hour is not None else "?"
        rows_html += f"""
        <div style="background:#fff5f5;border:1px solid #feb2b2;border-left:5px solid #c53030;
                    border-radius:8px;padding:14px;margin:12px 0;">
          <p style="margin:0 0 6px 0;"><b>Court:</b> {d.get('court')} &nbsp;|&nbsp;
             <b>Date:</b> {d.get('date')} &nbsp;|&nbsp; <b>Time:</b> {time_disp}</p>
          <p style="margin:0 0 6px 0;"><b>{d.get('count')} overlapping rows:</b></p>
          <p style="margin:0;font-size:14px;color:#2d3748;">{holders_list}</p>
        </div>
        """

    html = f"""
    <!DOCTYPE html>
    <html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color:#222;">
      <div style="max-width:640px;margin:24px auto;padding:24px;border:1px solid #e2e8f0;border-radius:12px;">
        <h2 style="color:#c53030;margin-top:0;">⚠️ Double Booking Detected</h2>
        <p>The Mira Court Booking integrity check found <b>{len(duplicates)}</b> court/date/time
        slot(s) with more than one booking row in the database.</p>
        {rows_html}
        <p style="font-size:13px;color:#718096;">Please review and remove the incorrect row(s) in Admin → Bookings &amp; Villas
        (or via Supabase). A unique constraint on (court, date, start_hour) should normally prevent this.</p>
      </div>
    </body></html>
    """
    subject = f"⚠️ Double booking alert — {len(duplicates)} conflict(s) in Mira Court Booking"
    return send_gmail_smtp(admin_email, subject, html)


def check_and_flag_double_bookings(force_notify=False):
    """Scan for double bookings; log and email admin for any group not notified in the last 24h.
    Returns (duplicates_list, newly_notified_count).
    force_notify=True emails even if recently notified (admin manual run).
    """
    duplicates = find_double_bookings()
    if not duplicates:
        return [], 0

    to_notify = []
    for d in duplicates:
        sig = _double_booking_signature(d)
        d["_signature"] = sig
        if force_notify or not _already_notified_double_booking(sig, within_hours=24):
            to_notify.append(d)

    newly = 0
    if to_notify:
        ok = notify_admin_of_double_bookings(to_notify)
        if ok:
            newly = len(to_notify)
            for d in to_notify:
                holders = ", ".join(
                    f"{b.get('sub_community')} Villa {b.get('villa')} (id={b.get('id')})"
                    for b in d["bookings"]
                )
                hour = d.get("start_hour")
                time_disp = f"{int(hour):02d}:00" if hour is not None else "?"
                add_log(
                    "Double Booking Detected",
                    f"Double booking on {d.get('court')} {d.get('date')} at {time_disp}: "
                    f"{d.get('count')} rows — {holders} ⟦DUP:{d['_signature']}⟧",
                )
        else:
            # Still log so the issue is visible even if email fails
            for d in to_notify:
                hour = d.get("start_hour")
                time_disp = f"{int(hour):02d}:00" if hour is not None else "?"
                add_log(
                    "Double Booking Detected",
                    f"Double booking on {d.get('court')} {d.get('date')} at {time_disp} "
                    f"({d.get('count')} rows) — admin email FAILED ⟦DUP:{d['_signature']}⟧",
                )
    return duplicates, newly


@st.cache_data(ttl=3600, show_spinner=False)
def _run_scheduled_double_booking_check():
    """Background integrity check at most once per hour per app instance."""
    try:
        check_and_flag_double_bookings(force_notify=False)
    except Exception as e:
        print(f"scheduled double-booking check error: {e}")
    return True


@st.cache_data(ttl=3600, show_spinner=False)
def _run_scheduled_out_of_range_purge():
    """Out-of-range villa purge, at most once an hour per app instance. It used to run on EVERY
    script rerun (every click/keystroke of every user) and downloads the whole villa_claims and
    bookings tables each time. Villa numbers are already validated at login/booking time, so
    an hourly sweep is plenty."""
    purge_out_of_range_records()
    return True

@st.cache_data(ttl=300, show_spinner=False)
def _run_scheduled_db_cleanup():
    """database_cleanup.run_db_cleanup(), throttled to at most once every 5 minutes per app
    instance instead of on every rerun. Lower ttl if that job is time-critical."""
    try:
        from database_cleanup import run_db_cleanup
        run_db_cleanup(supabase, courts, donor_villas=DONOR_VILLAS)
        # run_db_cleanup lives in its own module (database_cleanup.py) and writes/deletes booking
        # rows straight to Supabase with no knowledge of this file's caches at all — so unlike
        # book_slot()/delete_booking(), nothing there ever clears the shared display snapshot.
        # Clearing it here, right after every run, is what makes a just-auto-booked or
        # just-purged slot show correctly instead of waiting for the cache's own TTL to expire.
        invalidate_booking_caches()
    except Exception as e:
        print(f"scheduled db cleanup error: {e}")
    return True

def _process_background_tasks():
    try:
        _run_scheduled_out_of_range_purge()
        _run_scheduled_log_retention()
        _run_scheduled_double_booking_check()
        _run_scheduled_db_cleanup()
        _run_scheduled_watch_cleanup()
        _run_scheduled_tournament_processing()
        _run_scheduled_resource_check()
    except Exception:
        pass

# ==============================================================================
# --- TOURNAMENT BULK BOOKING (admin-only) ---
# ==============================================================================
# Lets an admin plan a tournament day up to a few weeks out — pick the courts, an hour range, and
# a pool of villas with room to spare — and have the app book those slots automatically the
# moment that date enters the normal 14-day booking window (this never books a date before
# residents themselves could). Every slot is created through the exact same
# validate_booking_attempt() + book_slot() path a resident's own Book button uses, so it obeys the
# same active/daily limits (Mira 1 229/231/249 rule included), writes the same "Booking Created"
# log line, and sends the same confirmation email to the villa's registered address — nothing
# about the result is distinguishable from a resident booking it themselves, and the admin/
# tournament origin is never written into the shared `logs` table residents can see. The only
# admin-visible trace lives in the dedicated table below.
# SAFEGUARD: a villa in the pool is only ever touched at the moment it's actually needed to fill a
# slot. If it's already at its active-booking limit right then, its own 2 farthest-out (latest
# date/hour) active bookings are cancelled first — through the normal delete_booking() path — to
# guarantee the tournament doesn't run out of room on the villa it was counting on. The affected
# resident gets the exact SAME cancellation email a self-service cancel sends — nothing mentions a
# tournament — because some residents will never be told a tournament exists at all (that's the
# whole point of this feature), including villas only ever used as anonymous quota donors. This
# never touches a pool villa that ends up not being needed.
# Needs a `tournament_requests` table (columns: id bigint generated always as identity primary
# key, created_at timestamptz default now(), target_date text, courts text, villa_pool text,
# start_hour int, end_hour int, status text default 'pending', result_summary text, processed_at
# timestamptz). Until it exists, the admin panel just says so and nothing else is affected — same
# convention as slot_watches above.
TOURNAMENT_TABLE = "tournament_requests"

def get_tournament_requests():
    """All tournament bulk-booking requests, newest first — or None if the table doesn't exist
    yet (same 'feature not switched on' convention used for slot watches)."""
    try:
        res = supabase.table(TOURNAMENT_TABLE).select("*").order("created_at", desc=True).execute()
        return res.data or []
    except Exception:
        return None

def get_villas_with_free_quota(target_date_str):
    """'Sub Community - Villa' labels (from the same master claimed-villa list the rest of the
    admin panel uses) that right now have at least one free active-booking slot AND haven't hit
    the 2/day limit for `target_date_str`. Only for populating the tournament pool picker — since
    the target date can be weeks away, a villa's own bookings between now and then can still
    change its real room; the actual gate is validate_booking_attempt() at processing time, same
    as any other write path. Batches every villa's live counts into one round of parallel
    queries rather than looping villa-by-villa."""
    candidates = get_all_claimed_villas()
    if not candidates:
        return []
    parsed = []
    for label in candidates:
        if " - " not in label:
            continue
        sub, villa = label.split(" - ", 1)
        parsed.append((label, sub, villa))
    if not parsed:
        return []

    results = run_parallel(
        *[(lambda sub=sub, villa=villa: get_active_bookings_count(villa, sub)) for _, sub, villa in parsed],
        *[(lambda sub=sub, villa=villa: get_daily_bookings_count(villa, sub, target_date_str)) for _, sub, villa in parsed],
    )
    n = len(parsed)
    active_counts, daily_counts = results[:n], results[n:]

    free = []
    for (label, sub, villa), active_count, daily_count in zip(parsed, active_counts, daily_counts):
        active_limit = get_active_booking_limit(sub, villa, for_date=target_date_str)
        if active_count < active_limit and daily_count < 2:
            free.append(label)
    return free

def create_tournament_request(target_date_str, courts_list, villa_pool_labels, start_hour, end_hour):
    try:
        supabase.table(TOURNAMENT_TABLE).insert({
            "target_date": target_date_str,
            "courts": json.dumps(courts_list),
            "villa_pool": json.dumps(villa_pool_labels),
            "start_hour": start_hour,
            "end_hour": end_hour,
            "status": "pending",
        }).execute()
        return True
    except Exception:
        return False

def delete_tournament_request(request_id):
    try:
        supabase.table(TOURNAMENT_TABLE).delete().eq("id", request_id).execute()
        return True
    except Exception:
        return False

def _evict_farthest_active_bookings(sub_community, villa, count=2):
    """Cancels this villa's own `count` farthest-out (latest date/hour) ACTIVE bookings — through
    the normal delete_booking() path, so logging/cache invalidation/slot alerts all behave exactly
    like any other cancellation — to free room for a tournament commitment. Used only when a villa
    in a tournament's pool is found completely full at processing time (see
    process_tournament_request()'s active-limit safeguard). The resident is sent the ORDINARY
    cancellation email (send_booking_notification_once, same as a self-service cancel) — never
    anything mentioning a tournament — so this is indistinguishable from the villa cancelling its
    own booking. Returns the (court, date, start_hour) tuples that were freed."""
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    res = run_query(
        supabase.table("bookings").select("id, court, date, start_hour")
        .eq("sub_community", sub_community).eq("villa", villa)
        .gte("date", today_str)
    )
    rows = res.data if res and res.data else []
    active_rows = [r for r in rows if r['date'] > today_str or int(r['start_hour']) >= now_hour]
    if not active_rows:
        return []
    active_rows.sort(key=lambda r: (r['date'], int(r['start_hour'])))  # soonest first, farthest last
    to_evict = active_rows[-count:] if count < len(active_rows) else active_rows

    freed = []
    for r in to_evict:
        delete_booking(r['id'], villa, sub_community)
        for claim in get_claims_for_villa(sub_community, villa):
            if claim.get("status") == "approved" and claim.get("email"):
                send_booking_notification_once("deleted", villa, sub_community, r['court'], r['date'], [r['start_hour']], claim["email"])
        freed.append((r['court'], r['date'], r['start_hour']))
    return freed

def process_tournament_request(req):
    """Books every (court, hour) slot in a tournament request across its villa pool, live, right
    now — called once the request's target_date has entered the normal booking window. Each slot
    goes through validate_booking_attempt() + book_slot(), so limits, logging and behavior exactly
    match a resident booking it themselves. The villa pool is shuffled and round-robined so one
    villa in a big pool doesn't grab every slot it happens to still be eligible for. Marks the
    request 'done' with a short summary either way (so it's never retried), and invalidates the
    shared display cache ONCE for the whole batch rather than per slot."""
    target_date = req["target_date"]
    try:
        courts_list = json.loads(req["courts"]) or []
    except Exception:
        courts_list = []
    try:
        villa_pool_labels = json.loads(req["villa_pool"]) or []
    except Exception:
        villa_pool_labels = []
    start_hour, end_hour = req["start_hour"], req["end_hour"]

    valid_hours = get_start_hours_for_date(target_date)
    requested_hours = sorted(h for h in valid_hours if start_hour <= h < end_hour)

    pool = []
    for label in villa_pool_labels:
        if " - " in label:
            sub, villa = label.split(" - ", 1)
            pool.append((sub, villa))
    random.shuffle(pool)  # same fairness spirit as the Legends of Mira auto-book job

    booked = []      # [{"villa", "sub_community", "court", "hour"}]
    unbooked = []     # ["Court @ HH:00", ...]
    evicted = []      # ["Court @ HH:00 (Sub - Villa)", ...] — bookings force-freed to honor the pool
    cleared_villas = set()  # (sub, villa) already given its one eviction pass this run

    for court in courts_list:
        for hour in requested_hours:
            placed = False
            for sub, villa in pool:
                ok, err = validate_booking_attempt(sub, villa, court, target_date, [hour], log_denials=False)
                # "Limit Reached" (the active-limit message) is the one case this safeguard covers —
                # a villa in the tournament's own pool that has used up all its active slots. It is
                # deliberately NOT triggered by "Daily Limit Reached" or an unavailable-slot message;
                # those aren't what "already used up its active slots" means, and evicting for them
                # wouldn't free anything relevant anyway.
                if not ok and err and err.startswith("Limit Reached") and (sub, villa) not in cleared_villas:
                    freed_here = _evict_farthest_active_bookings(sub, villa, count=2)
                    cleared_villas.add((sub, villa))
                    evicted.extend(f"{c} @ {h:02d}:00 ({sub} - {villa})" for c, d, h in freed_here)
                    ok, err = validate_booking_attempt(sub, villa, court, target_date, [hour], log_denials=False)
                if ok and book_slot(villa, sub, court, target_date, hour):
                    booked.append({"villa": villa, "sub_community": sub, "court": court, "hour": hour})
                    pool.remove((sub, villa))
                    pool.append((sub, villa))  # send it to the back of the queue for the next slot
                    placed = True
                    break
            if not placed:
                unbooked.append(f"{court} @ {hour:02d}:00")

    # One confirmation email per villa+court+consecutive-hour block — exactly like a normal 2-hour
    # resident booking — sent to every approved resident email on file for that villa.
    email_blocks = merge_consecutive_bookings([
        {"court": b["court"], "date": target_date, "start_hour": b["hour"], "id": i,
         "v": b["villa"], "sc": b["sub_community"]}
        for i, b in enumerate(booked)
    ])
    for block in email_blocks:
        for claim in get_claims_for_villa(block["sc"], block["v"]):
            if claim.get("status") == "approved" and claim.get("email"):
                send_booking_notification_once(
                    "created", block["v"], block["sc"], block["court"], target_date,
                    block["start_hours"], claim["email"],
                )

    summary = f"Booked {len(booked)} slot(s)."
    if evicted:
        summary += f" Freed up room by cancelling: {', '.join(evicted)}."
    if unbooked:
        summary += f" Could not book: {', '.join(unbooked)}."

    try:
        supabase.table(TOURNAMENT_TABLE).update({
            "status": "done", "result_summary": summary, "processed_at": get_utc_plus_4().isoformat(),
        }).eq("id", req["id"]).execute()
    except Exception:
        pass

    if booked:
        invalidate_booking_caches()
    return summary

@st.cache_data(ttl=300, show_spinner=False)
def _run_scheduled_tournament_processing():
    """Processes any pending tournament bulk-booking requests whose target_date has just entered
    the normal 14-day booking window, at most once every 5 minutes per app instance — same cadence
    as the other background jobs. Silently does nothing if the feature's table doesn't exist yet."""
    try:
        pending = get_tournament_requests()
        if not pending:
            return True
        window = {d.strftime('%Y-%m-%d') for d in get_next_14_days()}
        for req in pending:
            if req.get("status") == "pending" and req.get("target_date") in window:
                process_tournament_request(req)
    except Exception as e:
        print(f"scheduled tournament processing error: {e}")
    return True

# ==============================================================================
# --- RESOURCE MONITOR (admin-only) ---
# ==============================================================================
# Streamlit Community Cloud doesn't expose a usage API to the app itself, so this is the closest
# an app can get to checking its own footprint: `psutil` reads THIS PROCESS's own CPU/memory
# directly, which is measured the same way no matter how the container virtualizes /proc (unlike
# system-wide readings, which can silently report the underlying host instead of the container's
# actual quota). CPU and memory below are compared against Streamlit Community Cloud's DOCUMENTED
# per-app ceilings — Streamlit can change these without notice, so treat this as an early-warning
# signal, not an exact measurement. Disk has no equivalent process-scoped reading (psutil.disk_usage
# only sees the shared host filesystem here, confirmed in practice to report over 100GB used against
# this 50GB figure), so it's kept only as a documented reference and is never turned into a
# percentage or used to trigger an alert — see get_resource_usage()'s docstring.
STREAMLIT_CLOUD_CPU_CORES_MAX = 2.0
STREAMLIT_CLOUD_MEMORY_MB_MAX = 2700.0
STREAMLIT_CLOUD_DISK_GB_MAX = 50.0  # reference only — not used in any calculation, see comment above
RESOURCE_ALERT_THRESHOLD_PCT = 75
RESOURCE_ALERT_RECIPIENT = "devkrea@gmail.com"

@st.cache_resource
def _get_resource_monitor_process_handle():
    """One psutil.Process handle kept alive for the app's whole lifetime (via st.cache_resource,
    which — unlike st.cache_data — persists a live object rather than a serialized value). CPU%
    is only meaningful as a delta between two points in time; priming it here means every later
    call to get_resource_usage() reports usage since the PREVIOUS call, non-blocking, instead of
    pausing the request to take a fresh instantaneous sample. Returns None if psutil isn't
    installed, so the caller can fail soft instead of crashing the whole app."""
    try:
        import psutil
        p = psutil.Process(os.getpid())
        p.cpu_percent(interval=None)  # discard the meaningless first reading; see docstring above
        return p
    except Exception:
        return None

def get_resource_usage():
    """This app's own CPU/memory footprint, and each one's percentage of Streamlit Community
    Cloud's documented per-app limit — plus a disk figure that is informational only (see below).
    Returns None if psutil isn't installed (add `psutil` to requirements.txt to enable this) or if
    reading usage fails for any reason — callers should treat None as "the monitor isn't available
    right now", not as zero usage.

    Disk is NOT compared against Streamlit's 50GB limit the way CPU/memory are compared against
    theirs. CPU and memory are read from THIS PROCESS specifically (psutil.Process()), which is
    reliable regardless of how the container virtualizes things. Disk has no per-process
    equivalent — psutil.disk_usage('/') can only report the filesystem's own total/used, and on
    Streamlit Community Cloud that reflects the shared underlying node (confirmed in practice: it
    reported >100GB used against a 50GB app limit), not this app's own slice of it. Rather than
    show a percentage that's been observed to be wrong, disk is reported as a plain, unscored
    figure and left out of the 75% alert entirely."""
    try:
        import psutil
    except ImportError:
        return None
    proc = _get_resource_monitor_process_handle()
    if proc is None:
        return None
    try:
        cores_used = proc.cpu_percent(interval=None) / 100.0
        mem_mb = proc.memory_info().rss / (1024 * 1024)
        disk = psutil.disk_usage('/')
        return {
            "cores_used": round(cores_used, 3),
            "cpu_pct_of_limit": round(min(100.0, cores_used / STREAMLIT_CLOUD_CPU_CORES_MAX * 100), 1),
            "mem_mb": round(mem_mb, 1),
            "mem_pct_of_limit": round(min(100.0, mem_mb / STREAMLIT_CLOUD_MEMORY_MB_MAX * 100), 1),
            "disk_used_gb": round(disk.used / (1024 ** 3), 2),
            "disk_total_gb": round(disk.total / (1024 ** 3), 2),
        }
    except Exception:
        return None

def _send_resource_alert_email(usage, triggered_by):
    """Sends the resource-usage warning to the developer inbox. `triggered_by` is a list (e.g.
    ["CPU", "Memory"]) naming which metric(s) actually crossed the threshold, so the email is
    specific about what's high rather than a generic 'something's wrong'."""
    try:
        subject = f"⚠️ Court Booking App — Resource Usage Warning ({', '.join(triggered_by)})"
        html_content = (
            f"<h3>Streamlit Cloud Resource Warning</h3>"
            f"<p>This app has reached {RESOURCE_ALERT_THRESHOLD_PCT}% or more of Streamlit Community "
            f"Cloud's documented per-app limit on: <b>{', '.join(triggered_by)}</b>.</p>"
            f"<table style='border-collapse:collapse; font-family: -apple-system, sans-serif;'>"
            f"<tr><td style='padding:4px 12px;'>CPU</td><td style='padding:4px 12px;'>"
            f"<b>{usage['cores_used']:.2f} / {STREAMLIT_CLOUD_CPU_CORES_MAX:.0f} cores</b> ({usage['cpu_pct_of_limit']}%)</td></tr>"
            f"<tr><td style='padding:4px 12px;'>Memory</td><td style='padding:4px 12px;'>"
            f"<b>{usage['mem_mb']:.0f} MB / {STREAMLIT_CLOUD_MEMORY_MB_MAX:.0f} MB</b> ({usage['mem_pct_of_limit']}%)</td></tr>"
            f"<tr><td style='padding:4px 12px;'>Disk (host, informational)</td><td style='padding:4px 12px;'>"
            f"<b>{usage['disk_used_gb']:.2f} / {usage['disk_total_gb']:.2f} GB</b></td></tr>"
            f"</table>"
            f"<p style='color:#718096; font-size:13px;'>Checked at {get_utc_plus_4().strftime('%Y-%m-%d %H:%M')} (UTC+4). "
            f"Streamlit Cloud's limits are documented, approximate, and can change without notice — "
            f"treat this as an early-warning signal, not an exact measurement.</p>"
        )
        send_gmail_smtp(RESOURCE_ALERT_RECIPIENT, subject, html_content)
    except Exception as e:
        print(f"Error sending resource alert email: {e}")

@st.cache_data(ttl=1800, show_spinner=False)
def _run_scheduled_resource_check():
    """Checks this app's own resource usage at most once every 30 minutes per app instance, and
    emails RESOURCE_ALERT_RECIPIENT the moment any metric reaches RESOURCE_ALERT_THRESHOLD_PCT.
    The ttl here IS the alert throttle — while usage stays above the threshold this simply re-fires
    at most once per 30-minute window rather than emailing on every rerun."""
    try:
        usage = get_resource_usage()
        if not usage:
            return True
        triggered = [
            name for name, pct in (
                ("CPU", usage["cpu_pct_of_limit"]),
                ("Memory", usage["mem_pct_of_limit"]),
                # Disk deliberately excluded — see get_resource_usage()'s docstring for why it
                # can't be reliably compared against Streamlit's 50GB per-app limit here.
            ) if pct >= RESOURCE_ALERT_THRESHOLD_PCT
        ]
        if triggered:
            _send_resource_alert_email(usage, triggered)
    except Exception as e:
        print(f"scheduled resource check error: {e}")
    return True

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

def get_available_hours(court, date_str):
    # Display-only (fills the time pickers): answered from the shared booking snapshot. Booking
    # itself re-checks the slot against the database, and the DB unique constraint has the last word.
    booked_hours = {h for (c, h) in _day_map(date_str) if c == court}
    is_past = _past_checker()
    available = []
    for h in get_start_hours_for_date(date_str):
        if h not in booked_hours and not is_past(date_str, h):
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
    * **One email per villa:** To prevent abuse and false claiming, only a single verified email may be registered to each villa.
    ---
    Please enter your resident email below to receive your 6-digit verification code.
    💬 *Please reach out to Dev in case you have any queries.*
    """)
    if st.button("Got it — Continue 🎾", type="primary", width='stretch'):
        st.session_state.seen_migration_notice = True
        st.rerun(scope="app")

@st.dialog("📢 Community Notice")
def show_community_poster_dialog(poster_version="2026-09-25"):
    """Show the current community poster to every logged-in user.

    Dismiss closes it for the current Streamlit session. "Don't show again"
    writes a versioned flag to browser localStorage so the same poster is not
    shown again on that browser. Bump poster_version whenever a new poster is
    published.
    """
    poster_url = "https://raw.githubusercontent.com/mahadevbk/courtbooking/main/resources/Poster.jpeg"
    st.image(poster_url, width='stretch')

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Dismiss", width='stretch', key="community_poster_dismiss"):
            st.session_state["community_poster_dismissed"] = True
            st.rerun(scope="app")
    with col2:
        if st.button("Don't show again", type="primary", width='stretch', key="community_poster_never"):
            st_javascript(
                f"localStorage.setItem('mira_community_poster_seen', {json.dumps(poster_version)});",
                key=f"js_community_poster_seen_{poster_version}"
            )
            st.session_state["community_poster_dismissed"] = True
            st.rerun(scope="app")


def maybe_show_community_poster():
    """Show the poster once per browser unless the user chose 'Don't show again'."""
    if st.session_state.get("community_poster_dismissed", False):
        return

    # Version this key so publishing a new poster can make it visible again to everyone.
    poster_version = "2026-09-25"
    stored = st_javascript(
        "localStorage.getItem('mira_community_poster_seen') || '';",
        key="js_community_poster_seen_read"
    )

    # st_javascript can return None on its first browser round-trip. In that case,
    # show the poster optimistically; the user's choice is persisted immediately.
    if isinstance(stored, str) and stored == poster_version:
        st.session_state["community_poster_dismissed"] = True
        return

    show_community_poster_dialog(poster_version)


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
    if st.button("I Understand — Proceed", type="primary", width='stretch'):
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
    if st.button("Close / Logout", width='stretch'):
        logout_action()

@st.dialog("⚠️ Action Required: 1 Email Per Villa")
def show_email_consolidation_dialog(sub_community, villa, claims, current_email):
    """One-time forced migration for legacy villas that still have more than
    MAX_EMAILS_PER_VILLA approved emails on file (from before the 1-email-per-villa policy).

    `claims` is that villa's current list of APPROVED villa_claims rows (from
    get_approved_email_claims_for_villa()). The caller follows this with st.stop(), so nothing
    else in the app renders until the signed-in user picks which email stays — the other is
    deleted from villa_claims immediately, notified by email, and can no longer log in to this
    villa. There is deliberately no "skip" / "remind me later" option: this exists specifically
    to stop ongoing abuse, so letting it be dismissed would defeat the point.
    """
    # De-duplicate by email in case a villa somehow ended up with more than one approved row for
    # the same address — only distinct emails are shown/offered as choices.
    by_email = {}
    for c in claims:
        e = (c.get("email") or "").strip().lower()
        if e and e not in by_email:
            by_email[e] = c
    emails = list(by_email.keys())

    if len(emails) <= MAX_EMAILS_PER_VILLA:
        # Already resolved (e.g. a co-resident finished this same dialog a moment ago on another
        # device/tab) — nothing left to do, just let the normal rerun move on.
        st.success("This villa is already down to one registered email. Continuing…")
        time.sleep(1)
        st.rerun(scope="app")
        return

    st.markdown(f"""
    To prevent abuse and false claiming, **{sub_community} - Villa {villa}** may now have only
    **{MAX_EMAILS_PER_VILLA} verified resident email** on file — previously up to 2 were allowed.

    This villa currently has **{len(emails)}** registered emails. Please choose the **one email
    that should remain** with this villa. The other will be **permanently removed** and will no
    longer be able to log in here.
    """)

    current_clean = (current_email or "").strip().lower()
    labels = [e + (" (you)" if e == current_clean else "") for e in emails]
    default_idx = emails.index(current_clean) if current_clean in emails else 0
    picked_label = st.radio(
        "Which email should remain with this villa?",
        options=labels,
        index=default_idx,
        key=f"consolidate_choice_{sub_community}_{villa}"
    )
    choice = emails[labels.index(picked_label)]
    other = next(e for e in emails if e != choice)

    st.warning(
        f"📧 **{choice}** will remain registered to this villa.  \n"
        f"🗑️ **{other}** will be permanently removed and lose access."
    )
    confirmed = st.checkbox(
        f"I confirm — keep {choice}, permanently remove {other}. This cannot be undone.",
        key=f"consolidate_confirm_{sub_community}_{villa}"
    )

    if st.button("✅ Confirm & Continue", type="primary", width='stretch', disabled=not confirmed):
        other_ids = [c["id"] for c in claims if (c.get("email") or "").strip().lower() == other]
        for cid in other_ids:
            run_query(supabase.table("villa_claims").delete().eq("id", cid))
        get_approved_email_claims_for_villa.clear()
        add_log(
            "Villa Claim Removed",
            f"1-email-per-villa migration: {sub_community} Villa {villa} reduced from {len(emails)} to "
            f"{MAX_EMAILS_PER_VILLA} email. Kept {choice}, removed {other}."
        )
        try:
            send_gmail_smtp(
                other,
                f"Access update for {sub_community} Villa {villa}",
                (
                    "<p>Hi,</p>"
                    "<p>To prevent abuse and false claiming, Mira Court Booking now allows only one "
                    "registered email per villa.</p>"
                    f"<p>For <b>{sub_community} - Villa {villa}</b>, <b>{html.escape(choice)}</b> was "
                    f"chosen to remain the registered email, so this email (<b>{html.escape(other)}</b>) "
                    "no longer has access.</p>"
                    "<p>If you believe this is a mistake, please reach out to Dev.</p>"
                )
            )
        except Exception:
            pass

        if choice == current_clean:
            st.success(f"Done — {choice} remains registered to this villa. Continuing…")
            time.sleep(1.2)
            st.rerun(scope="app")
        else:
            st.success(
                f"Done — {choice} remains registered to this villa. "
                f"You'll be logged out now since {current_email} no longer has access here."
            )
            time.sleep(1.8)
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

def build_ban_tag(email, villa_pairs):
    """Embeds the banned email and villa list as a machine-readable tag inside a Sniping
    Penalty log entry, so get_active_ban() can check it reliably regardless of sub-community
    naming quirks (e.g. 'Mira Oasis 3A'), rather than parsing free-text prose."""
    email_part = (email or "").strip().lower()
    villas_part = ";".join(f"{s}|{v}" for s, v in villa_pairs if s and v)
    return f"⟦BAN_EMAIL:{email_part}⟧⟦BAN_VILLAS:{villas_part}⟧"

def get_active_ban(email=None, sub_community=None, villa=None):
    """Checks whether the given email and/or villa is currently covered by an active Sniping
    Penalty/Lockout that hasn't since been cleared by an Admin Reset. Returns
    (expiry_datetime, raw_reason_text) if banned, else (None, None).

    Understands the structured ⟦BAN_EMAIL:...⟧⟦BAN_VILLAS:...⟧ tags written by newer penalty
    entries, and falls back to parsing the free-text 'Mira X Villa Y' / email mentions in older
    penalty log entries for backward compatibility with penalties applied before this tagging
    existed.
    """
    email_clean = (email or "").strip().lower()
    villa_tag = f"{sub_community} - {villa}" if (sub_community and villa) else None
    if not email_clean and not villa_tag:
        return None, None

    now = get_utc_plus_4()
    cutoff = (now - timedelta(hours=96)).isoformat()
    try:
        res = run_query(
            supabase.table("logs").select("timestamp, event_type, details, fingerprint")
            .gte("timestamp", cutoff)
            .in_("event_type", ["Sniping Penalty", "Sniping Lockout", "Admin Reset"])
            .order("timestamp", desc=True)
        )
        logs = res.data if res and res.data else []
    except Exception:
        return None, None

    cleared = []
    for entry in logs:
        if entry.get("event_type") != "Admin Reset":
            continue
        try:
            ts = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            continue
        details = entry.get("details") or ""
        if not any(term in details.lower() for term in ["cleared cooldown", "reset cooldown", "cleared restrictions", "ownership reset", "wrong villa"]):
            continue
        m_email = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', details)
        if m_email:
            cleared.append((m_email.group(0).lower(), ts))
        if entry.get("fingerprint"):
            cleared.append((entry["fingerprint"], ts))

    def _is_cleared(key, penalty_ts):
        return any(k == key and reset_ts >= penalty_ts for k, reset_ts in cleared)

    latest_expiry, latest_reason = None, None
    for entry in logs:
        if entry.get("event_type") not in ("Sniping Penalty", "Sniping Lockout"):
            continue
        try:
            ts = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            continue
        expiry = ts + timedelta(hours=96)
        if expiry <= now:
            continue

        details = entry.get("details") or ""
        fp = entry.get("fingerprint") or ""

        banned_email, banned_villas = None, set()
        m_tag_email = re.search(r'⟦BAN_EMAIL:(.*?)⟧', details)
        m_tag_villas = re.search(r'⟦BAN_VILLAS:(.*?)⟧', details)
        if m_tag_email or m_tag_villas:
            if m_tag_email and m_tag_email.group(1):
                banned_email = m_tag_email.group(1).strip().lower()
            if m_tag_villas and m_tag_villas.group(1):
                for pair in m_tag_villas.group(1).split(";"):
                    if "|" in pair:
                        s, v = pair.split("|", 1)
                        banned_villas.add(f"{s.strip()} - {v.strip()}")
        else:
            m_email = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', details)
            if m_email:
                banned_email = m_email.group(0).lower()
            for m in re.finditer(r"(Mira(?:\s+Oasis)?\s+\w+)\s+Villa\s+(\d+)", details):
                banned_villas.add(f"{m.group(1)} - {m.group(2)}")

        cleared_key = banned_email or fp
        if cleared_key and _is_cleared(cleared_key, ts):
            continue

        matches = (email_clean and banned_email and banned_email == email_clean) or (villa_tag and villa_tag in banned_villas)
        if matches and (not latest_expiry or expiry > latest_expiry):
            latest_expiry, latest_reason = expiry, details

    return latest_expiry, latest_reason

def _attempt_resident_login(otp_sub, otp_villa, otp_email_input):
    """Runs the normal resident validation + OTP/PIN routing. Shared by the main login form
    and by the 'Continue as Resident' choice offered to emails that are registered as both
    a coach and a resident.

    Speed: the old version ran ~6 Supabase queries one after another (three of them fetching
    the same villa's claims). Now the four independent lookups run concurrently, and the
    villa's claims are fetched once and reused for the existing-claim, claim-count and
    cooldown checks. The decision logic and messages are unchanged."""
    max_allowed = SUB_COMMUNITY_VILLA_LIMITS.get(otp_sub, 9999)
    if not otp_sub or not otp_villa:
        st.error("Please specify your Sub-Community and Villa Number.")
        return
    if not otp_villa.isdigit() or not (1 <= int(otp_villa) <= max_allowed):
        st.error(f"Invalid villa number for {otp_sub}. Must be between 1 and {max_allowed}.")
        return
    if not otp_email_input or "@" not in otp_email_input:
        st.error("Please provide a valid email address.")
        return

    current_uuid = st.session_state.get("device_uuid", "device_pending")
    email_clean = otp_email_input.strip().lower()

    with st.spinner("Checking your details..."):
        ban_check, villa_claims, email_claims, uuid_villas = run_parallel(
            lambda: get_active_ban(email=otp_email_input, sub_community=otp_sub, villa=otp_villa),
            lambda: get_claims_for_villa(otp_sub, otp_villa),
            lambda: get_all_villas_for_email(otp_email_input),
            lambda: get_uuid_claimed_villas(current_uuid),
        )

    existing_claim = _find_claim_for_email(villa_claims, email_clean)

    if ban_check[0]:
        ban_expiry, ban_reason = ban_check
        now_precheck = get_utc_plus_4()
        hours_left = max(1, int((ban_expiry - now_precheck).total_seconds() // 3600))
        until_str = ban_expiry.strftime("%A, %b %d at %H:%M")
        st.error(
            f"🚫 **Misuse of System — Access Banned**\n\n"
            f"This villa and/or email address is currently under a fair-use suspension due to "
            f"detected court-booking sniping. Access is **banned until {until_str}** "
            f"(~{hours_left}h remaining).\n\n"
            "If you believe this is a mistake, please contact Dev via Court Maintenance."
        )
        add_log(
            "Access Denied",
            f"Banned login/registration attempt blocked for {otp_sub} Villa {otp_villa} by {otp_email_input} "
            f"(ban active until {ban_expiry.isoformat()})",
            fingerprint=current_uuid
        )
        # If this villa is already banned but the SPECIFIC email attempting it isn't the one on
        # record (i.e. someone is trying a fresh email to dodge the lockout), extend the ban to
        # cover this new email too, so switching emails doesn't bypass it.
        existing_banned_email = None
        m_existing_email = re.search(r'⟦BAN_EMAIL:(.*?)⟧', ban_reason or "")
        if m_existing_email and m_existing_email.group(1):
            existing_banned_email = m_existing_email.group(1).strip().lower()
        if not existing_claim and email_clean != existing_banned_email:
            extra_tag = build_ban_tag(otp_email_input, [(otp_sub, otp_villa)])
            add_log(
                "Sniping Penalty",
                f"Ban extended to new email {otp_email_input} attempting to register already-banned "
                f"villa {otp_sub} Villa {otp_villa} {extra_tag}",
                fingerprint=current_uuid
            )
        return

    if is_disposable_email(otp_email_input):
        st.error("Disposable/temporary email domains are not allowed. Please use a personal or work email.")
        return

    current_claims_count = sum(1 for c in (villa_claims or []) if c.get("status") == "approved")
    email_villas_count = len({
        f"{c['sub_community']}::{c['villa']}"
        for c in (email_claims or []) if c.get("status") == "approved"
    })
    max_villas_allowed = get_max_villas_for_email(otp_email_input)
    is_on_cooldown, hours_left = _cooldown_from_claims(villa_claims, otp_email_input)
    target_pair = f"{otp_sub}::{otp_villa}"

    if not existing_claim and is_on_cooldown:
        st.error(
            f"🚫 Security Lockout: This villa ({otp_sub} - Villa {otp_villa}) already has "
            f"{MAX_EMAILS_PER_VILLA} registered email(s), with an active 72-hour ownership change "
            f"cooldown ({hours_left} hours remaining). "
            "Please contact Dev in Court Maintenance for urgent reassignment."
        )
        add_log("Access Denied", f"Villa {otp_sub} Villa {otp_villa} 72h cooldown triggered by {otp_email_input} ({hours_left}h left)", fingerprint=current_uuid)
    elif not existing_claim and current_claims_count >= MAX_EMAILS_PER_VILLA:
        st.error(
            f"🚫 This villa ({otp_sub} - Villa {otp_villa}) already has {MAX_EMAILS_PER_VILLA} verified "
            "resident email(s) attached. Only one verified email is allowed per villa. "
            "If you recently moved in or need to update your registered email, please reach out via the contact channels in Court Maintenance."
        )
    elif not existing_claim and email_villas_count >= max_villas_allowed:
        # Same generic message and log format regardless of WHY the cap was hit (the normal
        # 3-villa cap, or the 1-villa greylist cap for a known abuser) — a greylisted email gets
        # no signal that it's being treated any differently from an ordinary resident hitting the
        # normal cap.
        st.error(
            "Unable to register this villa to your email address. "
            "Please contact Dev via the contact details in Court Maintenance for assistance."
        )
        add_log("Access Denied", f"Email {otp_email_input} exceeded villa cap ({max_villas_allowed}) attempting {otp_sub} Villa {otp_villa}", fingerprint=current_uuid)
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
                    # Shown once on the next screen. Replaces a fixed 1.2s time.sleep() that
                    # only existed so the user could read this message before the rerun.
                    st.session_state.auth_notice = f"✅ Code sent! Please check your inbox at {otp_email_input}"
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
/* Headings inside the tabs ("Court Availability & Booking", "My Bookings", section headings...) at HALF
   of Streamlit's default sizes (h1 2.75rem, h2 2.25rem, h3 1.75rem), keeping their proportions. The page
   title above the tabs is outside the tab panels, so it is unaffected. */
div[role="tabpanel"] h1 { font-size: 1.375rem !important; }
div[role="tabpanel"] h2 { font-size: 1.125rem !important; }
div[role="tabpanel"] h3 { font-size: 0.875rem !important; }
/* Tab labels ("Plan & Book", "My Bookings", "Maint.", "Log", "News") in the same font as the page title.
   Audiowide is a wide font, which is why the labels are kept fairly short. */
[role="tab"], [role="tab"] p, [role="tab"] div { font-family: 'Audiowide', cursive !important; }
/* The "I'm looking for a slot" form reads as a sentence ("on [day] at [time] for [1 hr]"). On phones
   Streamlit stacks every column onto its own line; inside this one named container keep the cells of a row
   side by side, with a tighter gap, so the form is two short rows instead of five. */
.st-key-watch_form [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 0.4rem !important; align-items: center !important; }
.st-key-watch_form [data-testid="stColumn"] { min-width: 0 !important; }
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
    _is_past_now = _past_checker()
    for d in get_next_14_days():
        d_str = d.strftime('%Y-%m-%d')
        st.subheader(f"{d_str} ({d.strftime('%A')})")
        bookings_with_details = _day_map(d_str)
        data = {}
        for h in get_start_hours_for_date(d_str):
            label = f"{h:02d}:00 - {h+1:02d}:00"
            row = []
            slot_past = _is_past_now(d_str, h)      # same answer for every court, so ask once per hour
            for court in courts:
                key = (court, h)
                if slot_past: row.append("—")
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

@st.cache_data(ttl=30, show_spinner=False)
def _get_home_stats_from_queries():
    """Header numbers: (list of villas with active bookings, total active bookings).
    These used to be 4 uncached queries on every script rerun. Cached for 30s across all
    sessions (they're cosmetic) and the queries run concurrently when the cache is cold."""
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    villas, res_f, res_t = run_parallel(
        get_villas_with_active_bookings,
        lambda: run_query(supabase.table("bookings").select("id", count="exact").gt("date", today_str)),
        lambda: run_query(supabase.table("bookings").select("id", count="exact").eq("date", today_str).gte("start_hour", now_hour)),
    )
    if res_f is None or res_t is None:
        raise RuntimeError("stats query failed")  # raising means the failure is NOT cached
    count_f = res_f.count if res_f.count is not None else 0
    count_t = res_t.count if res_t.count is not None else 0
    return villas, count_f + count_t

def get_home_stats():
    """(villas with active bookings, total active bookings) from the shared snapshot, falling
    back to the direct queries above if the snapshot is unavailable."""
    rows, _since = _snapshot_rows()
    if rows is None:
        return _get_home_stats_from_queries()
    active = _upcoming_rows(rows)
    return sorted({f"{r['sub_community']} - {r['villa']}" for r in active}), len(active)

# --- MAIN APP ---
# Log-in state must be settled BEFORE anything is drawn. A browser refresh starts a brand-new session
# (logged out), and the login is then restored from the ?auth= / ?cauth= link in the URL. That restore
# used to happen ~150 lines below the header, in the same run, so a refreshed logged-in page showed the
# login-screen header (tagline, "Serving…", live stats) above the tabs. Restoring first fixes that.
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False
if 'is_coach' not in st.session_state:
    st.session_state.is_coach = False

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

st.subheader("🎾 Book that Court ...")
st.info("The App will stop on 31st Dec 2026.")
# Logged-in users should see the app itself first (tabs right under the title), especially on a
# phone. So the tagline, user count, live stats and "logged in as" line are shown in full on the
# login screen only; once logged in they move to a small footer just above the Logout button
# (see render_availability_tab). The values are computed either way (villas_active is needed later).
_show_full_header = not st.session_state.get("authenticated", False)
if _show_full_header:
    st.caption("An Un-Official & Community Driven Booking Solution.")
_live_user_count = get_live_active_users_count()
_user_count_label = f"{_live_user_count:,}" if _live_user_count else "2,450"
if _show_full_header:
    st.markdown(
        "<p style='color:#ccff00; font-weight:700; margin-top:-8px;'>"
        f"Serving {_user_count_label} active users, the app is community coded and funded."
        "</p>",
        unsafe_allow_html=True,
    )

total_residences = total_bookings = 0
_stats_ok = True
try:
    _process_background_tasks()
    villas_active, total_bookings = get_home_stats()
    total_residences = len(villas_active)

    if _show_full_header:
        st.write(f"**{total_residences}** Residences have **{total_bookings}** active bookings.")
except Exception:
    _stats_ok = False
    if _show_full_header:
        st.write("Unable to load live stats (Network refreshing...)")
    villas_active = []

_DEVICE_ID_JS = """
    (function() {
        let devId = localStorage.getItem('court_device_uuid');
        if (!devId) {
            devId = 'dev_' + Math.floor(Math.random() * 89999999 + 10000000) + '_' + Math.floor(Date.now() / 1000);
            localStorage.setItem('court_device_uuid', devId);
        }
        return devId;
    })();
"""
_UA_CHECK_JS = """
        (function() {
            const ua = navigator.userAgent || '';
            return /Mobi|Android|iPhone|iPad|iPod/i.test(ua) ? 'mobile' : 'desktop';
        })();
    """

# Where the two invisible browser helpers (device id + phone/desktop check) are drawn:
#  * Login screen (not logged in): right here, because their results are needed immediately (the
#    device id is written back to the browser and enforces the max-villas-per-device rule).
#  * Logged in: at the very END of the page (render_deferred_helpers, below the footer). They only
#    keep values in sync there, and drawn up here each one reserved space in the layout: a blank band
#    between the title and the tabs. Their last reported value is read from session_state instead;
#    the components still report on every run they are drawn, exactly as before.
# Decided once per run, so a component can never be drawn twice in the same run.
_helpers_deferred = bool(st.session_state.authenticated)

def render_deferred_helpers():
    """Logged-in pages: draw the invisible helpers at the end of the page (no-op on the login screen)."""
    if not _helpers_deferred:
        return
    st_javascript(_DEVICE_ID_JS, key="js_device_fetch")
    if "is_mobile_device" not in st.session_state:
        st_javascript(_UA_CHECK_JS, key="js_ua_check")

if _helpers_deferred:
    js_device_fetch = st.session_state.get("js_device_fetch")   # last value the browser reported
else:
    js_device_fetch = st_javascript(_DEVICE_ID_JS, key="js_device_fetch")

if isinstance(js_device_fetch, str) and js_device_fetch.startswith("dev_"):
    st.session_state.device_uuid = js_device_fetch
elif "device_uuid" not in st.session_state:
    st.session_state.device_uuid = f"dev_{random.randint(10000000, 99999999)}_{int(time.time())}"

if "is_mobile_device" not in st.session_state:
    if _helpers_deferred:
        ua_check = st.session_state.get("js_ua_check")
    else:
        ua_check = st_javascript(_UA_CHECK_JS, key="js_ua_check")
    # st_javascript returns None/0 on the very first render while it waits for the browser
    # round-trip. Only lock in a result once we actually get "mobile" or "desktop" back —
    # otherwise this was permanently caching is_mobile_device=False before the real value
    # ever arrived, which made every phone fall back to the desktop download button instead
    # of the native share sheet.
    if isinstance(ua_check, str) and ua_check in ("mobile", "desktop"):
        st.session_state.is_mobile_device = (ua_check == "mobile")

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
    st.caption("Secure login for your residence. 1 verified resident email per villa.")
    
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
        _auth_notice = st.session_state.pop("auth_notice", None)
        if _auth_notice:
            st.success(_auth_notice)
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

                                try:
                                    # This app's own villa_claims row + signed URL token (below)
                                    # is the real, ongoing login mechanism — this Supabase Auth
                                    # session was only ever needed for the instant of checking the
                                    # OTP code above. Left open, it sits on the shared `supabase`
                                    # client (cached 30 min, see init_supabase()) and Supabase's
                                    # client library keeps auto-refreshing it in the background —
                                    # a POST to /auth/v1/token every few minutes, for every
                                    # resident who logs in, for a session nothing else here ever
                                    # reads again. Signing out immediately stops that.
                                    supabase.auth.sign_out()
                                except Exception:
                                    pass

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
                if st.button("🔓 Clear Restrictions & Restore Clean Access", type="primary", key="login_rst_btn", width='stretch'):
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
                            f"System cleared restrictions and reset cooldown for {rst_email} across {len(claims)} villas"
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
    st.markdown("""
    <div style="background-color:#0d5384; padding:10px 14px; border-radius:10px; border-left: 5px solid #ccff00; margin-bottom:8px;">
        <div style="font-family:'Audiowide', cursive; color:#ccff00; font-size:1rem; margin:0 0 4px 0;">Power in Numbers</div>
        <p style="font-size:0.82em; line-height:1.35; margin:0 0 4px 0;">
            This hub centralizes every court issue to facilitate <b>mass maintenance requests</b>. By reporting collectively, we ensure
            our concerns are impossible to ignore and prioritized for repair.
        </p>
        <p style="font-style:italic; font-size:0.78em; line-height:1.3; margin:0; border-top:0.5px solid #052134; padding-top:4px;">
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
                    add_log("Maintenance Reported", f"Issue reported for {m_court}. Reported by {reporter_label}.", fingerprint=current_device)
                    refresh_after_maintenance_change()
                    st.success("✅ Maintenance report submitted successfully!")
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to submit report: {str(e)}")

    _pill = ("display:inline-block; background: rgba(255,255,255,0.05); padding: 5px 10px; border-radius: 8px; "
             "border: 1px solid rgba(255,255,255,0.1); font-size: 12px; margin: 2px 4px 2px 0;")
    st.markdown(
        '<div style="margin: 6px 0 2px 0;">'
        '<span style="font-family: \'Audiowide\', cursive; font-size: 12px; margin-right: 8px;">📞 Contact Resources</span>'
        f'<span style="{_pill}">📧 <a href="mailto:support@dubaiholdingcm.ae" style="color: #4CAF50; text-decoration: none;">support@dubaiholdingcm.ae</a></span>'
        f'<span style="{_pill}">💬 <a href="https://wa.me/971562069871" target="_blank" style="color: #4CAF50; text-decoration: none;">+971 56 206 9871</a></span>'
        f'<span style="{_pill}">🌐 <a href="https://dubaiholdingcommunities.ae" target="_blank" style="color: #4CAF50; text-decoration: none;">dubaiholdingcommunities.ae</a></span>'
        '</div>',
        unsafe_allow_html=True,
    )

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

        def _render_issue(item):
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
                            refresh_after_maintenance_change()
                            st.rerun()

        fixed_issues = [item for item in maint_data.data if item.get('is_fixed')]

        st.markdown(f"#### ⚠️ Pending ({len(open_issues)})")
        if open_issues:
            for item in open_issues:
                _render_issue(item)
        else:
            st.caption("No pending issues.")

        st.markdown(f"#### ✅ Fixed ({len(fixed_issues)})")
        if fixed_issues:
            for item in fixed_issues:
                _render_issue(item)
        else:
            st.caption("No fixed issues yet.")
    else:
        st.info("No maintenance issues reported yet.")


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
        if st.button("Create Coach Profile", type="primary", width='stretch'):
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
                    if st.button("🗑️ Delete Selected Villa", key=f"{key_prefix}_delete_villa_btn_{selected_coach_email}", width='stretch'):
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

                if st.button("➕ Add Villa to Coach", type="primary", width='stretch', key=f"{key_prefix}_add_villa_btn_{selected_coach_email}"):
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
                        if st.button("🔑 Reset PIN", key=f"{key_prefix}_reset_pin_{c_email}", width='stretch'):
                            run_query(supabase.table("coach_accounts").update({"pin": None}).eq("email", c_email))
                            add_log("Admin Reset", f"Admin reset PIN for coach {c_email}")
                            st.success(f"PIN cleared for {c.get('coach_name', 'this coach')}. They'll set a new one on next login.")
                            time.sleep(1)
                            st.rerun()
                    with cc2:
                        toggle_label = "⏸️ Deactivate" if c.get("is_active", True) else "▶️ Reactivate"
                        if st.button(toggle_label, key=f"{key_prefix}_toggle_active_{c_email}", width='stretch'):
                            run_query(supabase.table("coach_accounts").update({"is_active": not c.get("is_active", True)}).eq("email", c_email))
                            st.success(f"{c.get('coach_name', 'Coach')} {'deactivated' if c.get('is_active', True) else 'reactivated'}.")
                            time.sleep(1)
                            st.rerun()
                    with cc3:
                        if st.button("🗑️ Delete Coach", key=f"{key_prefix}_delete_coach_{c_email}", width='stretch'):
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

                        if st.button("💾 Save Changes", key=f"{key_prefix}_save_edit_{c_email}", type="primary", width='stretch'):
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

def _villa_line_parts(line):
    """'Mira 4 - Villa 84' / 'Mira 4 Villa 84' / 'Mira 4 - 84' -> ('Mira 4', '84'); None if it isn't that shape."""
    m = re.match(r"^\s*(.+?)\s*[-–—]?\s*(?:villa\s*)?(\d+)\s*$", line or "", flags=re.I)
    return (m.group(1).strip(), m.group(2)) if m else None

def build_sniping_warning_details(warn_villas):
    """Log text for a warning sent by hand, written EXACTLY like the automatic warning
    ('Cross-villa warning triggered for <villa>. Prior activity on: <villas>') so nothing in the
    activity log tells residents which kind it was."""
    parts = [_villa_line_parts(v) for v in warn_villas]
    if not warn_villas:
        return "Cross-villa warning triggered for multiple properties. Prior activity on: multiple properties"
    triggered = f"{parts[0][0]} Villa {parts[0][1]}" if parts[0] else warn_villas[0]
    prior = ", ".join((f"{p[0]} - {p[1]}" if p else v) for v, p in zip(warn_villas[1:], parts[1:]))
    return f"Cross-villa warning triggered for {triggered}. Prior activity on: {prior or 'multiple properties'}"

# Colour of the "event type" cell in the activity log (one entry per event type).
_ACTIVITY_EVENT_STYLES = {
    "Booking Created": 'background-color: #d4edda; color: #155724; font-weight: bold;',
    "Villa Claim": 'background-color: #d4edda; color: #155724; font-weight: bold;',
    "Booking Deleted": 'background-color: #f8d7da; color: #721c24; font-weight: bold;',
    "Booking Cancelled": 'background-color: #f8d7da; color: #721c24; font-weight: bold;',
    "Villa Claim Removed": 'background-color: #f8d7da; color: #721c24; font-weight: bold;',
    "Access Denied": 'background-color: #ffcc00; color: black; font-weight: bold;',
    "Claim Held for Review": 'background-color: #ffcc00; color: black; font-weight: bold;',
    "Sniping Warning": 'background-color: #ffcc00; color: black; font-weight: bold;',
    "Sniping Penalty": 'background-color: #ff4d4d; color: white; font-weight: bold;',
    "Sniping Lockout": 'background-color: #ff4d4d; color: white; font-weight: bold;',
    "Double Booking Detected": 'background-color: #ff4d4d; color: white; font-weight: bold;',
    "Booked but not used": 'background-color: #ff9800; color: black; font-weight: bold;',  # warning orange: distinct from yellow (denied/warning) and red (penalties)
}

def _style_activity_event(val):
    return _ACTIVITY_EVENT_STYLES.get(val, '')

def _activity_logs_content_key(logs):
    """Cheap exact fingerprint of the log rows the table is built from (only the three fields it
    uses), so an unchanged log list reuses the already-built table."""
    return hash(tuple((r.get("timestamp"), r.get("event_type"), r.get("details")) for r in logs))

@st.cache_data(show_spinner=False, max_entries=8)
def _build_activity_log_view(_logs, content_key, is_admin):
    """Filtered / cleaned / formatted activity-log table (timestamp, event_type, details) for the
    admin or the resident view. This used to be rebuilt from scratch — five regex passes over every
    row, date parsing, and row-by-row styling — on EVERY rerun of every logged-in user (all tabs
    render on every rerun, not just the visible one). It is a pure function of the log rows and the
    view, so it is now built once per distinct log list. `_logs` is excluded from Streamlit's
    argument hashing; `content_key` is what identifies it."""
    log_df = pd.DataFrame(_logs, columns=["timestamp", "event_type", "details"])
    filters = (
        (log_df['event_type'] != "Debug") &
        (log_df['event_type'] != "System Maintenance") &
        (log_df['event_type'] != "Auto-Book Ledger") &
        (~log_df['details'].str.contains("System-Synced", case=False, na=False))
    )
    if not is_admin:
        filters &= (log_df['event_type'] != "Limit Enforcement")
        # Hide anything admin-only or coach-related from the resident-facing view entirely — not
        # just "Coach Login", but coach profile admin actions (Admin Edit, PIN resets, deletions),
        # internal broadcast-email bookkeeping (Admin Broadcast), and any booking/cancellation made
        # using a coach's pooled quota. None of this is something a resident needs to see, and it's
        # simpler/safer to hide it outright than to try to phrase it as an automatic action.
        is_admin_only_or_coach_related = (
            (log_df['event_type'] == "Coach Login") |
            (log_df['event_type'] == "Admin Edit") |
            (log_df['event_type'] == "Admin Broadcast") |
            (log_df['details'].str.contains(r'\bcoach\b', case=False, na=False, regex=True))
        )
        filters &= ~is_admin_only_or_coach_related

    display_df = log_df[filters].copy()
    display_df['details'] = display_df['details'].str.replace(r'⟦FP:.*?⟧⟦IP:.*?⟧ ', '', regex=True)
    display_df['details'] = display_df['details'].str.replace(r'\s*⟦SLOT:.*?⟧', '', regex=True)
    display_df['details'] = display_df['details'].str.replace(r'\s*⟦BAN_EMAIL:.*?⟧⟦BAN_VILLAS:.*?⟧', '', regex=True)
    if not is_admin:
        # Who filed a "Booked but not used" report is admin-only — residents still see which
        # villa was reported and the running 30-day count, just not who reported them.
        display_df['details'] = display_df['details'].str.replace(r'\s*Reported by [^.]+\.', '', regex=True)
        # Who a warning was emailed to is kept for the admin only.
        display_df['details'] = display_df['details'].str.replace(r'\s*⟦WARNED:.*?⟧', '', regex=True)
        display_df['details'] = display_df['details'].apply(mask_emails_in_text)

    cols = ['timestamp', 'event_type', 'details']
    display_df['timestamp'] = pd.to_datetime(display_df['timestamp'], format='ISO8601').dt.strftime('%b %d, %H:%M')
    return display_df[cols]

def _render_activity_log_table(is_admin):
    ref_col1, ref_col2 = st.columns([5, 2])
    with ref_col1:
        st.subheader("Community Activity Log")
    with ref_col2:
        st.write("")
        if st.button("🔄 Refresh Logs", key="refresh_logs_btn", width='stretch'):
            get_logs_last_14_days.clear()
            st.rerun()
    st.caption("Timezone: UTC+4")

    logs = get_logs_last_14_days()
    if logs:
        display_df = _build_activity_log_view(logs, _activity_logs_content_key(logs), bool(is_admin))
        # Only the event-type column is coloured, so style just that column cell by cell rather than
        # building a pandas Series for every row (same CSS, a fraction of the work).
        st.dataframe(display_df.style.map(_style_activity_event, subset=['event_type']), hide_index=True, width="stretch")
    else: st.info("No activity.")

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

    admin_pass_val = st.session_state.get("log_admin_pass", "")
    is_admin = admin_pass_val == st.secrets.get("ADMIN_PASSWORD", "admin123")

    _render_activity_log_table(is_admin)

    st.divider()
    st.subheader("🛠️ Admin Tools")
    admin_pass = st.text_input("Admin Password", type="password", key="log_admin_pass")

    if is_admin:
        col_adm1, col_adm2 = st.columns([3, 1])
        with col_adm1: st.success("Admin Access Granted")
        with col_adm2:
            if st.button("🔒 Exit Admin Mode", type="secondary", width='stretch'):
                st.session_state.pop("log_admin_pass", None)
                st.rerun()

        admin_tabs = st.tabs([
            "📊 Overview",
            "👥 Residents & Access",
            "🚫 Security & Lockouts",
            "📅 Bookings & Villas",
            "🎾 Coach Facility",
            "🗄️ Backup & Housekeeping",
            "📧 Broadcast Email",
        ])

        with admin_tabs[0]:
            if is_supporter_mode_enabled():
                st.error("🚦 **Supporter-Only Access Mode is currently ON** — only supporters can access the app. Turn it off in the Security & Lockouts tab once traffic settles.")
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
            st.markdown("### ⚠️ Double-Booking Integrity Check")
            st.caption(
                "Scans the bookings table for the same court + date + hour appearing more than once. "
                "Runs automatically about once an hour in the background and emails "
                "**devkrea@gmail.com** (or `ADMIN_NOTIFY_EMAIL` in secrets) when a *new* conflict appears. "
                "Use the button below to scan now and force a notification."
            )
            if st.button("🔍 Scan for double bookings now", key="admin_scan_double_bookings", width='stretch'):
                with st.spinner("Scanning bookings table…"):
                    dups, newly = check_and_flag_double_bookings(force_notify=True)
                if not dups:
                    st.success("✅ No double bookings found.")
                else:
                    st.error(f"Found **{len(dups)}** conflicting slot(s). Admin notified for {newly} of them.")
                    for d in dups:
                        hour = d.get("start_hour")
                        time_disp = f"{int(hour):02d}:00" if hour is not None else "?"
                        holders = "; ".join(
                            f"{b.get('sub_community')} Villa {b.get('villa')} (id={b.get('id')})"
                            + (f" coach={b.get('coach_email')}" if b.get("coach_email") else "")
                            for b in d["bookings"]
                        )
                        st.warning(
                            f"**{d.get('court')}** · {d.get('date')} · {time_disp} — "
                            f"{d.get('count')} rows: {holders}"
                        )

            st.divider()
            st.caption(
                "**Not finding a tool here?** One admin action lives on the login screen itself because "
                "it has to work BEFORE anyone's signed in: unlocking a resident who's locked out — the "
                "🛠️ Admin Emergency Console at the bottom of the login page. Every other admin tool, "
                "including switching your own session to a resident (👥 Residents & Access → Admin "
                "Resident Bypass), lives right here under this one admin panel."
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
                            max_v_allowed = get_max_villas_for_email(man_email)
                            if curr_c >= MAX_EMAILS_PER_VILLA:
                                st.error(f"Cannot add: {man_sub} Villa {man_villa} already has {MAX_EMAILS_PER_VILLA} verified claim(s) (1 email per villa).")
                            elif email_v_count >= max_v_allowed:
                                greylist_note = " — this email is greylisted" if is_email_greylisted(man_email) else ""
                                st.error(f"Cannot add: {man_email} already holds claims for {max_v_allowed} villa(s) (maximum cap reached{greylist_note}).")
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
                                add_log("Villa Claim", f"System authorized {man_sub} Villa {man_villa} for {man_email}")
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
                            st.write(f"Active Verified Emails for **{claim_inspect_villa}** ({len(claims)} / {MAX_EMAILS_PER_VILLA} allowed):")
                            for claim in claims:
                                c_box1, c_box2 = st.columns([3, 1])
                                with c_box1:
                                    st.info(f"📧 **{claim['email']}**  \n*Status:* `{claim['status']}`")
                                with c_box2:
                                    st.write("")
                                    if st.button(f"🔓 Release Claim", key=f"del_claim_{claim['id']}", type="secondary", width="stretch"):
                                        run_query(supabase.table("villa_claims").delete().eq("id", claim['id']))
                                        add_log("Villa Claim Removed", f"System released claim for {c_sub} Villa {c_villa}")
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
                    lookup_pressed = st.button("Search Account", type="primary", width='stretch')

                if reset_email_input:
                    email_claims = get_all_villas_for_email(reset_email_input)
                    if email_claims:
                        st.markdown(f"**Properties Linked to `{reset_email_input}` ({len(email_claims)} total):**")
                        for c in email_claims:
                            st.info(f"🏡 **{c['sub_community']} - Villa {c['villa']}** | *Verified:* `{c.get('verified_at', 'Unverified')}`")
                        st.write("")
                        col_e1, col_e2 = st.columns(2)
                        with col_e1:
                            if st.button(f"🔓 Reset Cooldown & Restore Clean Access", type="primary", width='stretch', key="email_rst_btn_1"):
                                now_ts = get_utc_plus_4().isoformat()
                                for c in email_claims:
                                    run_query(supabase.table("villa_claims").update({"verified_at": now_ts, "status": "approved"}).eq("id", c["id"]))
                                add_log("Admin Reset", f"System reset cooldown for {reset_email_input}")
                                st.success(f"✅ Successfully cleared lockout for {reset_email_input}!")
                                time.sleep(1.5)
                                st.rerun()
                        with col_e2:
                            if st.button(f"🔄 Reset Ownership (Wrong Villa Mistake)", type="secondary", width='stretch', key="email_rst_btn_2"):
                                for c in email_claims:
                                    run_query(supabase.table("villa_claims").delete().eq("id", c["id"]))
                                add_log("Admin Reset", f"System reset villa claims for {reset_email_input}")
                                st.success(f"🔄 All villa claims deleted for {reset_email_input}!")
                                time.sleep(1.5)
                                st.rerun()
                    else:
                        st.warning(f"No active property claims found for `{reset_email_input}`.")

            with st.expander("🔑 Admin Resident Bypass (Authorize & Switch Active Resident)", expanded=False):
                st.caption(
                    "Directly authorize a resident email without OTP and immediately switch THIS admin "
                    "session to them — moved here from the Maint. tab so it sits under the same single "
                    "admin password as every other admin tool, instead of its own separate prompt."
                )
                b_col1, b_col2 = st.columns(2)
                with b_col1:
                    bypass_sub = st.selectbox("Sub-Community", options=sub_community_list, key="admres_bypass_sub")
                with b_col2:
                    bypass_villa_raw = st.text_input("Villa Number", key="admres_bypass_villa").strip()
                    bypass_villa = "".join(filter(str.isdigit, bypass_villa_raw))
                bypass_email = st.text_input("Resident Email Address", placeholder="resident@example.com", key="admres_bypass_email").strip().lower()

                if st.button("Authorize & Switch Session to Resident", type="primary", width='stretch', key="admres_bypass_btn"):
                    max_allowed_bypass = SUB_COMMUNITY_VILLA_LIMITS.get(bypass_sub, 9999)
                    if not bypass_sub or not bypass_villa or not bypass_email or "@" not in bypass_email:
                        st.error("Please specify a valid Sub-Community, Villa, and Email Address.")
                    elif not bypass_villa.isdigit() or not (1 <= int(bypass_villa) <= max_allowed_bypass):
                        st.error(f"Invalid villa number for {bypass_sub}. Must be between 1 and {max_allowed_bypass}.")
                    elif (
                        not get_existing_claim(bypass_sub, bypass_villa, bypass_email)
                        and is_email_greylisted(bypass_email)
                        and get_email_claimed_villas_count(bypass_email) >= GREYLIST_MAX_VILLAS_PER_EMAIL
                    ):
                        # This tool otherwise bypasses every other cap by design (that's its whole
                        # purpose) — the greylist is the one restriction that still applies here,
                        # since letting a known abuser get a 2nd villa through the admin's own
                        # convenience tool would defeat the point of greylisting them at all.
                        st.error(
                            f"Cannot grant a new villa to {bypass_email} — this email is greylisted and "
                            f"already holds its maximum of {GREYLIST_MAX_VILLAS_PER_EMAIL} villa. "
                            "Remove it from the greylist first (Security & Lockouts tab) if this is a genuine exception."
                        )
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
                            add_log("Villa Claim", f"System authorized {bypass_sub} Villa {bypass_villa} for {bypass_email}")
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

        with admin_tabs[2]:
            _supporter_on = is_supporter_mode_enabled()
            with st.expander("🚦 Supporter-Only Access Mode (emergency high-traffic control)", expanded=_supporter_on):
                if _supporter_on:
                    st.error("🔴 **ON** — only supporter emails can access the app right now. Everyone else sees a high-traffic message.")
                else:
                    st.success("🟢 OFF — the app is open to everyone as normal.")
                st.caption(
                    "For genuinely high-traffic moments: when ON, only emails on the supporter list below can "
                    "log in or keep using the app (checked on every page load, not just at login — anyone already "
                    "using the app who isn't a supporter is cut off on their next interaction too). Everyone else "
                    "sees a plain 'we're experiencing high traffic' message — no mention of supporters or this "
                    "toggle anywhere they can see. A small password-protected override on that screen means this "
                    "can never turn into a real lockout, even if your own email isn't on the list yet."
                )
                _sup_toggle_label = "🔴 Turn OFF Supporter-Only Mode" if _supporter_on else "🟢 Turn ON Supporter-Only Mode"
                if st.button(_sup_toggle_label, type="primary", key="supporter_mode_toggle_btn"):
                    if set_supporter_mode_enabled(not _supporter_on):
                        add_log("Admin Edit", f"Admin turned Supporter-Only Access Mode {'ON' if not _supporter_on else 'OFF'}")
                        st.rerun()
                    else:
                        st.error("Could not update the setting — the `app_settings` table may not exist yet (see below).")

                st.divider()
                st.markdown("**Supporter list**")
                _sup_entries = get_supporter_entries()
                if _sup_entries is None:
                    st.warning(
                        "This feature needs `app_settings` and `supporters` tables that don't exist yet. Create "
                        "them in Supabase and this panel switches on automatically:"
                    )
                    st.code(
                        "create table app_settings (\n"
                        "  key text primary key,\n"
                        "  value text,\n"
                        "  updated_at timestamptz default now()\n"
                        ");\n\n"
                        "create table supporters (\n"
                        "  id bigint generated always as identity primary key,\n"
                        "  email text not null unique,\n"
                        "  note text,\n"
                        "  added_at timestamptz default now()\n"
                        ");",
                        language="sql",
                    )
                else:
                    if _sup_entries:
                        st.caption(f"{len(_sup_entries)} supporter(s)")
                        for _se in _sup_entries:
                            _se_note = f" — *{_se['note']}*" if _se.get("note") else ""
                            _sup_col1, _sup_col2 = st.columns([6, 1])
                            with _sup_col1:
                                st.caption(f"⭐ **{_se['email']}**{_se_note}")
                            with _sup_col2:
                                if st.button("🗑️", key=f"sup_remove_{_se['id']}", help="Remove supporter"):
                                    remove_supporter(_se["email"])
                                    add_log("Admin Edit", f"Admin removed supporter {_se['email']}")
                                    st.rerun()
                        st.divider()
                    else:
                        st.caption("No supporters added yet — turning the toggle ON right now would lock everyone out.")

                    _sup_new_email = st.text_input("Email address", placeholder="supporter@example.com", key="sup_new_email").strip().lower()
                    _sup_new_note = st.text_input("Note (admin-only, optional)", key="sup_new_note")
                    if st.button("⭐ Add Supporter", type="secondary", key="sup_add_btn", disabled=not ("@" in _sup_new_email and "." in _sup_new_email)):
                        if _sup_new_email in get_supporter_emails():
                            st.info("Supporter is already added to the list.")
                        elif add_supporter(_sup_new_email, _sup_new_note):
                            add_log("Admin Edit", f"Admin added supporter {_sup_new_email}" + (f" ({_sup_new_note})" if _sup_new_note else ""))
                            st.success(f"{_sup_new_email} added to the supporter list.")
                            time.sleep(1.0)
                            st.rerun()
                        else:
                            st.error("Could not add supporter — please try again.")

                    with st.expander("➕ Bulk add (paste a list)"):
                        _sup_bulk_text = st.text_area(
                            "Emails — comma or newline separated",
                            key="sup_bulk_text", height=120,
                            placeholder="alice@example.com, bob@example.com\ncarol@example.com",
                        )
                        if st.button("⭐ Add All", type="secondary", key="sup_bulk_add_btn", disabled=not _sup_bulk_text.strip()):
                            _raw_emails = [e.strip().lower() for e in re.split(r"[,\n]", _sup_bulk_text) if e.strip()]
                            _existing_sup = get_supporter_emails()
                            _added, _skip_existing, _skip_invalid = [], [], []
                            _seen_this_batch = set()
                            for _e in _raw_emails:
                                _domain = _e.split("@")[-1] if "@" in _e else ""
                                if "@" not in _e or "." not in _domain:
                                    _skip_invalid.append(_e)
                                elif _e in _existing_sup or _e in _seen_this_batch:
                                    _skip_existing.append(_e)
                                elif add_supporter(_e):
                                    _added.append(_e)
                                    _seen_this_batch.add(_e)
                                else:
                                    _skip_invalid.append(_e)
                            if _added:
                                add_log("Admin Edit", f"Admin bulk-added {len(_added)} supporter(s)")
                            _bulk_summary = f"Added {len(_added)}."
                            if _skip_existing:
                                _bulk_summary += f" {len(_skip_existing)} already on the list (skipped)."
                            if _skip_invalid:
                                _bulk_summary += f" {len(_skip_invalid)} invalid (skipped)."
                            st.success(_bulk_summary)
                            time.sleep(1.2)
                            st.rerun()

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
                        if st.button("🚫 Apply Sniping Lockout to Email & Associated Villas", type="primary", width='stretch', key="apply_admin_lockout_btn"):
                            villas_str = ", ".join(villa_descriptions)
                            villa_pairs = [(c['sub_community'], c['villa']) for c in associated_claims]
                            ban_tag = build_ban_tag(lockout_email_input, villa_pairs)
                            log_msg = f"4-day penalty active for email {lockout_email_input} across properties: {villas_str} {ban_tag}"
                            add_log("Sniping Penalty", log_msg)
                            st.success(f"✅ Sniping lockout successfully applied and logged for `{lockout_email_input}` and associated properties ({villas_str}).")
                            time.sleep(1.5)
                            st.rerun()
                    else:
                        st.warning(f"No villas found associated with email `{lockout_email_input}`.")

                st.divider()
                st.markdown("### Send a Manual Sniping Warning (No Lockout)")
                st.caption(
                    "Use this to warn residents about suspected multi-email / multi-villa "
                    "sniping without applying a lockout yet — useful as a first, softer step. "
                    "This sends a real email to each address listed and logs a public "
                    "'Sniping Warning' entry, but does not block their access."
                )
                warn_emails_raw = st.text_area(
                    "Email address(es) to warn (one per line)",
                    placeholder="resident1@example.com\nresident2@example.com",
                    key="admin_warn_emails_input",
                )
                warn_villas_raw = st.text_area(
                    "Villa(s) involved (one per line, e.g. 'Mira 4 - Villa 84')",
                    placeholder="Mira 4 - Villa 84\nMira 1 - Villa 229",
                    key="admin_warn_villas_input",
                )
                warn_emails = [e.strip().lower() for e in warn_emails_raw.splitlines() if e.strip() and "@" in e]
                warn_villas = [v.strip() for v in warn_villas_raw.splitlines() if v.strip()]

                if st.button("📨 Send Sniping Warning", type="primary", width='stretch', key="admin_send_sniping_warning_btn"):
                    if not warn_emails:
                        st.error("Please enter at least one valid email address.")
                    else:
                        villas_str = ", ".join(warn_villas) if warn_villas else "multiple properties"
                        for w_email in warn_emails:
                            subject = "⚠️ Fair-Use Warning — Unusual Booking Activity Detected"
                            html_content = f"""
                            <html><body style="font-family: Arial, sans-serif; color: #222;">
                            <h3>Fair-Use Warning</h3>
                            <p>Our system has detected activity suggesting possible manipulation of the
                            court booking system using multiple email addresses and/or villas, including:</p>
                            <p><b>{villas_str}</b></p>
                            <p>This kind of activity — such as booking and cancelling repeatedly, or moving
                            between different villa registrations to gain extra bookings — is considered
                            <b>court-booking sniping</b> and is against our fair-use policy.</p>
                            <p>This is a warning only — no lockout has been applied. However, if this pattern
                            continues, your access to the booking system may be <b>temporarily suspended</b>.</p>
                            </body></html>
                            """
                            send_gmail_smtp(w_email, subject, html_content)
                        # Written exactly like the automatic warning so residents can't tell them apart. The
                        # addresses are kept in a hidden tag (shown to admins only) as your record of who was warned.
                        add_log(
                            "Sniping Warning",
                            f"{build_sniping_warning_details(warn_villas)} ⟦WARNED:{','.join(warn_emails)}⟧",
                        )
                        st.success(f"✅ Warning email sent to {len(warn_emails)} address(es) and logged.")
                        time.sleep(1.5)
                        st.rerun()

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
                                if st.button(f"🔓 Clear Restrictions & Wipe Clean", type="primary", width='stretch', key=f"clear_rest_{idx}"):
                                    now_ts = get_utc_plus_4().isoformat()
                                    if target["claims"]:
                                        for c in target["claims"]:
                                            run_query(supabase.table("villa_claims").update({"verified_at": now_ts, "status": "approved"}).eq("id", c["id"]))
                                    add_log("Admin Reset", f"System cleared restrictions for {target['email']}", fingerprint=target["fingerprint"])
                                    st.success(f"🎉 Restrictions cleared for {target['email']}.")
                                    time.sleep(1.5)
                                    st.rerun()
                            with col_r2:
                                if st.button(f"🔄 Reset Ownership (Wrong Villa Mistake)", type="secondary", width='stretch', key=f"wrong_villa_rst_{idx}"):
                                    if target["claims"]:
                                        for c in target["claims"]:
                                            run_query(supabase.table("villa_claims").delete().eq("id", c["id"]))
                                    add_log("Admin Reset", f"System reset villa ownership claims for {target['email']}", fingerprint=target["fingerprint"])
                                    st.success(f"🔄 Villa ownership claims deleted for {target['email']}.")
                                    time.sleep(1.5)
                                    st.rerun()

            with st.expander("🚫 Greylist (Known Abusers — Max 1 Villa)", expanded=False):
                st.caption(
                    "Emails on this list are capped at **1 villa** instead of the normal 3 — once one of "
                    "these emails holds a villa, it cannot register another, anywhere a villa claim gets "
                    "created (self-service registration, the manual-authorize tool, and the admin resident-"
                    "bypass tool). Their existing claim keeps working as normal — this only blocks adding "
                    "a second one. The person sees the same generic 'contact Dev' message anyone hitting "
                    "the normal cap would see, so nothing reveals they're specifically flagged."
                )
                _grey_entries = get_greylist_entries()
                if _grey_entries is None:
                    st.warning(
                        "This feature needs a `greylisted_emails` table that doesn't exist yet. Create it "
                        "in Supabase and this panel switches on automatically:"
                    )
                    st.code(
                        "create table greylisted_emails (\n"
                        "  id bigint generated always as identity primary key,\n"
                        "  email text not null unique,\n"
                        "  reason text,\n"
                        "  added_at timestamptz default now()\n"
                        ");",
                        language="sql",
                    )
                else:
                    if _grey_entries:
                        st.markdown(f"**Currently greylisted ({len(_grey_entries)})**")
                        for _ge in _grey_entries:
                            _ge_villas = get_email_claimed_villas_count(_ge["email"])
                            _gcol1, _gcol2 = st.columns([6, 1])
                            with _gcol1:
                                _reason_txt = f" — *{_ge['reason']}*" if _ge.get("reason") else ""
                                st.caption(f"🚫 **{_ge['email']}** ({_ge_villas}/{GREYLIST_MAX_VILLAS_PER_EMAIL} villa){_reason_txt}")
                            with _gcol2:
                                if st.button("🔓", key=f"grey_remove_{_ge['id']}", help="Remove from greylist"):
                                    remove_from_greylist(_ge["email"])
                                    add_log("Admin Edit", f"Admin removed {_ge['email']} from the greylist")
                                    st.rerun()
                        st.divider()

                    st.markdown("**Add an email to the greylist**")
                    _grey_new_email = st.text_input("Email address", placeholder="resident@example.com", key="grey_new_email").strip().lower()
                    _grey_new_reason = st.text_input("Reason (admin-only note, optional)", key="grey_new_reason")
                    if st.button("🚫 Add to Greylist", type="primary", key="grey_add_btn", disabled=not ("@" in _grey_new_email and "." in _grey_new_email)):
                        if add_to_greylist(_grey_new_email, _grey_new_reason):
                            add_log("Admin Edit", f"Admin greylisted {_grey_new_email}" + (f" ({_grey_new_reason})" if _grey_new_reason else ""))
                            st.success(f"{_grey_new_email} is now greylisted (capped at {GREYLIST_MAX_VILLAS_PER_EMAIL} villa).")
                            time.sleep(1.0)
                            st.rerun()
                        else:
                            st.error("Could not add to the greylist — please try again.")

        with admin_tabs[3]:
            with st.expander("🕘 Booking Window Release Time", expanded=False):
                _cur_release_hour = get_booking_window_release_hour()
                st.caption(
                    "Each day, the rolling 15-day booking window advances by one day — the newest, "
                    "farthest-out date becomes bookable — at this clock time (app time, UTC+4). "
                    "Pick 00:00 (12 AM) for the new day to release at the natural midnight rollover "
                    "with no early release at all; any other hour releases it that many hours "
                    "earlier that same day. This affects Plan & Book, the Tournament tool's date "
                    "range, and every other date picker in the app — they all read this one setting."
                )
                _release_hour_options = list(range(24))
                def _format_release_hour(h):
                    if h == 0:
                        return "12:00 AM (midnight — natural rollover, no early release)"
                    period = "AM" if h < 12 else "PM"
                    display_h = h if 1 <= h <= 12 else (h - 12 if h > 12 else 12)
                    return f"{display_h}:00 {period}"
                _new_release_hour = st.selectbox(
                    "Release time",
                    options=_release_hour_options,
                    index=_cur_release_hour,
                    format_func=_format_release_hour,
                    key="release_hour_select",
                )
                if st.button("💾 Save Release Time", type="primary", key="release_hour_save_btn", disabled=(_new_release_hour == _cur_release_hour)):
                    if set_booking_window_release_hour(_new_release_hour):
                        add_log("Admin Edit", f"Admin changed booking window release time from {_format_release_hour(_cur_release_hour)} to {_format_release_hour(_new_release_hour)}")
                        st.success(f"Release time updated to {_format_release_hour(_new_release_hour)}.")
                        time.sleep(1.0)
                        st.rerun()
                    else:
                        st.error("Could not save — the `app_settings` table may not exist yet. See the Supporter-Only Access Mode panel in Security & Lockouts for the table it also needs; run that same table's SQL first.")

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
                    "The concealed Legends of Mira auto-booking feature (Mira 1 Villas 229/231/249) "
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
                    if st.button(f"🔓 Re-open {_ledger_pick} for auto-booking", type="primary", width='stretch', key="admin_reopen_ledger_btn"):
                        from database_cleanup import clear_auto_book_ledger_date
                        if clear_auto_book_ledger_date(supabase, _ledger_pick):
                            st.success(f"Cleared — {_ledger_pick} will be reconsidered by the auto-book feature on its next run.")
                        else:
                            st.error("Could not clear the ledger entry — please try again.")
                        time.sleep(1.2)
                        st.rerun()

            with st.expander("🎾 Legends of Mira — Manual Booking (229 / 231 / 249)", expanded=False):
                st.caption(
                    "Admin-only manual booking for the 3 special Mira 1 villas used by the "
                    "concealed auto-booking feature. If a villa is full, delete a booking for it "
                    "above first, then come back here once it has room. This is never shown to "
                    "regular users — the resulting booking just looks like an ordinary booking "
                    "made by that villa, same as the auto-booked ones."
                )
                _special_villas = [("229", "Mira 1"), ("231", "Mira 1"), ("249", "Mira 1")]
                _villa_capacity = {}
                _cap_cols = st.columns(3)
                for _i, (_v, _sc) in enumerate(_special_villas):
                    _cnt = get_active_bookings_count(_v, _sc)
                    _lim = get_active_booking_limit(_sc, _v)
                    _villa_capacity[_v] = (_cnt, _lim)
                    with _cap_cols[_i]:
                        st.metric(f"Villa {_v}", f"{_cnt} / {_lim}")

                _villa_labels = [f"Villa {v} ({sc}) — {_villa_capacity[v][0]}/{_villa_capacity[v][1]} active" for v, sc in _special_villas]
                _villa_pick_label = st.selectbox("Villa to book under", options=_villa_labels, key="admin_special_manual_villa")
                _pick_villa, _pick_sub = _special_villas[_villa_labels.index(_villa_pick_label)]

                _mdate_options = [f"{d.strftime('%Y-%m-%d')} ({d.strftime('%A')})" for d in get_next_14_days()]
                _mdate_choice = st.selectbox("Date:", _mdate_options, key="admin_special_manual_date").split(" (")[0]
                _mcourt_choice = st.selectbox("Court:", courts, key="admin_special_manual_court")

                _mbooked = run_query(supabase.table("bookings").select("start_hour").eq("court", _mcourt_choice).eq("date", _mdate_choice))
                _mbooked_hours = [r['start_hour'] for r in _mbooked.data] if _mbooked and _mbooked.data else []
                _mfree_hours = [h for h in get_start_hours_for_date(_mdate_choice) if h not in _mbooked_hours and not is_slot_in_past(_mdate_choice, h)]

                if not _mfree_hours:
                    st.warning(f"No free slots on {_mcourt_choice} for {_mdate_choice}.")
                else:
                    _mtime_choice = st.selectbox("Time Slot:", [f"{h:02d}:00 - {h+1:02d}:00" for h in _mfree_hours], key="admin_special_manual_time")
                    _mstart_h = int(_mtime_choice.split(":")[0])
                    _mslots_2h = st.checkbox("Book for 2 hours", key="admin_special_manual_2h", disabled=(_mstart_h + 1 not in _mfree_hours))
                    _mhours_needed = 2 if _mslots_2h else 1

                    _mlimit_for_date = get_active_booking_limit(_pick_sub, _pick_villa, for_date=_mdate_choice)
                    _mcurrent_count = _villa_capacity[_pick_villa][0]
                    if _mcurrent_count + _mhours_needed > _mlimit_for_date:
                        st.error(f"Villa {_pick_villa} would exceed its {_mlimit_for_date}-active-booking limit for that date — pick a different villa, date, or delete a booking first.")
                    else:
                        if st.button("📌 Create Manual Booking", type="primary", width='stretch', key="admin_special_manual_book_btn"):
                            _mhours_to_book = [_mstart_h, _mstart_h + 1] if _mslots_2h else [_mstart_h]
                            _minserted_hours, _mall_ok = [], True
                            for _h in _mhours_to_book:
                                if book_slot(_pick_villa, _pick_sub, _mcourt_choice, _mdate_choice, _h, fingerprint=current_device):
                                    _minserted_hours.append(_h)
                                else:
                                    _mall_ok = False
                                    break
                            if not _mall_ok and _minserted_hours:
                                _mrollback = run_query(
                                    supabase.table("bookings").select("id")
                                    .eq("villa", _pick_villa).eq("sub_community", _pick_sub)
                                    .eq("court", _mcourt_choice).eq("date", _mdate_choice)
                                    .in_("start_hour", _minserted_hours)
                                )
                                for _r in (_mrollback.data if _mrollback and _mrollback.data else []):
                                    delete_booking(_r["id"], _pick_villa, _pick_sub, fingerprint=current_device)
                            if _mall_ok:
                                add_log(
                                    "Admin Edit",
                                    f"Admin manually booked {_mcourt_choice} on {_mdate_choice} at {_mtime_choice} "
                                    f"for {_pick_sub} Villa {_pick_villa} (Legends of Mira group)"
                                )
                                st.success(f"Booked {_mcourt_choice} on {_mdate_choice} at {_mtime_choice} for Villa {_pick_villa}.")
                                time.sleep(1.2)
                                st.rerun()
                            else:
                                st.error("One of those slots was just taken by someone else — please pick another time.")

            with st.expander("🏆 Tournament Bulk Booking (auto-books once the day opens)", expanded=False):
                st.caption(
                    "Plan a tournament day up to a few weeks out. Pick the courts, the hour range, and a "
                    "pool of villas with room to spare — the app books each slot automatically, cycling "
                    "through the pool, the moment that date enters the normal 14-day booking window (it "
                    "never books a date before residents themselves could). Every slot is created exactly "
                    "like a normal resident booking — same limits, same confirmation email — and nothing "
                    "admin- or tournament-related is ever written to the activity log residents can see. "
                    "**Safeguard:** if a pool villa has used up all of its active slots by the time it's "
                    "needed for the tournament, its 2 farthest-out existing bookings are cancelled "
                    "automatically to make room — using the exact same cancellation email a resident "
                    "gets from cancelling it themselves, with no mention of a tournament anywhere."
                )
                _trn_requests = get_tournament_requests()
                if _trn_requests is None:
                    st.warning(
                        "This feature needs a `tournament_requests` table that doesn't exist yet. Create it "
                        "in Supabase and this panel switches on automatically:"
                    )
                    st.code(
                        "create table tournament_requests (\n"
                        "  id bigint generated always as identity primary key,\n"
                        "  created_at timestamptz default now(),\n"
                        "  target_date text not null,\n"
                        "  courts text not null,\n"
                        "  villa_pool text not null,\n"
                        "  start_hour int not null,\n"
                        "  end_hour int not null,\n"
                        "  status text default 'pending',\n"
                        "  result_summary text,\n"
                        "  processed_at timestamptz\n"
                        ");",
                        language="sql",
                    )
                else:
                    if _trn_requests:
                        st.markdown("**Existing requests**")
                        for _treq in _trn_requests:
                            _t_icon = "✅" if _treq.get("status") == "done" else "⏳"
                            try:
                                _t_courts = ", ".join(json.loads(_treq["courts"]))
                                _t_pool = ", ".join(json.loads(_treq["villa_pool"]))
                            except Exception:
                                _t_courts, _t_pool = _treq.get("courts", ""), _treq.get("villa_pool", "")
                            _t_col1, _t_col2 = st.columns([6, 1])
                            with _t_col1:
                                _t_status_line = _treq.get("result_summary") or "Waiting for this date to enter the 14-day booking window."
                                st.caption(
                                    f"{_t_icon} **{_treq['target_date']}** · {_t_courts} · "
                                    f"{_treq['start_hour']:02d}:00–{_treq['end_hour']:02d}:00 · pool: {_t_pool}  \n"
                                    f"_{_t_status_line}_"
                                )
                            with _t_col2:
                                if _treq.get("status") == "pending" and st.button("🗑️", key=f"trn_del_{_treq['id']}", help="Cancel this request"):
                                    delete_tournament_request(_treq["id"])
                                    st.rerun()
                        st.divider()

                    st.markdown("**New tournament request**")
                    _trn_date = st.date_input(
                        "Tournament date",
                        value=get_window_today() + timedelta(days=1),
                        min_value=get_window_today(),
                        max_value=get_window_today() + timedelta(days=18),
                        key="trn_date",
                    )
                    _trn_date_str = _trn_date.strftime("%Y-%m-%d")
                    _trn_courts = st.multiselect("Courts", options=courts, key="trn_courts")

                    _trn_hc1, _trn_hc2 = st.columns(2)
                    _trn_hour_opts = list(range(6, 24))
                    with _trn_hc1:
                        _trn_start = st.selectbox("Start time", options=_trn_hour_opts[:-1], format_func=lambda h: f"{h:02d}:00", key="trn_start")
                    with _trn_hc2:
                        _trn_end = st.selectbox("End time", options=_trn_hour_opts[1:], format_func=lambda h: f"{h:02d}:00", index=len(_trn_hour_opts) - 2, key="trn_end")

                    _trn_pool_options = get_villas_with_free_quota(_trn_date_str)
                    if not _trn_pool_options:
                        st.info("No villas currently show free quota for that date — free some up or pick a different date.")
                    _trn_pool = st.multiselect(
                        "Villa pool (only villas with free quota right now are listed)",
                        options=_trn_pool_options, key="trn_pool",
                    )

                    _trn_ready = bool(_trn_courts) and bool(_trn_pool) and _trn_end > _trn_start
                    if st.button("📌 Create Tournament Request", type="primary", width='stretch', key="trn_create_btn", disabled=not _trn_ready):
                        if create_tournament_request(_trn_date_str, _trn_courts, _trn_pool, _trn_start, _trn_end):
                            st.success(f"Tournament request saved for {_trn_date_str}.")
                            if _trn_date_str in {d.strftime('%Y-%m-%d') for d in get_next_14_days()}:
                                _trn_fresh = get_tournament_requests() or []
                                _trn_new = next((r for r in _trn_fresh if r["target_date"] == _trn_date_str and r["status"] == "pending"), None)
                                if _trn_new:
                                    with st.spinner("This date is already within the booking window — booking now…"):
                                        _trn_summary = process_tournament_request(_trn_new)
                                    st.info(_trn_summary)
                            time.sleep(1.5)
                            st.rerun()
                        else:
                            st.error("Could not save the tournament request — please try again.")

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
                        if st.button(f"↩️ Transfer {pending_count} coach booking(s) to villa ownership", type="primary", width='stretch'):
                            run_query(supabase.table("bookings").update({"coach_email": None}).not_.is_("coach_email", "null"))
                            invalidate_booking_caches()
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
                if old_logs_count > 0 and st.button(f"🗑️ Purge {old_logs_count} log entr{'y' if old_logs_count == 1 else 'ies'} now", width='stretch'):
                    purge_old_logs(days=90)
                    st.success("Old log entries purged.")
                    time.sleep(1)
                    st.rerun()

            with st.expander("📊 Resource Monitor (Streamlit Cloud)", expanded=False):
                st.caption(
                    "Reads this app's own CPU/memory footprint with `psutil` and compares it against "
                    "Streamlit Community Cloud's documented per-app ceilings (2 CPU cores, 2.7GB memory) — "
                    "Streamlit doesn't publish a usage API, so this is the closest an app can check on "
                    "itself, and Streamlit can change these limits without notice. CPU is measured since "
                    "the app's *last* check rather than an instant snapshot, so it reads 0% right after a "
                    f"restart until some time has passed. If CPU or memory reaches "
                    f"{RESOURCE_ALERT_THRESHOLD_PCT}% of its limit, a warning email is sent automatically "
                    f"to {RESOURCE_ALERT_RECIPIENT} (checked at most once every 30 minutes). Disk is shown "
                    "for information only, without a percentage — psutil can only see the shared host "
                    "filesystem here, not this app's own 50GB slice of it, so a 'percent of limit' figure "
                    "for disk would be meaningless (in practice it read 100%+ even on a near-empty app)."
                )
                _res_usage = get_resource_usage()
                if _res_usage is None:
                    st.warning("`psutil` isn't installed in this environment — add `psutil` to `requirements.txt` and redeploy to enable this monitor.")
                else:
                    _r1, _r2, _r3 = st.columns(3)
                    with _r1:
                        st.metric("CPU", f"{_res_usage['cores_used']:.2f} / {STREAMLIT_CLOUD_CPU_CORES_MAX:.0f} cores",
                                  f"{_res_usage['cpu_pct_of_limit']}% of limit", delta_color="off")
                    with _r2:
                        st.metric("Memory", f"{_res_usage['mem_mb']:.0f} / {STREAMLIT_CLOUD_MEMORY_MB_MAX:.0f} MB",
                                  f"{_res_usage['mem_pct_of_limit']}% of limit", delta_color="off")
                    with _r3:
                        st.metric("Disk (host, informational)", f"{_res_usage['disk_used_gb']:.1f} / {_res_usage['disk_total_gb']:.1f} GB")

                    _worst_pct = max(_res_usage['cpu_pct_of_limit'], _res_usage['mem_pct_of_limit'])
                    if _worst_pct >= RESOURCE_ALERT_THRESHOLD_PCT:
                        st.error(f"⚠️ CPU or memory is at or above the {RESOURCE_ALERT_THRESHOLD_PCT}% alert threshold — a warning email goes out automatically (at most once every 30 minutes).")
                    elif _worst_pct >= RESOURCE_ALERT_THRESHOLD_PCT - 15:
                        st.warning("Getting close to the alert threshold — worth keeping an eye on.")
                    else:
                        st.success("CPU and memory are comfortably within Streamlit Cloud's documented limits.")

                    if st.button("📧 Send a test resource-usage email now", key="res_test_email_btn"):
                        _send_resource_alert_email(_res_usage, ["Manual test"])
                        st.success(f"Test email sent to {RESOURCE_ALERT_RECIPIENT}.")

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

                BACKUP_TABLES = ["bookings", "logs", "villa_claims", "coach_accounts", "coach_villas", "court_maintenance", "slot_watches","tournament_requests"]

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

        with admin_tabs[6]:
            st.markdown("### 📧 Broadcast Email to Residents")
            st.caption(
                "Send a one-off announcement to residents who currently have active bookings.\n\n"
                "**Privacy rules:**\n"
                "- **No bookings attached** + multiple recipients → one email with "
                "To: `miracourtbooking@gmail.com` and all addresses in BCC (hidden from each other).\n"
                "- **Bookings attached** → emails are sent **individually**. Each person only "
                "receives *their own* active bookings (same content as “Email Me All My Bookings” "
                "in My Bookings), and To: is their own address.\n\n"
                "Fields below are inside a form — typing does **not** re-query the database. "
                "Recipient list is loaded once; use **Refresh recipient list** if bookings changed."
            )

            # Load recipient list once into session state (not on every keystroke).
            # A dedicated refresh button forces a reload when the admin wants an update.
            if "broadcast_active_emails" not in st.session_state:
                with st.spinner("Loading residents with active bookings…"):
                    try:
                        st.session_state.broadcast_active_emails = get_emails_with_active_bookings()
                        st.session_state.broadcast_emails_error = None
                    except Exception as _e:
                        st.session_state.broadcast_active_emails = []
                        st.session_state.broadcast_emails_error = str(_e)

            col_info, col_refresh = st.columns([4, 1])
            with col_refresh:
                if st.button("🔄 Refresh list", key="broadcast_refresh_emails", width='stretch'):
                    with st.spinner("Refreshing…"):
                        try:
                            st.session_state.broadcast_active_emails = get_emails_with_active_bookings()
                            st.session_state.broadcast_emails_error = None
                        except Exception as _e:
                            st.session_state.broadcast_active_emails = []
                            st.session_state.broadcast_emails_error = str(_e)
                    st.rerun()

            active_emails = st.session_state.get("broadcast_active_emails") or []
            if st.session_state.get("broadcast_emails_error"):
                st.error(f"Could not load recipient list: {st.session_state.broadcast_emails_error}")
            with col_info:
                if active_emails:
                    st.info(f"**{len(active_emails)}** unique resident email(s) with active bookings (cached).")
                else:
                    st.warning(
                        "No resident emails found for villas with **active** bookings right now. "
                        "Compose is still available; recipients will stay empty until someone has an active booking."
                    )

            # st.form batches all inputs — script only re-runs (and validates/sends) on submit.
            with st.form("broadcast_email_form", clear_on_submit=False):
                sel_mode = st.radio(
                    "Recipients",
                    options=["Select specific emails", "All users with active bookings"],
                    horizontal=True,
                    key="broadcast_recipient_mode",
                )

                if not active_emails:
                    st.caption("No emails available to select.")
                    selected_in_form = []
                elif sel_mode == "All users with active bookings":
                    selected_in_form = list(active_emails)
                    st.caption(f"Will target all **{len(selected_in_form)}** cached addresses.")
                    with st.expander(f"Preview all {len(selected_in_form)} recipient(s)"):
                        st.code("\n".join(selected_in_form))
                else:
                    selected_in_form = st.multiselect(
                        "Choose one or more recipient emails",
                        options=active_emails,
                        default=[],
                        key="broadcast_email_multiselect",
                        placeholder="Type to filter emails…",
                    )

                broadcast_subject = st.text_input(
                    "Email subject",
                    value="",
                    key="broadcast_subject",
                    placeholder="e.g. Court maintenance this Sunday",
                )
                broadcast_body = st.text_area(
                    "Email body (plain text — line breaks are preserved)",
                    value="",
                    height=180,
                    key="broadcast_body",
                    placeholder="Write your message here…",
                )

                attach_bookings = st.radio(
                    "Attach each recipient's own active bookings?",
                    options=[
                        "No — plain announcement only",
                        "Yes — include their personal bookings summary + calendar file (sent individually)",
                    ],
                    key="broadcast_attach_radio",
                    help=(
                        "When enabled, each recipient gets a separate email containing only the "
                        "bookings for villas registered to their email — never anyone else's."
                    ),
                )

                dry_run = st.checkbox(
                    "🧪 Dry-run: send a test copy to the admin mailbox only (do not email residents)",
                    value=False,
                    key="broadcast_dry_run",
                    help=(
                        "When checked, nothing is sent to residents. A single test email goes to "
                        "the app Gmail account. If personal bookings are enabled, the first "
                        "selected recipient's bookings are used as the sample payload."
                    ),
                )

                submitted = st.form_submit_button(
                    "📨 Review & Send",
                    type="primary",
                    width='stretch',
                )

            # Validation + send only after the form is submitted (not on every keystroke).
            if submitted:
                include_personal_bookings = attach_bookings.startswith("Yes")
                selected_emails = selected_in_form
                n_recip = len(selected_emails)
                subject_clean = (broadcast_subject or "").strip()
                body_raw = broadcast_body or ""

                if n_recip == 0:
                    st.error("Select at least one recipient (or choose “All users with active bookings”).")
                elif not subject_clean:
                    st.error("Subject is required.")
                elif not body_raw.strip():
                    st.error("Body is required.")
                else:
                    admin_mailbox = st.secrets.get("GMAIL_USER", "miracourtbooking@gmail.com")
                    body_escaped = (
                        body_raw.replace("&", "&amp;")
                        .replace("<", "&lt;")
                        .replace(">", "&gt;")
                    )
                    body_html_inner = body_escaped.replace("\n", "<br>")
                    notice_html = f"""
                    <!DOCTYPE html>
                    <html>
                    <head><meta charset="utf-8"></head>
                    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #222; line-height: 1.55;">
                      <div style="max-width: 600px; margin: 24px auto; padding: 24px; background: #fff; border: 1px solid #e2e8f0; border-radius: 10px;">
                        <p style="margin: 0 0 16px 0; color: #0d5384; font-weight: 700;">Mira Court Booking — Community Notice</p>
                        <div style="white-space: normal;">{body_html_inner}</div>
                        <p style="margin-top: 24px; font-size: 12px; color: #718096;">
                          You are receiving this because your villa currently has an active court booking.
                        </p>
                      </div>
                    </body>
                    </html>
                    """

                    if dry_run:
                        st.info(
                            f"**Dry-run:** 1 test email → `{admin_mailbox}` only. "
                            f"Would target **{n_recip}** resident(s)"
                            + (" with personal bookings." if include_personal_bookings else ".")
                        )
                    elif include_personal_bookings:
                        st.info(f"**Delivery:** {n_recip} individual email(s) with personal bookings.")
                    elif n_recip == 1:
                        st.info(f"**Delivery:** 1 email · To: `{selected_emails[0]}`")
                    else:
                        st.info(
                            f"**Delivery:** 1 shared email · To: `miracourtbooking@gmail.com` · "
                            f"BCC: {n_recip} addresses"
                        )

                    sent_ok = 0
                    failed = []
                    with st.spinner("Sending…" if dry_run else f"Sending to {n_recip} recipient(s)…"):
                        if dry_run:
                            test_subject = f"[TEST / DRY-RUN] {subject_clean}"
                            if include_personal_bookings and selected_emails:
                                sample_email = selected_emails[0]
                                merged = get_merged_active_bookings_for_email(sample_email)
                                preview_note = (
                                    f"<p style='background:#fff3cd;border:1px solid #ffc107;"
                                    f"padding:10px;border-radius:6px;font-size:13px;'>"
                                    f"<b>DRY-RUN PREVIEW</b> — sample bookings for "
                                    f"<code>{sample_email}</code>. Residents were not emailed."
                                    f"</p>"
                                )
                                ok = _send_broadcast_with_personal_bookings(
                                    admin_mailbox,
                                    test_subject,
                                    preview_note + body_html_inner,
                                    merged,
                                )
                            else:
                                dry_banner = (
                                    "<p style='background:#fff3cd;border:1px solid #ffc107;"
                                    "padding:10px;border-radius:6px;font-size:13px;'>"
                                    "<b>DRY-RUN PREVIEW</b> — residents were not emailed. "
                                    f"This would have gone to {n_recip} recipient(s)."
                                    "</p>"
                                )
                                dry_html = notice_html.replace(
                                    body_html_inner,
                                    dry_banner + body_html_inner,
                                    1,
                                )
                                ok = send_gmail_smtp(admin_mailbox, test_subject, dry_html)
                            if ok:
                                sent_ok = 1
                            else:
                                failed = [admin_mailbox]
                        elif include_personal_bookings:
                            for email in selected_emails:
                                merged = get_merged_active_bookings_for_email(email)
                                ok = _send_broadcast_with_personal_bookings(
                                    email, subject_clean, body_html_inner, merged
                                )
                                if ok:
                                    sent_ok += 1
                                else:
                                    failed.append(email)
                        elif n_recip == 1:
                            if send_gmail_smtp(selected_emails[0], subject_clean, notice_html):
                                sent_ok = 1
                            else:
                                failed = list(selected_emails)
                        else:
                            if send_gmail_smtp(
                                "miracourtbooking@gmail.com",
                                subject_clean,
                                notice_html,
                                bcc_emails=selected_emails,
                            ):
                                sent_ok = n_recip
                            else:
                                failed = list(selected_emails)

                    if dry_run:
                        if sent_ok and not failed:
                            add_log(
                                "Admin Broadcast",
                                f"Admin dry-run test email sent to {admin_mailbox} "
                                f"(subject: {subject_clean[:80]}; would target {n_recip} resident(s)"
                                f"{'; personal bookings' if include_personal_bookings else ''})",
                                fingerprint=current_device,
                            )
                            st.success(
                                f"✅ Test email sent to **{admin_mailbox}**. "
                                "No residents were contacted."
                            )
                        else:
                            st.error("❌ Failed to send test email. Check Gmail SMTP credentials in secrets.")
                    elif sent_ok and not failed:
                        attach_note = " (with personal bookings)" if include_personal_bookings else ""
                        add_log(
                            "Admin Broadcast",
                            f"Admin sent broadcast email to {sent_ok} recipient(s) "
                            f"(subject: {subject_clean[:80]}){attach_note}",
                            fingerprint=current_device,
                        )
                        st.success(f"✅ Email sent successfully to {sent_ok} recipient(s){attach_note}.")
                    elif sent_ok and failed:
                        add_log(
                            "Admin Broadcast",
                            f"Admin broadcast partial success: {sent_ok} sent, {len(failed)} failed "
                            f"(subject: {subject_clean[:80]})",
                            fingerprint=current_device,
                        )
                        st.warning(
                            f"Sent to {sent_ok}, but failed for {len(failed)}: "
                            + ", ".join(failed[:5])
                            + ("…" if len(failed) > 5 else "")
                        )
                    else:
                        st.error("❌ Failed to send. Check Gmail SMTP credentials in secrets and try again.")


    elif admin_pass:
        st.error("Incorrect Password")



def render_availability_tab(is_coach, current_device, resident_ctx=None, coach_ctx=None, logout_label="🚪 Logout / Change Villa"):
    """Shared Availability & Booking tab: ONE booking area (the schedule grid plus the booking bar
    directly under it, sharing a single selection) and the court/villa lookup. Residents and
    coaches share everything except how the Book button books (single villa vs. pool of villas),
    so only that part is branched via is_coach. This tab is the whole check-and-book flow."""
    st.subheader("Court Availability & Booking")
    if has_recent_announcement():
        st.info("🔴 **New in 📢 News** — check the News tab for the latest community update.")
    _current_identity_email = (coach_ctx or {}).get("coach_email") if is_coach else (resident_ctx or {}).get("verified_user_email")
    date_options = [f"{d.strftime('%Y-%m-%d')} ({d.strftime('%A')})" for d in get_next_14_days(is_supporter=is_supporter_email(_current_identity_email))]
    selected_date_full = st.selectbox("Select Date:", date_options, key="tab1_date_select")
    selected_date = selected_date_full.split(" (")[0]
    bookings_with_details = _day_map(selected_date)     # read-only use below

    def _pretty_day(date_str):
        """'2026-09-21' -> 'Monday, 21 Sep'"""
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %d %b")

    def _reset_booking_bar():
        """Return the whole booking area (table selection + bar) to a blank state. A tapped cell
        is remembered in two places: our own keys AND the table widget's internal selection
        (stored under its widget key). Clearing only ours made the table re-report the tap on the
        next rerun and re-select the slot, so the table also gets a brand-new widget key (nonce),
        i.e. an empty selection. The same goes for the court/time/2-hour widgets in the bar: deleting their
        stored value blanks the app's copy but the BROWSER keeps showing the old text in the dropdowns, so
        they are given brand-new widget keys instead (see _bk). Must run BEFORE the pickers are drawn."""
        st.session_state["avail_grid_last_tap"] = None
        st.session_state["avail_grid_nonce"] = st.session_state.get("avail_grid_nonce", 0) + 1
        st.session_state["bar_nonce"] = st.session_state.get("bar_nonce", 0) + 1

    data = {}
    _is_past_now = _past_checker()
    for h in get_start_hours_for_date(selected_date):
        label = f"{h:02d}:00 - {h+1:02d}:00"
        row = []
        slot_past = _is_past_now(selected_date, h)      # same answer for every court, so ask once per hour
        for court in courts:
            key = (court, h)
            if slot_past: row.append("—")
            elif key in bookings_with_details:
                full_comm, villa_num = bookings_with_details[key].rsplit(" - ", 1)
                abbr = abbreviate_community(full_comm)
                row.append(f"{abbr}-{villa_num}")
            else: row.append("Available")
        data[label] = row
    df_avail = pd.DataFrame(data, index=courts)

    # --- Click-to-book: single cell only (touch-friendly) + optional 2-hour toggle ---
    def _color_avail_grid_cell(val):
        if val in ("★ Selected", "✓ SELECTED", "★ SELECTED"):
            return (
                "background-color: #f59e0b; color: #111827; font-weight: 900; "
                "box-shadow: inset 0 0 0 3px #b45309;"
            )
        return color_cell(val)

    def _parse_single_avail_cell(event, df):
        """Return (court, hour, col_label) for the first selected cell, or None."""
        if event is None:
            return None
        sel = getattr(event, "selection", None)
        if sel is None and isinstance(event, dict):
            sel = event.get("selection")
        raw = None
        if sel is not None:
            raw = getattr(sel, "cells", None)
            if raw is None and isinstance(sel, dict):
                raw = sel.get("cells")
        if not raw:
            return None
        c = raw[0]
        try:
            if isinstance(c, dict):
                r, col = c.get("row"), c.get("column", c.get("col"))
            elif isinstance(c, (list, tuple)) and len(c) >= 2:
                r, col = c[0], c[1]
            else:
                return None
            row_labels = list(df.index)
            col_labels = list(df.columns)
            if isinstance(r, (int, float)) and not isinstance(r, bool):
                r_i = int(r)
                if r_i < 0 or r_i >= len(row_labels):
                    return None
                court = row_labels[r_i]
            else:
                court = str(r)
                if court not in row_labels:
                    return None
            if isinstance(col, (int, float)) and not isinstance(col, bool):
                c_i = int(col)
                if c_i < 0 or c_i >= len(col_labels):
                    return None
                col_label = col_labels[c_i]
            else:
                col_label = str(col)
                if col_label not in col_labels:
                    return None
            hour = int(str(col_label).split(":")[0])
            return (court, hour, col_label)
        except Exception:
            return None

    # ---------------------------------------------------------------------------------------------
    # ONE cohesive booking area. The table and the booking bar under it share a single selection:
    # tapping a green slot fills the bar's Court/Time; choosing them by hand highlights the matching
    # slot in the table. Clear (or a successful booking) blanks both.
    # ---------------------------------------------------------------------------------------------
    ss = st.session_state

    def _bk(name):
        """Widget key for one of the booking bar's controls. It carries counters so a reset (or a stale
        value) can give the browser a genuinely new, empty widget: 'bar_nonce' for the whole bar,
        'nonce_<name>' for a single control."""
        return f"{name}_{ss.get('bar_nonce', 0)}_{ss.get('nonce_' + name, 0)}"

    if ss.pop("avail_bar_reset", False):
        _reset_booking_bar()
    if ss.get("avail_last_date") != selected_date:
        if ss.get("avail_last_date") is not None:   # date changed: drop the table's own tap (keeps valid picks)
            ss["avail_grid_nonce"] = ss.get("avail_grid_nonce", 0) + 1
            ss["avail_grid_last_tap"] = None
        ss["avail_last_date"] = selected_date

    # Keep the pickers consistent with what is actually free on the displayed date
    _sel_court = ss.get(_bk("q_court_select"))
    _free_sel = get_available_hours(_sel_court, selected_date) if _sel_court else []
    _sel_time = ss.get(_bk("q_time_select"))
    if _sel_time and int(_sel_time.split(":")[0]) not in _free_sel:
        ss["nonce_q_time_select"] = ss.get("nonce_q_time_select", 0) + 1     # e.g. the date changed: a fresh, blank time picker
        _sel_time = None
    _sel_h = int(_sel_time.split(":")[0]) if _sel_time else None
    _sel_two = bool(ss.get(_bk("q_2_hours_check"))) and _sel_h is not None and (_sel_h + 1) in _free_sel

    display_df = df_avail.copy()
    if _sel_court and _sel_h is not None:
        for _h in ([_sel_h, _sel_h + 1] if _sel_two else [_sel_h]):
            _lab = f"{_h:02d}:00 - {_h+1:02d}:00"
            if _lab in display_df.columns and display_df.loc[_sel_court, _lab] == "Available":
                display_df.loc[_sel_court, _lab] = "★ Selected"

    st.markdown(
        """
        <style>
        div[data-testid="stDataFrame"] [aria-selected="true"],
        div[data-testid="stDataFrameResizable"] [aria-selected="true"],
        [data-testid="stDataFrame"] div[role="gridcell"][aria-selected="true"] {
            background-color: #f59e0b !important;
            color: #111827 !important;
            font-weight: 800 !important;
            outline: 3px solid #b45309 !important;
            outline-offset: -3px !important;
            box-shadow: inset 0 0 0 2px #92400e !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    grid_event = None
    try:
        grid_event = st.dataframe(
            display_df.style.map(_color_avail_grid_cell),
            width="stretch",
            key=f"avail_grid_click_{ss.get('avail_grid_nonce', 0)}",
            on_select="rerun",
            selection_mode="single-cell",
        )
    except TypeError:
        st.dataframe(df_avail.style.map(color_cell), width="stretch")

    picked = _parse_single_avail_cell(grid_event, df_avail)
    if picked is not None:
        if ss.get("avail_grid_last_tap") != picked:
            ss["avail_grid_last_tap"] = picked
            _p_court, _p_hour, _p_lab = picked
            _p_cell = df_avail.loc[_p_court, _p_lab]
            if _p_cell == "Available":
                ss[_bk("q_court_select")] = _p_court
                ss[_bk("q_time_select")] = f"{_p_hour:02d}:00"
                ss[_bk("q_2_hours_check")] = False
            else:
                ss["avail_bar_notice"] = (
                    "That time has passed." if _p_cell == "—" else "That slot is already booked — tap a green one."
                )
            st.rerun()
    elif grid_event is not None:
        _sel_obj = getattr(grid_event, "selection", None)
        if _sel_obj is None and isinstance(grid_event, dict):
            _sel_obj = grid_event.get("selection")
        _raw_cells = None
        if _sel_obj is not None:
            _raw_cells = getattr(_sel_obj, "cells", None)
            if _raw_cells is None and isinstance(_sel_obj, dict):
                _raw_cells = _sel_obj.get("cells")
        if _raw_cells is not None and len(_raw_cells) == 0 and ss.get("avail_grid_last_tap") is not None:
            # The user un-tapped the slot. If the bar still holds that same slot, blank it too.
            _lt_court, _lt_hour, _ = ss["avail_grid_last_tap"]
            ss["avail_grid_last_tap"] = None
            if ss.get(_bk("q_court_select")) == _lt_court and ss.get(_bk("q_time_select")) == f"{_lt_hour:02d}:00":
                _reset_booking_bar()
                st.rerun()

    if is_coach:
        villa = sub_community = verified_user_email = None
    else:
        villa = resident_ctx["villa"]
        sub_community = resident_ctx["sub_community"]
        verified_user_email = resident_ctx["verified_user_email"]

    _notice = ss.pop("avail_bar_notice", None)
    if _notice:
        st.caption(f"⚠️ {_notice}")

    with st.container(border=True):
        b1, b2, b3 = st.columns([3, 2, 2])
        with b1:
            q_court = st.selectbox(
                "Court", options=courts, index=None, placeholder="Tap a green slot, or choose a court",
                key=_bk("q_court_select"), label_visibility="collapsed",
            )
        q_free = get_available_hours(q_court, selected_date) if q_court else []
        with b2:
            q_time = st.selectbox(
                "Time", options=[f"{h:02d}:00" for h in q_free], index=None,
                placeholder=("Time" if q_free else ("No free slots" if q_court else "Time")),
                disabled=not q_free, key=_bk("q_time_select"), label_visibility="collapsed",
            )
        q_h = int(q_time.split(":")[0]) if q_time else None
        q_can_2h = q_h is not None and (q_h + 1) in q_free
        with b3:
            if not q_can_2h and ss.get(_bk("q_2_hours_check")):
                ss[_bk("q_2_hours_check")] = False    # 2nd slot not free -> untick (before the checkbox is drawn)
            q_2h = st.checkbox(
                "2 hours" if (q_time is None or q_can_2h) else "2 hours (n/a)",
                key=_bk("q_2_hours_check"), disabled=not q_can_2h,
            )
        q_slots = 2 if (q_2h and q_can_2h) else 1
        q_ready = bool(q_court and q_time)

        s1, s2, s3 = st.columns([5, 2, 1.3])
        with s1:
            if q_ready:
                st.markdown(
                    f"📌 **{_pretty_day(selected_date)} · {q_court} · {q_h:02d}:00 – {q_h + q_slots:02d}:00** "
                    f"({q_slots} hr{'s' if q_slots > 1 else ''})"
                )
            else:
                st.caption("Nothing selected yet.")
        with s2:
            do_book = st.button("🚀 Book", type="primary", key="q_book_btn", width='stretch', disabled=not q_ready)
        with s3:
            if st.button("Clear", key="q_clear_btn", width='stretch', disabled=not (q_court or q_time)):
                ss["avail_bar_reset"] = True
                st.rerun()

        if is_coach:
            st.caption(
                f"Pool: **{coach_ctx['total_active']} / {coach_ctx['total_allowed']}** active across "
                f"{coach_ctx['n_villas']} villa(s). A 2-hour session that doesn't fit one villa's remaining "
                "quota is split across two of your villas automatically."
            )
        else:
            _q_limit = get_active_booking_limit(sub_community, villa, for_date=selected_date)
            _q_active = get_active_bookings_count_display(villa, sub_community)
            _q_daily = get_daily_bookings_count_display(villa, sub_community, selected_date)
            st.caption(f"Your active bookings: **{_q_active} / {_q_limit}** · On this date: **{_q_daily} / 2**")

        if do_book and q_ready:
            hours_to_book = list(range(q_h, q_h + q_slots))
            if is_coach:
                success, result = process_coach_booking(
                    coach_ctx["coach_email"], coach_ctx["coach_name"], q_court, selected_date,
                    hours_to_book, fingerprint=current_device,
                )
                if success:
                    ss["avail_bar_reset"] = True
                    st.balloons()
                    st.success("Booked successfully using allocations from: " + ", ".join([f"Villa {r['villa']}" for r in result]))
                    time.sleep(2)
                    st.rerun()
                else:
                    st.error(result)
            else:
                ok, err = validate_booking_attempt(
                    sub_community, villa, q_court, selected_date, hours_to_book, fingerprint=current_device,
                )
                if not ok:
                    st.error(err)
                else:
                    success = True
                    booked_slots = []
                    for h in hours_to_book:
                        if book_slot(villa, sub_community, q_court, selected_date, h, fingerprint=current_device):
                            booked_slots.append(h)
                        else:
                            success = False
                            break
                    if success:
                        ss["avail_bar_reset"] = True
                        send_booking_notification_once("created", villa, sub_community, q_court, selected_date, booked_slots, verified_user_email)
                        st.balloons()
                        st.success(f"Booked {q_slots} slot(s) for {q_court} starting at {q_h:02d}:00")
                        if verified_user_email and "@" in verified_user_email:
                            st.info(f"📧 A confirmation email has been sent to **{verified_user_email}** from **miracourtbooking@gmail.com**. If you don't see it, please check your spam/junk folder.")
                        time.sleep(2)
                        st.rerun()
                    else:
                        st.error("❌ One or more slots were taken! Please refresh.")

    curr_auth = st.query_params.get("auth")
    full_url = f"/?view=full&auth={curr_auth}" if curr_auth else "/?view=full"
    st.link_button("🌐 View Full 14-Day Schedule (Full Page)", url=full_url)

    render_slot_watch_section(
        selected_date,
        coach_ctx["coach_email"] if is_coach else verified_user_email,
        None if is_coach else sub_community,
        None if is_coach else villa,
    )

    with st.expander("🔍 Court & Booking Lookup"):
        st.caption(f"Check who holds a slot on {selected_date} and its history, or look up a villa's bookings.")
        st.markdown("**By court & time**")
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

                _bnu_key = f"bnu_pending_{hist_court}_{selected_date}_{hist_hour}"
                if st.session_state.get(_bnu_key):
                    st.warning(
                        f"⚠️ You're about to report **{hist_court}** on **{selected_date}** at "
                        f"**{hist_time_label}** (currently held by {bookings_with_details[hist_key]}) "
                        "as booked but sitting unused. This will email the resident and add a "
                        "public note to the Community Activity Log. Only confirm if the court is "
                        "genuinely empty right now — please don't report a slot as a joke or out of spite."
                    )
                    bnu_c1, bnu_c2 = st.columns(2)
                    with bnu_c1:
                        if st.button("✅ Yes, Confirm Report", key=f"{_bnu_key}_yes", type="primary", width='stretch'):
                            # Re-verify the slot is still actually booked before acting on it —
                            # it may have been cancelled in the moments since the page loaded.
                            recheck = run_query(
                                supabase.table("bookings").select("villa, sub_community")
                                .eq("court", hist_court).eq("date", selected_date).eq("start_hour", hist_hour)
                            )
                            if not recheck or not recheck.data:
                                st.info("This slot is no longer booked — it looks like it just freed up. No report needed.")
                            elif is_slot_already_reported(hist_court, selected_date, hist_hour):
                                st.info("\"Booked but not used\" has already been reported for this slot — no need to report it again.")
                            else:
                                r_villa = recheck.data[0]["villa"]
                                r_sub = recheck.data[0]["sub_community"]
                                reporter_label = (
                                    f"Coach {coach_ctx['coach_name']}" if is_coach
                                    else f"{resident_ctx['sub_community']} Villa {resident_ctx['villa']}"
                                )
                                report_booked_not_used(r_sub, r_villa, hist_court, selected_date, hist_hour, reporter_label)
                                st.success("Reported — the resident has been emailed, and this has been logged.")
                            st.session_state.pop(_bnu_key, None)
                            time.sleep(1.5)
                            st.rerun()
                    with bnu_c2:
                        if st.button("Cancel", key=f"{_bnu_key}_no", width='stretch'):
                            st.session_state.pop(_bnu_key, None)
                            st.rerun()
                else:
                    if is_slot_reported_display(hist_court, selected_date, hist_hour):
                        st.caption("ℹ️ \"Booked but not used\" has already been reported for this slot.")
                    elif st.button("🚨 Booked but Not Used!", key=f"bnu_btn_{hist_court}_{selected_date}_{hist_hour}"):
                        st.session_state[_bnu_key] = True
                        st.rerun()
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
        st.markdown("**By villa**")
        if villas_active:
            look_villa = st.selectbox("Select Villa to see details:", options=["-- Select --"] + villas_active, key="lookup_villa_select")
            if look_villa != "-- Select --":
                active_list = get_active_bookings_for_villa_display(look_villa)
                if active_list: st.selectbox("Active bookings for this villa:", options=active_list, key="lookup_villa_bookings_select")
                else: st.write("No active bookings found for this villa.")

    st.divider()
    if is_coach:
        _who = f"Coach **{coach_ctx['coach_name']}** · {coach_ctx['coach_email']}  \n🎾 {coach_ctx['villa_line']}"
    else:
        _who = f"**{sub_community} - Villa {villa}** · {verified_user_email}"
    _live = (
        f"**{total_residences}** residences have **{total_bookings}** active bookings  \n"
        if _stats_ok else "Live stats are refreshing…  \n"
    )
    st.caption(
        f"✅ Logged in as {_who}  \n"
        f"🏘️ {_live}"
        f"Serving {_user_count_label} active users — community coded and funded."
    )
    if st.button(logout_label, width='stretch', key="tab1_logout"):
        logout_action()

# ==========================================
# --- SUPPORTER-ONLY ACCESS GATE (admin emergency toggle for high-traffic periods) ---
# ==========================================
# Sits right before BOTH the coach and resident views, after every possible login path (cached
# auto-login, fresh OTP, the admin resident-bypass tool) has already converged on
# st.session_state.verified_email / coach_email — so this one check covers all of them. Re-run on
# every script rerun (not just at login), so turning this ON also cuts off anyone already using
# the app on their very next interaction, which is the point of an emergency traffic control.
_supporter_gate_identity = (
    st.session_state.coach_email if (COACH_FEATURE_ENABLED and st.session_state.get('is_coach'))
    else st.session_state.get("verified_email")
)
if (
    is_supporter_mode_enabled()
    and not st.session_state.get("supporter_gate_bypass")
    and not is_supporter_email(_supporter_gate_identity)
):
    render_supporter_gate_screen()
    st.stop()

# ==========================================
# --- ROUTING: COACH VIEW VS RESIDENT VIEW ---
# ==========================================

if COACH_FEATURE_ENABLED and st.session_state.get('is_coach'):
    coach_email = st.session_state.coach_email
    coach_name = st.session_state.coach_name
    current_device = st.session_state.get("device_uuid")

    assigned_villas, total_allowed, total_active = get_coach_dashboard_stats(coach_email)

    if assigned_villas:
        villa_summary_bits = []
        for v in assigned_villas:
            v_active = get_active_bookings_count(v['villa'], v['sub_community'])
            v_limit = get_active_booking_limit(v['sub_community'], v['villa'])
            villa_summary_bits.append(f"{v['sub_community']} Villa {v['villa']} ({v_active}/{v_limit})")
        _coach_villa_line = " • ".join(villa_summary_bits)
    else:
        _coach_villa_line = "No villas assigned to your pool yet. Contact your admin."

    coach_ctx = {
        "villa_line": _coach_villa_line,   # shown in the footer above Logout, not above the tabs
        "coach_email": coach_email, "coach_name": coach_name,
        "total_allowed": total_allowed, "total_active": total_active, "n_villas": len(assigned_villas),
    }

    c_tab_avail, c_tab_mine, c_tab_resources, c_tab_maint, c_tab_log, c_tab_news = st.tabs(["📅 Plan & Book", "📋 My Bookings", "🔗 Resources", "🛠️ Maint.", "📜 Log", announcements_tab_label()])

    with c_tab_avail:
        render_whatsapp_banner()
        render_availability_tab(
            is_coach=True,
            current_device=current_device,
            coach_ctx=coach_ctx,
            logout_label="🚪 Logout",
        )

    with c_tab_mine:
        render_whatsapp_banner()
        st.subheader("📋 My Coach Bookings")
        my_coach_b = get_coach_bookings(coach_email)

        merged_coach_bookings = merge_consecutive_bookings([
            {"court": b["court"], "date": b["date"], "start_hour": b["start_hour"], "id": b["id"],
             "v": b["villa"], "sc": b["sub_community"]}
            for b in my_coach_b
        ])

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
                st.download_button(label="📥 Export to CSV", data=csv_data, file_name=f"coach_{coach_name}_bookings.csv", mime="text/csv", width='stretch')
            with b_col2:
                if st.button("📧 Email Me All My Bookings", width='stretch', key="coach_email_summary_btn"):
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

    with c_tab_resources:
        render_whatsapp_banner()
        render_resources_tab()

    with c_tab_maint:
        render_whatsapp_banner()
        render_court_maintenance_tab(f"Coach {coach_name}", current_device)

    with c_tab_log:
        render_whatsapp_banner()
        render_activity_log_tab(current_device)

    with c_tab_news:
        render_announcements_tab()

else:
    # ----------------------------------------
    # STANDARD RESIDENT DASHBOARD
    # ----------------------------------------
    sub_community, villa = st.session_state.sub_community, st.session_state.villa
    verified_user_email = st.session_state.get("verified_email", "Verified")

    # --- ONE-EMAIL-PER-VILLA MIGRATION CHECK ---
    # Runs on every load of the resident dashboard (not just at login) so it also catches
    # already-authenticated sessions restored from localStorage. Villas from before the
    # 1-email-per-villa policy may still have 2 approved emails on file; the first time anyone
    # from such a villa interacts with the app, force a one-time consolidation down to
    # MAX_EMAILS_PER_VILLA before letting them use anything else. Also guards against a stale
    # session for an email that was just removed from this villa (e.g. via that same dialog on
    # a co-resident's device).
    _villa_claims_now = get_approved_email_claims_for_villa(sub_community, villa)
    _villa_emails_now = {(c.get("email") or "").strip().lower() for c in _villa_claims_now}
    if len(_villa_emails_now) > MAX_EMAILS_PER_VILLA:
        show_email_consolidation_dialog(sub_community, villa, _villa_claims_now, verified_user_email)
        render_deferred_helpers()
        st.stop()
    elif _villa_emails_now and (verified_user_email or "").strip().lower() not in _villa_emails_now:
        st.warning(
            "🚫 Your access to this villa has changed — your email is no longer the registered "
            "contact for this residence (likely due to the new 1-email-per-villa policy). "
            "Please log in again with the currently registered email, or contact Dev via Court "
            "Maintenance if this looks wrong."
        )
        if st.button("🚪 Logout", key="consolidation_stale_logout"):
            logout_action()
        render_deferred_helpers()
        st.stop()

    if is_donor_villa(sub_community, villa):
        render_donor_legend_banner()

    # --- VILLA SNIPING INTERCEPTOR & ENFORCEMENT ---
    current_device = st.session_state.get("device_uuid")
    if is_sniping_exempt_villa(sub_community, villa):
        sniping_level, hopping_villas, cooldown_hrs = 0, [], 0
    else:
        sniping_level, hopping_villas, cooldown_hrs = check_device_sniping_status(
            current_device, verified_user_email, sub_community, villa
        )

    if sniping_level == 2:
        _hopping_pairs = []
        for _tag in hopping_villas:
            if " - " in _tag:
                _s, _v = _tag.rsplit(" - ", 1)
                _hopping_pairs.append((_s, _v))
        _hopping_pairs.append((sub_community, villa))
        _ban_tag = build_ban_tag(verified_user_email, _hopping_pairs)
        add_log(
            "Sniping Penalty",
            f"4-day penalty active for {sub_community} Villa {villa} (Device: {current_device}, Email: {verified_user_email}) {_ban_tag}",
            fingerprint=current_device
        )
        show_sniping_lockout_dialog(cooldown_hrs)
        render_deferred_helpers()
        st.stop()
    elif sniping_level == 1 and not st.session_state.get("seen_sniping_warning", False):
        add_log(
            "Sniping Warning",
            f"Cross-villa warning triggered for {sub_community} Villa {villa}. Prior activity on: {', '.join(hopping_villas)}",
            fingerprint=current_device
        )
        show_sniping_warning_dialog(hopping_villas)

    tab_avail, tab_mine, tab_resources, tab_maint, tab_log, tab_news = st.tabs(["📅 Plan & Book", "📋 My Bookings", "🔗 Resources", "🛠️ Maint.", "📜 Log", announcements_tab_label()])

    with tab_avail:
        render_whatsapp_banner()
        render_availability_tab(
            is_coach=False,
            current_device=current_device,
            resident_ctx={"villa": villa, "sub_community": sub_community, "verified_user_email": verified_user_email},
        )

    with tab_mine:
        render_whatsapp_banner()
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
        if sub_community == "Mira 1" and villa in ["229", "231", "249"]:
            my_b = []
            for v_num in ["229", "231", "249"]:
                vb = get_user_bookings(v_num, "Mira 1")
                for b in vb: b['orig_v'] = v_num; b['orig_sc'] = "Mira 1"
                my_b.extend(vb)
            # my_b above is a GROUP-WIDE total (all 3 shared villas combined), so the limit shown
            # alongside it has to be the group's combined limit too — the sum of each villa's own
            # limit (229 may be donor-elevated to 8 while 231/249 sit at 6; this adds up whatever
            # each one currently is, e.g. 8+6+6=20 during the donor window, 6+6+6=18 outside it) —
            # never just the single logged-in villa's own limit, which would compare a 3-villa
            # total against a 1-villa cap.
            limit_val = sum(get_active_booking_limit("Mira 1", v) for v in ["229", "231", "249"])
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

        merged_bookings = merge_consecutive_bookings([
            {"court": b["court"], "date": b["date"], "start_hour": b["start_hour"], "id": b["id"],
             "v": b["orig_v"], "sc": b["orig_sc"]}
            for b in my_b
        ])

        if merged_bookings:
            if st.button("📧 Email Me All My Bookings", type="primary", width='stretch', key="email_all_bookings_btn"):
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
            st.markdown("""
                <style>
                div[class*="st-key-ics_download_"] button {
                    background-color: #1f8a45 !important;
                    color: #ffffff !important;
                    border: 1px solid rgba(255,255,255,0.35) !important;
                }
                div[class*="st-key-ics_download_"] button:hover {
                    background-color: #166534 !important;
                    border-color: #ffffff !important;
                }
                div[class*="st-key-cancel_"] button {
                    background-color: #dc2626 !important;
                    color: #ffffff !important;
                    border: 1px solid rgba(255,255,255,0.35) !important;
                    font-weight: 700 !important;
                }
                div[class*="st-key-cancel_"] button:hover {
                    background-color: #b91c1c !important;
                    border-color: #ffffff !important;
                }
                /* Keep the Card/Calendar/Cancel row side-by-side even on narrow mobile screens,
                   overriding Streamlit's default behavior of stacking columns vertically below
                   a certain viewport width. */
                div[class*="st-key-booking_action_row_"] div[data-testid="stHorizontalBlock"] {
                    flex-wrap: nowrap !important;
                    gap: 0.4rem !important;
                }
                div[class*="st-key-booking_action_row_"] div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
                    flex: 1 1 0 !important;
                    width: auto !important;
                    min-width: 0 !important;
                }
                div[class*="st-key-booking_action_row_"] button {
                    padding-left: 0.3rem !important;
                    padding-right: 0.3rem !important;
                    font-size: 0.82rem !important;
                    white-space: nowrap !important;
                }
                </style>
            """, unsafe_allow_html=True)
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

                    with st.container(key=f"booking_action_row_{i}"):
                        row_c1, row_c2, row_c3 = st.columns(3)
                        with row_c1:
                            render_share_or_download_button(jpg_bytes, f"{clean_ref_filename}.jpg", id_display, key=i)
                        with row_c2:
                            ics_bytes = generate_ics_content(b['court'], b['date'], b['start_hours'], b['sc'], b['v'])
                            st.download_button(
                                label="📅 Calendar",
                                data=ics_bytes,
                                file_name=f"{clean_ref_filename}.ics",
                                mime="text/calendar",
                                key=f"ics_download_{i}",
                                width='stretch'
                            )
                        with row_c3:
                            if st.button("❌ Cancel", key=f"cancel_{i}", width='stretch'):
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

    with tab_resources:
        render_whatsapp_banner()
        render_resources_tab()

    with tab_maint:
        render_whatsapp_banner()
        render_court_maintenance_tab(f"{sub_community} Villa {villa}", current_device)

    with tab_log:
        render_whatsapp_banner()
        render_activity_log_tab(current_device)

    with tab_news:
        render_announcements_tab()

col1, col2 = st.columns([1, 5])
with col1: st.markdown(f'<img src="https://raw.githubusercontent.com/mahadevbk/courtbooking/main/qr-code.miracourtbooking.streamlit.app.png" height="100">', unsafe_allow_html=True)
with col2: st.markdown("""
    <div style='background-color: #0d5384; padding: 1rem; border-left: 5px solid #fff500; border-radius: 0.5rem; color: white;'>
    Built with ❤️ using <a href='https://streamlit.io/' style='color: #ccff00;'>Streamlit</a> — free and open source.
    <a href='https://devs-scripts.streamlit.app/' style='color: #ccff00;'>Other Scripts by dev</a> on Streamlit.
    </div>
    """, unsafe_allow_html=True)

render_deferred_helpers()   # invisible; last on purpose so it takes no space between the title and the tabs

# ==========================================
# --- COMMUNITY POSTER ---
# Shown to every authenticated user, regardless of resident/coach view.
# IMPORTANT: keep this AFTER render_deferred_helpers(). On a browser refresh, the app may need
# one JS round-trip to restore login/localStorage state. Putting the poster before those helpers
# could cause the run to end before the dialog is opened.
# ==========================================
if st.session_state.get("authenticated", False):
    maybe_show_community_poster()
