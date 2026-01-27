import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import random
import math
import ast
import datetime
import re

# ────────────────────────────────────────────────
# PAGE CONFIG + AUDIOWIDE FONT
# ────────────────────────────────────────────────
st.set_page_config(page_title="Tennis Tournament Organiser", layout="wide", page_icon="🎾")

st.markdown("""
<link href="https://fonts.googleapis.com/css2?family=Audiowide&display=swap" rel="stylesheet">
<style>
    html, body, [data-testid="stAppViewContainer"], .stApp, .st-emotion-cache-* {
        font-family: 'Audiowide', sans-serif !important;
    }
    h1, h2, h3, h4, h5, h6, [data-testid="stMarkdownContainer"] * {
        font-family: 'Audiowide', sans-serif !important;
    }
    .stTabs [data-baseweb="tab"] {
        font-family: 'Audiowide', sans-serif !important;
    }
    button, .stButton > button {
        font-family: 'Audiowide', sans-serif !important;
    }
    input, textarea, select, .stTextInput input, .stSelectbox select {
        font-family: 'Audiowide', sans-serif !important;
    }
    .stDataFrame, .dataframe, td, th {
        font-family: 'Audiowide', sans-serif !important;
    }
    section[data-testid="stSidebar"] * {
        font-family: 'Audiowide', sans-serif !important;
    }
    .match-card {
        background: rgba(30, 41, 59, 0.92);
        border: 1px solid #475569;
        border-radius: 12px;
        padding: 24px;
        margin: 24px auto;
        max-width: 420px;
        box-shadow: 0 6px 20px rgba(0,0,0,0.35);
        text-align: center;
    }
    .match-card:hover {
        transform: translateY(-6px);
        box-shadow: 0 10px 25px rgba(34, 197, 94, 0.3);
    }
    .player-row {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 16px;
        margin: 12px 0;
    }
    .player-avatar {
        width: 80px; height: 80px;
        border-radius: 50%;
        object-fit: cover;
        border: 3px solid #4ade80;
        box-shadow: 0 2px 10px rgba(74,222,128,0.4);
    }
    .mini-avatar {
        width: 40px; height: 40px;
        border-radius: 50%;
        object-fit: cover;
        border: 2px solid #4ade80;
    }
    .vs-divider {
        font-size: 1.5em;
        font-weight: bold;
        color: #4ade80;
        margin: 12px 0;
        letter-spacing: 2px;
    }
    .court-header {
        font-size: 1.4em;
        color: #4ade80;
        margin-bottom: 16px;
        display: flex;
        align-items: center;
        gap: 10px;
        justify-content: center;
    }
    .court-header::before { content: "🎾"; }
</style>
""", unsafe_allow_html=True)

# ────────────────────────────────────────────────
# CONNECTION & UTILITIES
# ────────────────────────────────────────────────
conn = st.connection("gsheets", type=GSheetsConnection)

def get_p_img(name, player_list):
    placeholder = 'https://www.gravatar.com/avatar/000?d=mp'
    if name in ["BYE", "TBD", None, '']: return placeholder
    for p in player_list:
        if isinstance(p, dict) and p.get('name') == name:
            url = p.get('img', '')
            if not url: return placeholder
            if "drive.google.com" in url:
                match = re.search(r'/d/([^/]+)', url)
                if match: return f"https://drive.google.com/uc?export=view&id={match.group(1)}"
            return url
    return placeholder

def load_db():
    try:
        df = conn.read(ttl=0)
        return df if df is not None and not df.empty else pd.DataFrame(columns=["Tournament", "Data"])
    except:
        return pd.DataFrame(columns=["Tournament", "Data"])

def save_db(df):
    try:
        conn.update(data=df)
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"Save failed: {str(e)}")
        return False

# ────────────────────────────────────────────────
# GENERATORS (unchanged)
# ────────────────────────────────────────────────
def generate_bracket(participants):
    names = [p['name'] if isinstance(p, dict) else str(p) for p in participants]
    names = [n.strip() for n in names if n.strip()]
    random.shuffle(names)
    if not names: return None
    next_pow_2 = 2 ** math.ceil(math.log2(max(len(names), 1)))
    full_slots = names + (["BYE"] * (next_pow_2 - len(names)))
    return [full_slots[i:i+2] for i in range(0, len(full_slots), 2)]

