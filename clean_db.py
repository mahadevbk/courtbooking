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
    
    try:
        # Group configuration
        special_villas = [("229", "Mira 1"), ("231", "Mira 1"), ("233", "Mira 1")]
        # (Villa, Community, Assigned Weekdays)
        # Weekdays: 0=Mon, 2=Wed, 4=Fri, 1=Tue, 3=Thu, 5=Sat, 6=Sun
        assignments = [
            ("229", "Mira 1", [0, 2, 4]), 
            ("231", "Mira 1", [1, 3, 5]), 
            ("233", "Mira 1", [6])
        ]
        preferred_courts = ["Mira Oasis 3A", "Mira 5B"]
        
        today = get_today()
        today_str = today.strftime('%Y-%m-%d')
        
        # 1. Fetch ALL bookings for this group to manage the shared calendar
        group_villas = [v[0] for v in special_villas]
        group_res = run_query(supabase, 
            supabase.table("bookings").select("date")
            .in_("villa", group_villas)
            .eq("sub_community", "Mira 1")
            .gte("date", today_str)
        )
        booked_dates = set(b['date'] for b in group_res.data) if group_res and group_res.data else set()

        # 2. Iterate through the next 14 days STARTING FROM THE FUTURE
        # This ensures Friday Mar 13 is prioritized over earlier dates if slots are tight
        for j in reversed(range(15)):
            target_date = today + timedelta(days=j)
            date_str = target_date.strftime('%Y-%m-%d')
            
            # If the date is already booked by ANY villa in the group, move to next day
            if date_str in booked_dates:
                continue
                
            # Find which villa is responsible for this weekday
            responsible_villa = None
            for villa_id, comm, weekdays in assignments:
                if target_date.weekday() in weekdays:
                    responsible_villa = (villa_id, comm)
                    break
            
            if not responsible_villa:
                continue
                
            v_num, v_comm = responsible_villa
            
            # Check if THIS specific villa has space (Limit 6)
            current_count = get_active_bookings_count(supabase, v_num, v_comm)
            if current_count >= 6:
                continue # This villa is full, skip this day
                
            # Attempt to book
            # Alternate court preference based on day to vary locations
            shuffled_preferred = preferred_courts if target_date.day % 2 == 0 else preferred_courts[::-1]
            other_courts = [c for c in courts if c not in shuffled_preferred]
            random.shuffle(other_courts)
            search_order = shuffled_preferred + other_courts
            
            for court in search_order:
                # Check 17:00 and 18:00 (5pm-7pm)
                if not is_slot_in_past(date_str, 17) and not is_slot_booked(supabase, court, date_str, 17) and \
                   not is_slot_in_past(date_str, 18) and not is_slot_booked(supabase, court, date_str, 18):
                    try:
                        run_query(supabase, supabase.table("bookings").insert([
                            {"villa": v_num, "sub_community": v_comm, "court": court, "date": date_str, "start_hour": 17},
                            {"villa": v_num, "sub_community": v_comm, "court": court, "date": date_str, "start_hour": 18}
                        ]))
                        add_log(supabase, "Booking Created", f"{v_comm} Villa {v_num} auto-booked {court} 17-19:00 for {date_str}")
                        booked_dates.add(date_str)
                        break # Successfully booked this day, move to next day in loop
                    except:
                        continue
        
        st.session_state['background_tasks_run'] = True
        
    except Exception:
        pass
