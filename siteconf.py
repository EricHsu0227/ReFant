"""Shared helpers for reading site.json."""
import datetime as dt


def current_season():
    t = dt.date.today()
    return t.year if (t.month, t.day) >= (3, 15) else t.year - 1


def years(spec):
    """'2015-now', 2019, 'now', or a list of those -> sorted list of years."""
    if spec is None:
        return []
    if not isinstance(spec, list):
        spec = [spec]
    out = set()
    for item in spec:
        item = str(item).strip().lower().replace("now", str(current_season()))
        if "-" in item:
            a, b = item.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif item:
            out.add(int(item))
    return sorted(y for y in out if 2008 <= y <= dt.date.today().year)
