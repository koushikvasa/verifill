"""
Lane: Backend
Cross-field logic checks. A field can be read perfectly and still be wrong.

TODO, each returns a Check:
- max_daily_dose: parse sig frequency (daily=1, BID=2, TID=3, QID=4, q6h=4, q8h=3,
  q12h=2, PRN -> use max frequency) x tabs x strength <= drugs.json max_daily_mg
- decimal_error: strength 10x or 0.1x a marketed strength (0.5 -> 0.05 levothyroxine)
- qty_days: quantity ~= daily tabs x days_supply (+-10%)
- strength_marketed: strength in drugs.json strengths_mg
- dates: date_written not in future, not > 1 year old
"""
