from datetime import datetime


def validate_booking_payload(data):
    required = ["artist", "contact", "service", "mixer", "booking_date", "start_time", "end_time"]
    missing = [field for field in required if not data.get(field)]
    if missing:
        return False, f"Missing required fields: {', '.join(missing)}"

    if not data["artist"].strip():
        return False, "Artist name is required"
    if not data["contact"].strip():
        return False, "Contact is required"
    if not data["service"].strip():
        return False, "Service is required"
    if not data["mixer"].strip():
        return False, "Mixer is required"

    try:
        datetime.strptime(data["booking_date"], "%Y-%m-%d")
    except ValueError:
        return False, "booking_date must be YYYY-MM-DD"

    try:
        datetime.strptime(data["start_time"], "%H:%M")
        datetime.strptime(data["end_time"], "%H:%M")
    except ValueError:
        return False, "Times must be in HH:MM format"

    start_h, start_m = map(int, data["start_time"].split(":"))
    end_h, end_m = map(int, data["end_time"].split(":"))
    duration = (end_h * 60 + end_m) - (start_h * 60 + start_m)
    if duration <= 0:
        return False, "End time must be after start time"

    return True, "ok"
