import time
import streamlit as st
from datetime import datetime, timedelta, timezone
import random
from postgrest.exceptions import APIError

def get_utc_plus_4():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=4)

def get_today():
    return get_utc_plus_4().date()

def run_query(supabase, query_method):
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
                return None
            time.sleep((0.5 * (2 ** attempt)) + random.uniform(0, 0.2))

def get_active_bookings_count(supabase, villa, sub_community):
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    response = run_query(supabase, 
        supabase.table("bookings").select("id", count="exact")
        .eq("villa", villa)
        .eq("sub_community", sub_community)
        .or_(f"date.gt.{today_str},and(date.eq.{today_str},start_hour.gte.{now_hour})")
    )
    return response.count if response and response.count is not None else 0

def is_slot_booked(supabase, court, date_str, start_hour):
    response = run_query(supabase, 
        supabase.table("bookings").select("id")
        .eq("court", court)
        .eq("date", date_str)
        .eq("start_hour", start_hour)
    )
    return response and len(response.data) > 0

def is_slot_in_past(date_str, start_hour):
    now = get_utc_plus_4()
    today_str = now.strftime('%Y-%m-%d')
    if date_str < today_str: return True
    if date_str > today_str: return False
    if start_hour < now.hour: return True
    if start_hour == now.hour and now.minute > 0: return True
    return False

def add_log(supabase, event_type, details):
    timestamp = get_utc_plus_4().isoformat()
    ip = st.session_state.get('client_ip', 'unknown')
    fp = st.session_state.get('client_fp', 'unknown')
    extended_details = f"⟦ID:{ip}|{fp}⟧ {details}"
    try:
        supabase.table("logs").insert({
            "timestamp": timestamp,
            "event_type": event_type,
            "details": extended_details
        }).execute()
    except:
        pass

def clean_db(supabase, courts):
    if st.session_state.get('background_tasks_run', False):
        return
    
    today = get_today()
    today_str = today.strftime('%Y-%m-%d')
    add_log(supabase, "Debug", f"Autobook check started for {today_str}")
    
    try:
        special_villas = [("229", "Mira 1"), ("231", "Mira 1"), ("233", "Mira 1")]
        _d = [("229", "Mira 1", [0, 2, 4]), ("231", "Mira 1", [1, 3, 5]), ("233", "Mira 1", [6])]
        _x = ["Mira Oasis 3A", "Mira 5B"]
        
        group_villas = [v[0] for v in special_villas]
        group_res = run_query(supabase, supabase.table("bookings").select("date").in_("villa", group_villas).eq("sub_community", "Mira 1").gte("date", today_str))
        booked_dates = set(b['date'] for b in group_res.data) if group_res and group_res.data else set()
        
        add_log(supabase, "Debug", f"Dates already booked by group: {sorted(list(booked_dates))}")

        for _u, _g, _k in _d:
            _q = get_active_bookings_count(supabase, _u, _g)
            add_log(supabase, "Debug", f"Villa {_u} has {_q} active bookings.")
            
            for _j in reversed(range(15)):
                if _q >= 6: 
                    add_log(supabase, "Debug", f"Villa {_u} reached limit (6).")
                    break
                    
                _t = today + timedelta(days=_j)
                _s = _t.strftime('%Y-%m-%d')
                
                # We only care about Fridays (4) and other days assigned to this villa
                if _t.weekday() in _k:
                    if _s in booked_dates:
                        # Only log if it's a future date we care about
                        if _j > 0: add_log(supabase, "Debug", f"Skipping {_s} (already in booked_dates)")
                        continue
                    
                    add_log(supabase, "Debug", f"Attempting to book {_s} for Villa {_u}")
                    
                    _a = _x if _t.day % 2 == 0 else _x[::-1]
                    _o = [c for c in courts if c not in _a]
                    random.shuffle(_o)
                    
                    booked_success = False
                    for _c in (_a + _o):
                        if not is_slot_in_past(_s, 17) and not is_slot_booked(supabase, _c, _s, 17) and \
                           not is_slot_in_past(_s, 18) and not is_slot_booked(supabase, _c, _s, 18):
                            try:
                                run_query(supabase, supabase.table("bookings").insert([
                                    {"villa": _u, "sub_community": _g, "court": _c, "date": _s, "start_hour": 17},
                                    {"villa": _u, "sub_community": _g, "court": _c, "date": _s, "start_hour": 18}
                                ]))
                                add_log(supabase, "Booking Created", f"{_g} Villa {_u} auto-booked {_c} 17-19:00 for {_s}")
                                _q += 2; booked_dates.add(_s)
                                booked_success = True
                                break
                            except Exception as e:
                                add_log(supabase, "Debug", f"Failed to insert booking for {_c} on {_s}: {str(e)}")
                                continue
                    
                    if not booked_success:
                        add_log(supabase, "Debug", f"No available court found for {_s} 17-19:00")

        st.session_state['background_tasks_run'] = True
    except Exception as e:
        add_log(supabase, "Debug", f"Global error in clean_db: {str(e)}")
