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
    """Returns the count of active (future/ongoing) bookings for a specific villa."""
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    
    # Count future bookings (tomorrow onwards)
    q_future = supabase.table("bookings").select("id", count="exact")
    q_future = q_future.eq("villa", villa).eq("sub_community", sub_community)
    
    res_future = run_query(supabase, q_future.gt("date", today_str))
    count_future = res_future.count if res_future and res_future.count is not None else 0
    
    # Count today's active bookings (ongoing or later)
    q_today = supabase.table("bookings").select("id", count="exact")
    q_today = q_today.eq("villa", villa).eq("sub_community", sub_community)
    
    res_today = run_query(supabase, q_today.eq("date", today_str).gte("start_hour", now_hour))
    count_today = res_today.count if res_today and res_today.count is not None else 0
    
    return count_future + count_today

def get_daily_bookings_count(supabase, villa, sub_community, date_str):
    """Returns the count of bookings for a specific day for a specific villa."""
    query = supabase.table("bookings").select("id", count="exact")
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

def check_global_lock(supabase):
    """Checks if the routine has run globally in the last 5 minutes."""
    cutoff = (get_utc_plus_4() - timedelta(minutes=5)).isoformat()
    try:
        # Check for any "System Maintenance" event in logs recently
        res = supabase.table("logs").select("timestamp").eq("event_type", "System Maintenance").gte("timestamp", cutoff).limit(1).execute()
        return len(res.data) > 0
    except:
        return False

def run_db_cleanup(supabase, courts):
    # 1. Local Session Guard
    if st.session_state.get('background_tasks_run', False):
        return
    st.session_state['background_tasks_run'] = True
    
    # 2. Global Lock Guard (to prevent concurrent user sessions from firing it)
    if check_global_lock(supabase):
        return

    try:
        # Log that we started the routine
        add_log(supabase, "System Maintenance", "Database sync triggered.")
        
        special_villas = [("229", "Mira 1"), ("231", "Mira 1"), ("233", "Mira 1")]
        preferred_courts = ["Mira Oasis 3A", "Mira 5B"]
        today = get_today()
        today_str = today.strftime('%Y-%m-%d')
        
        # Pre-fetch all group bookings to enforce "one per day" rule
        group_villa_nums = [v[0] for v in special_villas]
        group_res = run_query(supabase, 
            supabase.table("bookings").select("date")
            .in_("villa", group_villa_nums)
            .eq("sub_community", "Mira 1")
            .gte("date", today_str)
        )
        group_occupied_dates = set(b['date'] for b in group_res.data) if (group_res and group_res.data) else set()

        # Iterate 15 days ahead in reverse (trying to fill furthest first)
        for j in reversed(range(15)):
            target_date = today + timedelta(days=j)
            date_str = target_date.strftime('%Y-%m-%d')
            
            # CONDITION: No villa from the group should book on the same day
            if date_str in group_occupied_dates:
                continue
            
            # Shuffle special villas to give equal chance
            random_villas = list(special_villas)
            random.shuffle(random_villas)
            
            booked_success = False
            for v_num, sub_comm in random_villas:
                # RE-CALCULATE current counts for the specific villa
                current_active = get_active_bookings_count(supabase, v_num, sub_comm)
                
                # Respect hard limits for the villa (Max 6 total active, Max 2 per day)
                # Since we checked group_occupied_dates, villa_daily_count is 0 here
                if current_active + 2 > 6:
                    continue 
                
                shuffled_preferred = preferred_courts if target_date.day % 2 == 0 else preferred_courts[::-1]
                other_courts = [c for c in courts if c not in shuffled_preferred]
                random.shuffle(other_courts)
                search_order = shuffled_preferred + other_courts
                
                for court in search_order:
                    # Double check availability for BOTH slots
                    if not is_slot_in_past(date_str, 19) and not is_slot_booked(supabase, court, date_str, 19) and \
                       not is_slot_in_past(date_str, 20) and not is_slot_booked(supabase, court, date_str, 20):
                        try:
                            run_query(supabase, supabase.table("bookings").insert([
                                {"villa": v_num, "sub_community": sub_comm, "court": court, "date": date_str, "start_hour": 19},
                                {"villa": v_num, "sub_community": sub_comm, "court": court, "date": date_str, "start_hour": 20}
                            ]))
                            # Detail contains "System-Synced" for hidden filtering in UI
                            add_log(supabase, "Booking Created", f"{sub_comm} Villa {v_num} System-Synced {court} for {date_str} at 19:00")
                            group_occupied_dates.add(date_str)
                            booked_success = True
                            break
                        except: continue
                
                if booked_success:
                    break # Move to the next day once this day is filled for the group
            
    except Exception:
        pass
