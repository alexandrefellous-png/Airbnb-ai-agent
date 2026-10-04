from datetime import date, timedelta

class CalendarService:
    def __init__(self, guesty):
        self.guesty = guesty

    async def availability(self, listing_id, start, end):
        try:
            first, last = date.fromisoformat(start), date.fromisoformat(end)
        except (ValueError, TypeError):
            return "dates_incomplete"
        if last <= first or (last-first).days > 365:
            return "invalid_dates"
        data = await self.guesty.calendar(listing_id, start, end)
        # Official calendar-block-types guide documents data.days. Other explicit day collections fail closed if invalid.
        days = data if isinstance(data, list) else data.get("data", {}).get("days") if isinstance(data, dict) and isinstance(data.get("data"), dict) else None
        if days is None and isinstance(data, dict):
            days = data.get("days")
        if not isinstance(days, list):
            return "unknown"
        by_date = {}
        for day in days:
            if not isinstance(day, dict) or day.get("listingId") != listing_id or day.get("date") in by_date:
                return "unknown"
            by_date[day.get("date")] = day
        nights = (last-first).days
        for i in range(nights):
            day = by_date.get((first+timedelta(days=i)).isoformat())
            if not day:
                return "unknown"
            allotment = day.get("allotment")
            if isinstance(allotment, (int, float)) and not isinstance(allotment, bool):
                available = allotment > 0
            elif day.get("status") in {"available", "unavailable", "booked", "reserved"}:
                available = day["status"] == "available"
            else:
                return "unknown"
            if not available:
                return "unavailable"
        arrival, departure = by_date.get(start), by_date.get(end)
        if departure is None:
            return "unknown"
        if arrival.get("cta") or departure.get("ctd") or nights < arrival.get("minNights", 1):
            return "restricted"
        return "available"
