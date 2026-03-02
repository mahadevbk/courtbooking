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
    
    query = supabase.table("bookings").select("id", count="exact")
    if sub_community == "Mira 1" and villa in ["229", "231", "233"]:
        query = query.in_("villa", ["229", "231", "233"]).eq("sub_community", "Mira 1")
    else:
        query = query.eq("villa", villa).eq("sub_community", sub_community)
    
    response = run_query(supabase, query.or_(f"date.gt.{today_str},and(date.eq.{today_str},start_hour.gte.{now_hour})"))
    if response is None or response.count is None:
        return 99
    return response.count

def get_daily_bookings_count(supabase, villa, sub_community, date_str):
    query = supabase.table("bookings").select("id", count="exact")
    if sub_community == "Mira 1" and villa in ["229", "231", "233"]:
        query = query.in_("villa", ["229", "231", "233"]).eq("sub_community", "Mira 1")
    else:
        query = query.eq("villa", villa).eq("sub_community", sub_community)
    
    response = run_query(supabase, query.eq("date", date_str))
    if response is None or response.count is None:
        return 99
    return response.count

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
    try:
        supabase.table("logs").insert({
            "timestamp": timestamp,
            "event_type": event_type,
            "details": details
        }).execute()
    except:
        pass

def run_db_cleanup(supabase, courts):
    if st.session_state.get('background_tasks_run', False):
        return
    
    try:
        special_villas = [("229", "Mira 1"), ("231", "Mira 1"), ("233", "Mira 1")]
        assignments = {
            0: "229", 2: "229", 4: "229", 
            1: "231", 3: "231", 5: "231", 
            6: "233"
        }
        preferred_courts = ["Mira Oasis 3A", "Mira 5B"]
        today = get_today()
        today_str = today.strftime('%Y-%m-%d')
        
        group_villas = [v[0] for v in special_villas]
        group_res = run_query(supabase, 
            supabase.table("bookings").select("date")
            .in_("villa", group_villas)
            .eq("sub_community", "Mira 1")
            .gte("date", today_str)
        )
        
        if group_res is None:
            return

        booked_dates = set(b['date'] for b in group_res.data) if group_res.data else set()

        for j in reversed(range(15)):
            target_date = today + timedelta(days=j)
            date_str = target_date.strftime('%Y-%m-%d')
            
            if date_str in booked_dates:
                continue
            
            primary_v = assignments.get(target_date.weekday())
            others = [v[0] for v in special_villas if v[0] != primary_v]
            random.shuffle(others)
            candidates = ([primary_v] if primary_v else []) + others
            
            booked_success = False
            for v_num in candidates:
                current_active = get_active_bookings_count(supabase, v_num, "Mira 1")
                current_daily = get_daily_bookings_count(supabase, v_num, "Mira 1", date_str)
                
                # Each auto-booking adds 2 slots (19:00 and 20:00)
                if current_active + 2 > 6 or current_daily + 2 > 2:
                    continue 
                
                shuffled_preferred = preferred_courts if target_date.day % 2 == 0 else preferred_courts[::-1]
                other_courts = [c for c in courts if c not in shuffled_preferred]
                random.shuffle(other_courts)
                search_order = shuffled_preferred + other_courts
                
                for court in search_order:
                    # Check the slots we are about to book (19:00 and 20:00)
                    if not is_slot_in_past(date_str, 19) and not is_slot_booked(supabase, court, date_str, 19) and \
                       not is_slot_in_past(date_str, 20) and not is_slot_booked(supabase, court, date_str, 20):
                        try:
                            run_query(supabase, supabase.table("bookings").insert([
                                {"villa": v_num, "sub_community": "Mira 1", "court": court, "date": date_str, "start_hour": 19},
                                {"villa": v_num, "sub_community": "Mira 1", "court": court, "date": date_str, "start_hour": 20}
                            ]))
                            # Detail contains "auto-booked" for hidden filtering in UI
                            add_log(supabase, "Booking Created", f"Mira 1 Villa {v_num} auto-booked {court} for {date_str} at 19:00")
                            booked_dates.add(date_str)
                            booked_success = True
                            break
                        except: continue
                
                if booked_success:
                    break 

        st.session_state['background_tasks_run'] = True
    except Exception:
        pass
