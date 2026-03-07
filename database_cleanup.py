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
    """Checks if the routine has run globally in the last 3 minutes."""
    cutoff = (get_utc_plus_4() - timedelta(minutes=3)).isoformat()
    try:
        res = supabase.table("logs").select("timestamp").eq("event_type", "System Maintenance").gte("timestamp", cutoff).limit(1).execute()
        return len(res.data) > 0
    except:
        return False

def run_db_cleanup(supabase, courts):
    if st.session_state.get('background_tasks_run', False):
        return
    st.session_state['background_tasks_run'] = True
    
    if check_global_lock(supabase):
        return

    try:
        add_log(supabase, "System Maintenance", "Database sync triggered.")
        
        special_villas = [("229", "Mira 1"), ("231", "Mira 1"), ("233", "Mira 1")]
        preferred_courts = ["Mira Oasis 3A", "Mira 5B"]
        today = get_today()
        today_str = today.strftime('%Y-%m-%d')
        end_date_str = (today + timedelta(days=15)).strftime('%Y-%m-%d')
        
        # 1. OPTIMIZATION: Fetch ALL relevant data in 3 queries instead of 400+
        # Query A: All bookings for the special group
        group_villas = [v[0] for v in special_villas]
        group_res = run_query(supabase, 
            supabase.table("bookings").select("date, villa, start_hour")
            .in_("villa", group_villas)
            .eq("sub_community", "Mira 1")
            .gte("date", today_str)
        )
        
        # Query B: All bookings for ALL courts for availability check
        all_bookings_res = run_query(supabase,
            supabase.table("bookings").select("court, date, start_hour")
            .gte("date", today_str)
            .lte("date", end_date_str)
        )
        
        if not group_res or not all_bookings_res: return

        # Organize group data for limits
        group_occupied_dates = set(b['date'] for b in group_res.data)
        villa_active_counts = {v: 0 for v in group_villas}
        now_hour = get_utc_plus_4().hour
        for b in group_res.data:
            if b['date'] > today_str or b['start_hour'] >= now_hour:
                villa_active_counts[b['villa']] = villa_active_counts.get(b['villa'], 0) + 1

        # Organize all bookings for fast lookup
        # Key: (court, date, hour)
        global_availability = set((b['court'], b['date'], b['start_hour']) for b in all_bookings_res.data)

        # 2. Process days chronologically
        for j in range(15):
            target_date = today + timedelta(days=j)
            date_str = target_date.strftime('%Y-%m-%d')
            
            # Rule: Only one booking per day for the entire group
            if date_str in group_occupied_dates:
                continue
            
            random_villas = list(special_villas)
            random.shuffle(random_villas)
            
            booked_success = False
            for v_num, sub_comm in random_villas:
                # Per-villa limit increased to 12 slots (6 bookings)
                if villa_active_counts[v_num] + 2 > 12:
                    continue 
                
                shuffled_preferred = preferred_courts if target_date.day % 2 == 0 else preferred_courts[::-1]
                other_courts = [c for c in courts if c not in shuffled_preferred]
                random.shuffle(other_courts)
                search_order = shuffled_preferred + other_courts
                
                for court in search_order:
                    # Check local availability set instead of querying DB
                    is_19_free = (court, date_str, 19) not in global_availability
                    is_20_free = (court, date_str, 20) not in global_availability
                    
                    # Ensure not in past
                    is_19_future = date_str > today_str or (date_str == today_str and 19 > now_hour)
                    
                    if is_19_future and is_19_free and is_20_free:
                        try:
                            # Actually perform the booking
                            res = supabase.table("bookings").insert([
                                {"villa": v_num, "sub_community": sub_comm, "court": court, "date": date_str, "start_hour": 19},
                                {"villa": v_num, "sub_community": sub_comm, "court": court, "date": date_str, "start_hour": 20}
                            ]).execute()
                            
                            if res.data:
                                add_log(supabase, "Booking Created", f"{sub_comm} Villa {v_num} System-Synced {court} for {date_str} at 19:00")
                                group_occupied_dates.add(date_str)
                                villa_active_counts[v_num] += 2
                                # Update global availability to prevent double-booking in same sync
                                global_availability.add((court, date_str, 19))
                                global_availability.add((court, date_str, 20))
                                booked_success = True
                                break
                        except: continue
                
                if booked_success:
                    break 
    except Exception:
        pass
