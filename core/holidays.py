from datetime import date, timedelta


def _easter(year: int) -> date:
    """Gregorian Easter — Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(114 + h + l - 7 * m, 31)
    return date(year, month, day + 1)


_FIXED = frozenset({
    (1, 1),   # Jour de l'An
    (5, 1),   # Fête du Travail
    (5, 8),   # Victoire 1945
    (7, 14),  # Fête Nationale
    (8, 15),  # Assomption
    (11, 1),  # Toussaint
    (11, 11), # Armistice
    (12, 25), # Noël
})


def is_french_holiday(d: date) -> bool:
    if (d.month, d.day) in _FIXED:
        return True
    easter = _easter(d.year)
    return d in {
        easter + timedelta(days=1),   # Lundi de Pâques
        easter + timedelta(days=39),  # Ascension
        easter + timedelta(days=50),  # Lundi de Pentecôte
    }


def is_sending_day(d: date) -> bool:
    """Returns False on weekends and French public holidays."""
    return d.weekday() < 5 and not is_french_holiday(d)
