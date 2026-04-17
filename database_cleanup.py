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
            if e.code == "23505": raise e
            if attempt == max_retries - 1: raise e
            time.sleep(0.5)
        except Exception as e:
            if attempt == max_retries - 1: return None
            time.sleep(0.5)

def add_log(supabase, event_type, details):
    timestamp = get_utc_plus_4().isoformat()
    try:
        supabase.table("logs").insert({"timestamp": timestamp, "event_type": event_type, "details": details}).execute()
    except: pass

def check_global_lock(supabase):
    cutoff = (get_utc_plus_4() - timedelta(minutes=2)).isoformat()
    try:
        res = supabase.table("logs").select("timestamp").eq("event_type", "System Maintenance").gte("timestamp", cutoff).limit(1).execute()
        return len(res.data) > 0
    except: return False

def enforce_active_limits(supabase):
    """Enforces the 6-active-booking limit globally. Keeps the earliest 6 and deletes the rest."""
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    
    # 1. Fetch all active bookings
    # A booking is active if date > today OR (date == today AND start_hour >= now_hour)
    res = run_query(supabase, supabase.table("bookings").select("*").gte("date", today_str))
    if not res or not res.data: return

    # Filter to truly active ones (handling today's hours)
    active_bookings = [
        b for b in res.data 
        if b['date'] > today_str or int(b['start_hour']) >= now_hour
    ]

    # 2. Group by villa
    villa_map = {} # (sub_community, villa) -> [bookings]
    for b in active_bookings:
        key = (b['sub_community'], b['villa'])
        if key not in villa_map: villa_map[key] = []
        villa_map[key].append(b)

    # 3. Check and prune
    for (sc, v), bookings in villa_map.items():
        if len(bookings) > 6:
            # Sort chronologically: date then start_hour
            bookings.sort(key=lambda x: (x['date'], int(x['start_hour'])))
            
            # Keep first 6, delete the rest
            excess = bookings[6:]
            excess_ids = [b['id'] for b in excess]
            
            if excess_ids:
                try:
                    supabase.table("bookings").delete().in_("id", excess_ids).execute()
                    add_log(supabase, "Limit Enforcement", f"Deleted {len(excess_ids)} excess bookings for {sc} Villa {v} (Max 6 limit).")
                except: pass

def run_db_cleanup(supabase, courts):
    if st.session_state.get('background_tasks_run', False): return
    st.session_state['background_tasks_run'] = True
    if check_global_lock(supabase): return

    try:
        # First, enforce limits on existing bookings
        enforce_active_limits(supabase)
        
        add_log(supabase, "System Maintenance", "Database sync triggered.")
        special_villas = [("229", "Mira 1"), ("231", "Mira 1"), ("233", "Mira 1")]
        preferred_courts = ["Mira Oasis 3A", "Mira 5B"]
        today = get_today()
        today_str = today.strftime('%Y-%m-%d')
        group_villa_nums = [v[0] for v in special_villas]

        # 1. Fetch all bookings for availability and group status
        all_res = run_query(supabase, supabase.table("bookings").select("*").gte("date", today_str))
        if not all_res: return
        
        all_data = all_res.data
        now_hour = get_utc_plus_4().hour

        # Pre-calculate active counts and daily group occupancy
        villa_active_slots = {v: 0 for v in group_villa_nums}
        group_daily_occupied = {} # date -> boolean (if group has 19:00 booking)

        for b in all_data:
            b_v = str(b['villa'])
            b_sc = b['sub_community']
            b_date = b['date']
            b_hour = int(b['start_hour'])

            # Count active slots for our special villas
            if b_sc == "Mira 1" and b_v in group_villa_nums:
                if b_date > today_str or b_hour >= now_hour:
                    villa_active_slots[b_v] += 1
                
                # Rule: One villa from the group can book per day (any time).
                # If ANY booking exists for ANY villa in the group on this day, skip it.
                group_daily_occupied[b_date] = True

        # 2. Chronological fill loop
        for j in range(15):
            target_date = today + timedelta(days=j)
            date_str = target_date.strftime('%Y-%m-%d')
            
            # 1. Skip if already occupied
            if group_daily_occupied.get(date_str):
                continue
                
            # 2. Skip today if 19:00 has already passed
            if date_str == today_str and now_hour >= 19:
                continue

            random_villas = list(special_villas)
            random.shuffle(random_villas)
            
            success = False
            for v_num, sub_comm in random_villas:
                # Per-villa limit 6 slots
                if villa_active_slots[v_num] + 2 > 6:
                    continue

                # Sort courts to prioritize preferred ones
                sorted_courts = []
                # First, add available preferred courts
                for pc in preferred_courts:
                    if pc in courts: sorted_courts.append(pc)
                # Then add the rest in random order
                other_courts = [c for c in courts if c not in preferred_courts]
                random.shuffle(other_courts)
                sorted_courts.extend(other_courts)
                
                for court in sorted_courts:
                    # Check if slot is free for EVERYONE
                    is_19_free = not any(b for b in all_data if b['court'] == court and b['date'] == date_str and b['start_hour'] == 19)
                    is_20_free = not any(b for b in all_data if b['court'] == court and b['date'] == date_str and b['start_hour'] == 20)
                    
                    if is_19_free and is_20_free:
                        try:
                            # Attempt to book
                            res = supabase.table("bookings").insert([
                                {"villa": v_num, "sub_community": sub_comm, "court": court, "date": date_str, "start_hour": 19},
                                {"villa": v_num, "sub_community": sub_comm, "court": court, "date": date_str, "start_hour": 20}
                            ]).execute()
                            
                            if res.data:
                                add_log(supabase, "Booking Created", f"{sub_comm} Villa {v_num} System-Synced {court} for {date_str} at 19:00")
                                # Update local state to prevent double-booking same day/villa
                                group_daily_occupied[date_str] = True
                                villa_active_slots[v_num] += 2
                                # Add to all_data so next 'j' sees it
                                all_data.append({"court": court, "date": date_str, "start_hour": 19})
                                all_data.append({"court": court, "date": date_str, "start_hour": 20})
                                success = True
                                break
                        except: continue
                if success: break
    except Exception: pass
