#!/usr/bin/env python3
"""
Fast script wrapper for scripts/train_search.py.

Looks up Israeli Railways departures (3 routes around the requested time:
before / target / after) and sends the result as a Hebrew Telegram reply.

Usage (called by the dispatcher when an Option D train request is detected):
    python fast_train_search.py \
        --from "תל אביב סבידור" \
        --to "ירושלים יצחק נבון" \
        [--date YYYY-MM-DD]   # default: today (Jerusalem)
        [--time HH:MM]        # default: now (Jerusalem)
        [--type departure|arrival]   # default: departure
        --ack_id N --orig_id N --chat_id N
"""
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "D:/Kai/scripts/fast")
from _base import base_args, finish, fail, action_log, RLM


IL_TZ = timezone(timedelta(hours=3))


def main():
    p = base_args("Search Israel Railways trains")
    p.add_argument("--from", dest="from_station", required=True)
    p.add_argument("--to", dest="to_station", required=True)
    p.add_argument("--date", default=None)
    p.add_argument("--time", default=None)
    p.add_argument("--type", default="departure", choices=["departure", "arrival"])
    args = p.parse_args()

    now = datetime.now(IL_TZ)
    date = args.date or now.strftime("%Y-%m-%d")
    time = args.time or now.strftime("%H:%M")

    # Call train_search.py
    try:
        proc = subprocess.run(
            [
                sys.executable, "D:/Kai/scripts/train_search.py",
                "--from", args.from_station,
                "--to", args.to_station,
                "--date", date,
                "--time", time,
                "--type", args.type,
            ],
            capture_output=True, text=True, encoding="utf-8", timeout=30
        )
    except subprocess.TimeoutExpired:
        return fail(args, "חיפוש הרכבת לקח יותר מ-30 שניות. נסה שוב.")
    except Exception as e:
        return fail(args, f"שגיאה בקריאת train_search.py: {e}")

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:200]
        return fail(args, f"train_search נכשל: {err}")

    try:
        data = json.loads(proc.stdout)
    except Exception as e:
        return fail(args, f"לא הצלחתי לפרסר JSON: {e}")

    if isinstance(data, dict) and data.get("status") == "error":
        return fail(args, data.get("message") or "שגיאה לא ידועה")

    routes = data.get("routes") if isinstance(data, dict) else data
    if not routes:
        return fail(args, "לא נמצאו רכבות בטווח הזמן הזה.")

    # Prefer canonical full station names returned by train_search.py
    # (falls back to the user-supplied input if for some reason the resolver
    # didn't include them).
    from_name = (data.get("from") or {}).get("name") if isinstance(data, dict) else None
    to_name = (data.get("to") or {}).get("name") if isinstance(data, dict) else None
    from_display = from_name or args.from_station
    to_display = to_name or args.to_station

    # Format the message
    lines = [f"{RLM}🚆 רכבת {from_display} ← {to_display}"]
    try:
        date_display = datetime.strptime(date, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        date_display = date
    lines.append(f"{RLM}📅 {date_display}, יציאה סביב {time}")
    lines.append(f"{RLM}")

    POSITION_LABEL = {
        "before": "⏮️ קודם",
        "target": "🎯 הקרוב ביותר",
        "after": "⏭️ הבא",
    }

    for route in routes:
        position = route.get("position", "")
        legs = route.get("legs", []) or []
        if not legs:
            continue

        label = POSITION_LABEL.get(position, position)
        lines.append(f"{RLM}*{label}*")

        # First leg's departure, last leg's arrival = full trip
        first = legs[0]
        last = legs[-1]
        dep = first.get("dep_time", "?")
        arr = last.get("arr_time", "?")
        plat_dep = first.get("platform")
        plat_arr = last.get("dest_platform")

        # Compute total minutes
        try:
            d_h, d_m = map(int, dep.split(":"))
            a_h, a_m = map(int, arr.split(":"))
            total_min = (a_h * 60 + a_m) - (d_h * 60 + d_m)
            if total_min < 0:
                total_min += 24 * 60
            duration = f"{total_min} דק'"
        except Exception:
            duration = ""

        plat_text = ""
        if plat_dep:
            plat_text = f", רציף {plat_dep}"
            if plat_arr and plat_arr != plat_dep:
                plat_text += f" ← {plat_arr}"

        n_legs = len(legs)
        legs_text = "ישירה" if n_legs == 1 else f"{n_legs - 1} החלפות"

        # Train number(s): for direct trains show one; for multi-leg show all chained
        train_nums = [str(l.get("train_number", "")).strip() for l in legs]
        train_nums = [tn for tn in train_nums if tn]
        if n_legs == 1 and train_nums:
            train_suffix = f", רכבת #{train_nums[0]}"
        elif train_nums:
            train_suffix = f", רכבות #{'+#'.join(train_nums)}"
        else:
            train_suffix = ""

        lines.append(f"{RLM}• {dep} ← {arr} ({duration}, {legs_text}{plat_text}{train_suffix})")

        if n_legs > 1:
            for i, leg in enumerate(legs):
                tn = str(leg.get("train_number", "")).strip()
                tn_text = f" [#{tn}]" if tn else ""
                lines.append(
                    f"{RLM}  {i+1}. {leg.get('dep_station','?')} ({leg.get('dep_time','?')}) "
                    f"← {leg.get('arr_station','?')} ({leg.get('arr_time','?')}){tn_text}"
                )
        lines.append(f"{RLM}")

    # Trim trailing blank
    while lines and lines[-1].strip() == RLM.strip():
        lines.pop()

    text = "\n".join(lines)
    action_log("dispatcher", "train_search", f"{args.from_station} -> {args.to_station} @ {date} {time}")
    finish(args, text)


if __name__ == "__main__":
    main()
