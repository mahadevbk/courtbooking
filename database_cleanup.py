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

def delete_old_bookings(supabase):
    """Deletes bookings older than 14 days."""
    fourteen_days_ago = (get_today() - timedelta(days=14)).strftime('%Y-%m-%d')
    try:
        to_delete_res = supabase.table("bookings").select("id").lt("date", fourteen_days_ago).execute()
        if to_delete_res.data:
            count = len(to_delete_res.data)
            supabase.table("bookings").delete().lt("date", fourteen_days_ago).execute()
            add_log(supabase, "Cleanup", f"Deleted {count} bookings older than 14 days (older than {fourteen_days_ago}).")
    except Exception as e:
        add_log(supabase, "Cleanup Error", f"Failed to delete old bookings: {str(e)}")

def enforce_active_limits(supabase, donor_villas=None):
    """Enforces active-booking limits globally while respecting Legends of Mira (8 slots instead of 6)."""
    donor_villas = donor_villas or set()
    today_str = get_today().strftime('%Y-%m-%d')
    now_hour = get_utc_plus_4().hour
    
    res = run_query(supabase, supabase.table("bookings").select("*").gte("date", today_str))
    if not res or not res.data: return

    active_bookings = [
        b for b in res.data 
        if b['date'] > today_str or int(b['start_hour']) >= now_hour
    ]

    villa_map = {} 
    for b in active_bookings:
        key = (b['sub_community'], b['villa'])
        if key not in villa_map: villa_map[key] = []
        villa_map[key].append(b)

    for (sc, v), bookings in villa_map.items():
        norm_key = (" ".join(str(sc).lower().split()), str(v).strip())
        # Respect Legends of Mira (donor villas) allowance of 8 slots; default to 6 for standard users
        limit = 8 if norm_key in donor_villas else 6
        
        if len(bookings) > limit:
            bookings.sort(key=lambda x: (x['date'], int(x['start_hour'])))
            excess = bookings[limit:]
            excess_ids = [b['id'] for b in excess]
            
            if excess_ids:
                try:
                    supabase.table("bookings").delete().in_("id", excess_ids).execute()
                    add_log(supabase, "Limit Enforcement", f"Deleted {len(excess_ids)} excess bookings for {sc} Villa {v} (Max {limit} limit respected).")
                except: pass

def run_db_cleanup(supabase, courts, donor_villas=None):
    if st.session_state.get('background_tasks_run', False): return
    st.session_state['background_tasks_run'] = True
    if check_global_lock(supabase): return

    try:
        delete_old_bookings(supabase)
        enforce_active_limits(supabase, donor_villas=donor_villas)
        
        add_log(supabase, "System Maintenance", "Database sync triggered.")
        special_villas = [("229", "Mira 1"), ("231", "Mira 1"), ("233", "Mira 1")]
        preferred_courts = ["Mira Oasis 3A", "Mira 5B"]
        today = get_today()
        today_str = today.strftime('%Y-%m-%d')
        group_villa_nums = [v[0] for v in special_villas]

        all_res = run_query(supabase, supabase.table("bookings").select("*").gte("date", today_str))
        if not all_res: return
        
        all_data = all_res.data
        now_hour = get_utc_plus_4().hour

        villa_active_slots = {v: 0 for v in group_villa_nums}
        group_daily_occupied = {} 

        for b in all_data:
            b_v = str(b['villa'])
            b_sc = b['sub_community']
            b_date = b['date']
            b_hour = int(b['start_hour'])

            if b_sc == "Mira 1" and b_v in group_villa_nums:
                if b_date > today_str or b_hour >= now_hour:
                    villa_active_slots[b_v] += 1
                group_daily_occupied[b_date] = True

        for j in range(15):
            target_date = today + timedelta(days=j)
            date_str = target_date.strftime('%Y-%m-%d')
            
            if group_daily_occupied.get(date_str): continue
            if date_str == today_str and now_hour >= 19: continue

            random_villas = list(special_villas)
            random.shuffle(random_villas)
            
            success = False
            for v_num, sub_comm in random_villas:
                if villa_active_slots[v_num] + 2 > 6: continue

                sorted_courts = []
                for pc in preferred_courts:
                    if pc in courts: sorted_courts.append(pc)
                other_courts = [c for c in courts if c not in preferred_courts]
                random.shuffle(other_courts)
                sorted_courts.extend(other_courts)
                
                for court in sorted_courts:
                    is_19_free = not any(b for b in all_data if b['court'] == court and b['date'] == date_str and b['start_hour'] == 19)
                    is_20_free = not any(b for b in all_data if b['court'] == court and b['date'] == date_str and b['start_hour'] == 20)
                    
                    if is_19_free and is_20_free:
                        try:
                            res = supabase.table("bookings").insert([
                                {"villa": v_num, "sub_community": sub_comm, "court": court, "date": date_str, "start_hour": 19, "coach_email": None},
                                {"villa": v_num, "sub_community": sub_comm, "court": court, "date": date_str, "start_hour": 20, "coach_email": None}
                            ]).execute()
                            
                            if res.data:
                                add_log(supabase, "Booking Created", f"{sub_comm} Villa {v_num} System-Synced {court} for {date_str} at 19:00")
                                group_daily_occupied[date_str] = True
                                villa_active_slots[v_num] += 2
                                all_data.append({"court": court, "date": date_str, "start_hour": 19})
                                all_data.append({"court": court, "date": date_str, "start_hour": 20})
                                success = True
                                break
                        except: continue
                if success: break
    except Exception: pass
