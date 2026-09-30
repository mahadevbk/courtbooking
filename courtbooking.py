import streamlit as st

NEW_APP_URL = "https://miratennis.up.railway.app"

st.set_page_config(page_title="Mira Court Booking has moved", page_icon="🎾", layout="centered")

# Background colour and font are inherited from the app's theme; only layout and detailing are set here.
st.markdown(
    f"""
    <style>
      #MainMenu, header[data-testid="stHeader"], footer, [data-testid="stToolbar"] {{ display: none !important; }}
      .block-container {{ padding-top: 12vh !important; max-width: 560px !important; }}

      .mv-wrap    {{ font-family: inherit; text-align: center; }}
      .mv-eyebrow {{ font-size: 12px; letter-spacing: .28em; text-transform: uppercase; opacity: .6; margin-bottom: 26px; }}
      .mv-title   {{ font-size: 44px; line-height: 1.1; font-weight: 600; letter-spacing: -.01em; margin: 0 0 16px 0; }}
      .mv-lead    {{ font-size: 17px; line-height: 1.6; opacity: .78; margin: 0 auto 30px auto; max-width: 420px; }}

      .mv-steps   {{ text-align: left; padding: 8px 22px; border: 1px solid rgba(128,128,128,.35); border-radius: 14px; margin: 0 0 26px 0; }}
      .mv-step    {{ display: flex; align-items: flex-start; gap: 16px; padding: 14px 0; font-size: 15px; line-height: 1.5; }}
      .mv-step + .mv-step {{ border-top: 1px solid rgba(128,128,128,.25); }}
      .mv-num     {{ flex: 0 0 auto; width: 26px; height: 26px; border-radius: 50%; border: 1px solid rgba(128,128,128,.6);
                     display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 600; margin-top: 1px; }}
      .mv-step b  {{ font-weight: 600; }}

      a.mv-btn    {{ display: inline-flex; align-items: center; justify-content: center; gap: 10px; width: 100%;
                     box-sizing: border-box; padding: 16px 24px; border-radius: 12px; background: #1C2B24;
                     color: #FFFFFF !important; font-family: inherit; font-size: 16px; font-weight: 600;
                     letter-spacing: .02em; text-decoration: none !important; transition: background .2s, transform .2s; }}
      a.mv-btn:hover {{ background: #2C4237; transform: translateY(-1px); }}

      .mv-url     {{ margin-top: 18px; font-size: 13px; opacity: .55; letter-spacing: .02em; }}
    </style>

    <div class="mv-wrap">
      <div class="mv-eyebrow">Mira Court Booking</div>
      <h1 class="mv-title">We&rsquo;ve moved.</h1>
      <p class="mv-lead">This app is retired and old logins no longer work. Please use the new app for all bookings.</p>

      <div class="mv-steps">
        <div class="mv-step"><span class="mv-num">1</span><span>Verify your villa with a <b>current DEWA bill</b>.</span></div>
        <div class="mv-step"><span class="mv-num">2</span><span>Create a <b>new password</b>.</span></div>
        <div class="mv-step"><span class="mv-num">3</span><span>Your existing bookings have been migrated and will <b>appear automatically</b>.</span></div>
      </div>

      <a class="mv-btn" href="{NEW_APP_URL}" target="_blank" rel="noopener noreferrer">
        Go to the new app
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M5 12h14"></path><path d="M13 6l6 6-6 6"></path>
        </svg>
      </a>
      <div class="mv-url">{NEW_APP_URL.replace("https://", "")}</div>
    </div>
    """,
    unsafe_allow_html=True,
)