def generate_round_robin(participants):
    names = [p['name'] if isinstance(p, dict) else str(p) for p in participants]
    names = [n.strip() for n in names if n.strip()]
    if len(names) < 2: return None
    if len(names) % 2 != 0: names.append("BYE")
    n, rounds = len(names), []
    p_list = names[:]
    for i in range(n - 1):
        rounds.append([[p_list[j], p_list[n - 1 - j]] for j in range(n // 2)])
        p_list = [p_list[0]] + [p_list[-1]] + p_list[1:-1]
    return rounds

def generate_groups(participants):
    names = [p['name'] if isinstance(p, dict) else str(p) for p in participants]
    names = [n.strip() for n in names if n.strip()]
    if len(names) < 4: return None
    random.shuffle(names)
    num_groups = 4 if len(names) >= 16 else 2 if len(names) >= 8 else max(1, len(names) // 3)
    groups = [names[i::num_groups] for i in range(num_groups)]
    group_matches = [generate_round_robin(g) for g in groups]
    return {"groups": groups, "group_matches": group_matches, "knockout": None}

# ────────────────────────────────────────────────
# MAIN APP – START WITH TOURNAMENT SELECTION
# ────────────────────────────────────────────────
st.title("🎾 Tennis Tournament Organiser")

df_db = load_db()
tournament_list = df_db["Tournament"].dropna().unique().tolist()

# ── Tournament selection (first thing user sees) ──
if not tournament_list:
    st.warning("No tournaments found in Google Sheets yet.")
    st.stop()

# Auto-select if only one tournament
default_t = tournament_list[0] if len(tournament_list) == 1 else None

selected_t = st.selectbox(
    "Select a tournament to view",
    options=tournament_list,
    index=0 if default_t is None else tournament_list.index(default_t),
    key="tournament_select"
)

if selected_t:
    row = df_db[df_db["Tournament"] == selected_t]
    if not row.empty:
        t_data = ast.literal_eval(row["Data"].values[0])

        # Ensure keys exist
        defaults = {
            "scores": {}, "winners": {}, "players": [], "courts": ["Court 1"],
            "format": "Single Elimination", "locked": False, "bracket": None,
            "admin_password": ""
        }
        for k, v in defaults.items():
            t_data.setdefault(k, v)

        if t_data.get("players") and isinstance(t_data["players"][0], str):
            t_data["players"] = [{"name": p, "img": ""} for p in t_data["players"]]

        # ── Sidebar: Admin access only ──
        with st.sidebar:
            st.header("Admin Controls")
            correct_pw = t_data.get("admin_password", "")
            if "setup_authorized" not in st.session_state:
                st.session_state.setup_authorized = False

            if not st.session_state.setup_authorized:
                entered_pw = st.text_input("Admin Password", type="password", key=f"pw_sidebar_{selected_t}")
                if st.button("Login as Admin"):
                    if entered_pw == correct_pw:
                        st.session_state.setup_authorized = True
                        st.success("Admin access granted")
                        st.rerun()
                    else:
                        st.error("Incorrect password")
            else:
                st.success("Admin mode active")
                if st.button("Logout Admin"):
                    st.session_state.setup_authorized = False
                    st.rerun()

                # Setup section – only visible to admin
                st.subheader("Setup (Admin)")
                t_data["locked"] = st.toggle("Lock tournament", value=t_data.get("locked", False))

                st.markdown("### Format")
                format_options = ["Single Elimination", "Round Robin", "Double Elimination", "Group Stage + Knockout"]
                fmt = st.radio(
                    "Change format",
                    options=format_options,
                    index=format_options.index(t_data.get("format", "Single Elimination")),
                    horizontal=True,
                    disabled=t_data["locked"],
                    key=f"admin_format_{selected_t}"
                )
                t_data["format"] = fmt

                st.markdown("### Participants")
                t_data["players"] = st.data_editor(
                    t_data["players"],
                    num_rows="dynamic",
                    disabled=t_data["locked"],
                    column_config={
                        "img": st.column_config.ImageColumn("Photo"),
                        "name": st.column_config.TextColumn("Name", required=True)
                    }
                )

                st.markdown("### Courts")
                t_data["courts"] = st.data_editor(
                    t_data["courts"],
                    num_rows="dynamic",
                    disabled=t_data["locked"]
                )

                if st.button("🚀 Generate / Regenerate Bracket", type="primary", disabled=t_data["locked"]):
                    if t_data["format"] == "Single Elimination":
                        t_data["bracket"] = generate_bracket(t_data["players"])
                    elif t_data["format"] == "Round Robin":
                        t_data["bracket"] = generate_round_robin(t_data["players"])
                    elif t_data["format"] == "Double Elimination":
                        t_data["bracket"] = {"winner": generate_bracket(t_data["players"]), "loser": []}
                    elif t_data["format"] == "Group Stage + Knockout":
                        t_data["bracket"] = generate_groups(t_data["players"])

                    t_data["winners"] = {}
                    t_data["scores"] = {}

                    df_db.loc[df_db["Tournament"] == selected_t, "Data"] = str(t_data)
                    if save_db(df_db):
                        df_db = load_db()
                        st.success("Bracket generated!")
                        st.balloons()
                        st.rerun()

        # ── Main tabs: Progress first, then Order of Play ──
        tab_progress, tab_order = st.tabs(["📊 PROGRESS", "📅 ORDER OF PLAY"])

        # ────── PROGRESS (default view) ──────
        with tab_progress:
            st.subheader(f"Progress: {selected_t}")

            if t_data.get("bracket") is None:
                st.info("No bracket generated yet. (Admin can create one via sidebar)")
            else:
                # Your existing progress logic here (wins, leaderboard, rounds, champion, save button)
                # ... (insert your full progress code from earlier versions)
                # For brevity, assuming you have it working – keep your match cards, leaderboard, etc.
                pass

        # ────── ORDER OF PLAY ──────
        with tab_order:
            st.subheader(f"Order of Play: {selected_t}")

            if t_data.get("bracket") is None:
                st.info("No bracket generated yet.")
            else:
                # Your existing order of play code (cleaned layout with match cards)
                # ... (insert your full order of play code)
                pass
