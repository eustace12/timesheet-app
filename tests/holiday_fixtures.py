"""Recorded public transport responses: tests must not depend on a live calendar."""
import io
from pathlib import Path
import re
import urllib.error

_FIXTURES = Path(__file__).parent / 'fixtures' / 'holidays'


def recorded_urlopen(request, *args, **kwargs):
    url = request.full_url if hasattr(request, 'full_url') else str(request)
    match = re.fullmatch(r'https://date\.nager\.at/api/v3/PublicHolidays/(\d+)/MD', url)
    if match:
        path = _FIXTURES / (match[1] + '.json')
        if path.is_file():
            return io.BytesIO(path.read_bytes())
    raise urllib.error.URLError('Unrecorded network request blocked in tests.')
