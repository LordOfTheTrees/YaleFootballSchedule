import requests
from bs4 import BeautifulSoup
import ics
from ics import Calendar, Event
import datetime
import time
import os
import re
import logging
import sys
import random
import uuid
from zoneinfo import ZoneInfo

# Configure logging first
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("yale_football_scraper.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Try to import curl_cffi for TLS fingerprinting, fallback to requests if not available
try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
    logger.info("curl_cffi available - using TLS fingerprinting")
except ImportError:
    CURL_CFFI_AVAILABLE = False
    logger.warning("curl_cffi not available - falling back to standard requests")

CALENDAR_FILE = "yale_football.ics"

# Stable namespace for event UIDs. Deriving the UID from the game itself keeps
# it constant across runs, so calendar subscribers update events in place
# instead of seeing them deleted and recreated on every scrape.
UID_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

# Kickoff used when the schedule page does not publish a time yet. Games that
# fall back to this carry time_known=False so the calendar can say so and
# validation can tell a real noon kickoff from a parser failure.
DEFAULT_KICKOFF_HOUR = 12
DEFAULT_KICKOFF_MINUTE = 0

HOME_LOCATION = "New Haven, Conn.\nYale Bowl, Class of 1954 Field"

_MONTH_PATTERN = (
    r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
    r'Jul(?:y)?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
)
# SIDEARM renders dates as "Sep 19 (Sat)" and sometimes AP-style ("Sept. 19"),
# so the trailing period is optional and "Sept" is accepted alongside "Sep".
_DATE_RE = re.compile(rf'\b({_MONTH_PATTERN}\.?\s+\d{{1,2}})\b', re.I)
_NUMERIC_DATE_RE = re.compile(r'\b(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b')
# No trailing \b: it would not match after the period in "12:00 p.m."
_TIME_RE = re.compile(r'\b(\d{1,2}:\d{2}\s*[AP]\.?M\.?)(?![A-Za-z])', re.I)
_TBA_VALUES = ("", "TBA", "TBD", "TIME TBA", "TIME TBD", "TBA TBA")

# Expected number of games per season for validation
EXPECTED_GAMES_PER_SEASON = {
    2026: 10,
    2025: 10,  # Yale typically plays 10 games in Ivy League
    2024: 10,
    2023: 10,
    # Add more years as needed
}

# Minimum acceptable number of games (fallback for unknown years)
MIN_GAMES_THRESHOLD = 8

# User-Agent rotation pool (Chrome versions for Windows and macOS)
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]

def _wait_random_time():
    """Random delay between requests to mimic human behavior"""
    wait_time = random.uniform(3, 7)  # Base wait 3-7 seconds
    if random.random() < 0.1:  # 10% chance of longer wait
        wait_time = random.uniform(10, 20)
    if random.random() < 0.01:  # 1% chance of very long wait
        wait_time = random.uniform(30, 60)
    time.sleep(wait_time)
    return wait_time

def get_browser_headers(user_agent=None, referer=None, is_navigation=True):
    """Generate browser-like headers with proper Sec-Fetch-* and Sec-CH-UA headers"""
    if user_agent is None:
        user_agent = random.choice(USER_AGENTS)
    
    # Extract Chrome version from User-Agent for Sec-CH-UA
    chrome_version_match = re.search(r'Chrome/(\d+)', user_agent)
    chrome_version = chrome_version_match.group(1) if chrome_version_match else "122"
    
    headers = {
        'User-Agent': user_agent,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document' if is_navigation else 'empty',
        'Sec-Fetch-Mode': 'navigate' if is_navigation else 'cors',
        'Sec-Fetch-Site': 'same-origin' if referer else 'none',
        'Sec-Fetch-User': '?1',
        'Sec-CH-UA': f'"Chromium";v="{chrome_version}", "Google Chrome";v="{chrome_version}", "Not-A.Brand";v="24"',
        'Sec-CH-UA-Mobile': '?0',
        'Sec-CH-UA-Platform': '"Windows"' if 'Windows' in user_agent else '"macOS"',
        'Cache-Control': 'max-age=0',
    }
    
    if referer:
        headers['Referer'] = referer
    
    return headers

class BrowserSession:
    """Session manager with bot detection avoidance techniques"""
    
    def __init__(self):
        self.session = None
        self.last_url = None
        self.user_agent = random.choice(USER_AGENTS)
        self._initialize_session()
    
    def _initialize_session(self):
        """Initialize session with TLS fingerprinting if available"""
        if CURL_CFFI_AVAILABLE:
            try:
                # Use curl_cffi to impersonate Chrome TLS fingerprint
                self.session = curl_requests.Session(impersonate="chrome120")
                logger.info("Initialized session with curl_cffi (Chrome TLS fingerprint)")
            except Exception as e:
                logger.warning(f"Failed to initialize curl_cffi session: {e}, falling back to requests")
                self.session = requests.Session()
        else:
            self.session = requests.Session()
        
        # Set initial headers
        self.session.headers.update(get_browser_headers(user_agent=self.user_agent))
    
    def visit_homepage(self, homepage_url='https://yalebulldogs.com/'):
        """Visit homepage first to establish session and get cookies"""
        try:
            logger.info(f"Visiting homepage to establish session: {homepage_url}")
            headers = get_browser_headers(user_agent=self.user_agent, is_navigation=True)
            response = self.session.get(homepage_url, headers=headers, timeout=30)
            
            # Check for Cloudflare challenge
            if response.status_code == 403:
                response_preview = response.text[:500].lower()
                if 'just a moment' in response_preview or 'challenge' in response_preview:
                    logger.warning("⚠️  Cloudflare challenge page detected on homepage")
                    return False
            
            self.last_url = homepage_url
            logger.info("Homepage visit successful - session established")
            return True
        except Exception as e:
            logger.error(f"Error visiting homepage: {e}")
            return False
    
    def get(self, url, **kwargs):
        """Make GET request with bot detection avoidance"""
        # Add random delay
        wait_time = _wait_random_time()
        logger.debug(f"Waiting {wait_time:.2f} seconds before request to {url}")
        
        # Set headers with referer tracking
        headers = kwargs.pop('headers', {})
        browser_headers = get_browser_headers(
            user_agent=self.user_agent,
            referer=self.last_url,
            is_navigation=True
        )
        browser_headers.update(headers)
        kwargs['headers'] = browser_headers
        
        # Make request
        try:
            response = self.session.get(url, timeout=30, **kwargs)
        except Exception as e:
            logger.error(f"Request failed for {url}: {e}")
            raise
        
        # Update last URL for referer tracking
        self.last_url = url
        
        # Check for Cloudflare challenge
        if response.status_code == 403:
            response_preview = response.text[:500].lower()
            if 'just a moment' in response_preview or 'challenge' in response_preview:
                logger.warning(f"⚠️  Cloudflare challenge page detected for {url}")
        
        return response
    
    def close(self):
        """Close the session"""
        if self.session:
            self.session.close()

def get_current_season():
    """Get the current football season based on the current date
    College football seasons run Aug-Dec with playoffs in January, so:
    - Jan: return previous year (playoffs still ongoing)
    - Feb-Dec: return current year (upcoming/current season)
    """
    today = datetime.datetime.now()
    if today.month == 1:
        # January - playoffs still happening, use previous year's season
        return today.year - 1
    else:
        # February onwards - look ahead to upcoming/current season
        return today.year

def get_sidearm_headers():
    """Headers optimized for SIDEARM Sports platform"""
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0',
        'Referer': 'https://www.google.com/',
    }

def parse_date_time(date_str, time_str=None, year=None):
    """Improved date/time parsing with better fallbacks - returns timezone-aware datetime"""
    try:
        if year is None:
            year = get_current_season()
            
        # Clean inputs
        date_str = date_str.strip() if date_str else ""
        time_str = time_str.strip() if time_str else ""
        
        logger.debug(f"Parsing date: '{date_str}', time: '{time_str}', year: {year}")
        
        # Handle various date formats
        month, day = None, None
        
        if "/" in date_str and re.match(r"^\s*\d{1,2}\s*/\s*\d{1,2}", date_str):
            # Format: MM/DD or MM/DD/YY (avoid "MST) / 2:00 PM (EST)" style SIDEARM strings)
            parts = [p.strip() for p in date_str.split("/")]
            if len(parts) >= 2:
                month = int(parts[0])
                day = int(parts[1])
                if len(parts) >= 3 and len(parts[2]) >= 2:
                    year_part = int(parts[2])
                    if year_part > 50:
                        year = 1900 + year_part
                    else:
                        year = 2000 + year_part
        elif re.match(r'\w+,?\s+\w+\s+\d+', date_str):
            # Handle ESPN format: "Sat, Sep 20" or "Saturday, September 20"
            try:
                from dateutil import parser
                # Remove day of week and parse the rest
                date_without_day = re.sub(r'^\w+,?\s+', '', date_str)
                parsed = parser.parse(f"{date_without_day} {year}")
                month, day = parsed.month, parsed.day
                logger.debug(f"ESPN date format parsed: '{date_str}' -> month={month}, day={day}")
            except Exception as e:
                logger.error(f"Could not parse ESPN date format '{date_str}': {e}")
                return None
        elif re.match(r'\w+\s+\d+', date_str):
            # Handle "Sep 20", "September 20" format
            try:
                from dateutil import parser
                parsed = parser.parse(f"{date_str} {year}")
                month, day = parsed.month, parsed.day
            except:
                # Fallback manual parsing
                month_names = {
                    'Jan': 1, 'January': 1, 'Feb': 2, 'February': 2, 'Mar': 3, 'March': 3,
                    'Apr': 4, 'April': 4, 'May': 5, 'Jun': 6, 'June': 6,
                    'Jul': 7, 'July': 7, 'Aug': 8, 'August': 8, 'Sep': 9, 'September': 9,
                    'Oct': 10, 'October': 10, 'Nov': 11, 'November': 11, 'Dec': 12, 'December': 12
                }
                parts = date_str.split()
                month_str = parts[0]
                # Try exact match first, then partial match
                month = month_names.get(month_str)
                if not month:
                    for key, val in month_names.items():
                        if month_str.lower().startswith(key.lower()[:3]):
                            month = val
                            break
                if not month:
                    month = 9  # Default to September
                
                try:
                    day = int(parts[1]) if len(parts) > 1 else 1
                except:
                    day = 1
        elif re.match(r'\d{1,2}/\d{1,2}', date_str):
            # Handle MM/DD format
            parts = date_str.split('/')
            month = int(parts[0])
            day = int(parts[1])
        elif re.match(r'\d{4}-\d{2}-\d{2}', date_str):
            # Handle YYYY-MM-DD format
            parts = date_str.split('-')
            year = int(parts[0])
            month = int(parts[1])
            day = int(parts[2])
        
        # If we still don't have month/day, log warning but don't default to Sept 1
        if month is None or day is None:
            logger.warning(f"Could not parse date: {date_str}. Using fallback.")
            # Return None to indicate parsing failure
            return None
        
        # Parse time with better handling. A missing or TBA kickoff falls back
        # to the default below, but says so in the log - callers track it via
        # time_known so a defaulted time is never mistaken for a scraped one.
        hour, minute = DEFAULT_KICKOFF_HOUR, DEFAULT_KICKOFF_MINUTE

        if _is_tba(time_str):
            logger.warning(
                f"No kickoff time for '{date_str}' - defaulting to "
                f"{DEFAULT_KICKOFF_HOUR:02d}:{DEFAULT_KICKOFF_MINUTE:02d} ET"
            )
        else:
            is_pm = "PM" in time_str.upper()
            is_am = "AM" in time_str.upper()
            
            # Extract just the time part
            time_clean = re.sub(r'[^\d:]', '', time_str)
            
            if ":" in time_clean:
                time_parts = time_clean.split(":")
                try:
                    hour = int(time_parts[0])
                    minute = int(time_parts[1]) if len(time_parts) > 1 else 0
                except:
                    hour, minute = DEFAULT_KICKOFF_HOUR, DEFAULT_KICKOFF_MINUTE
            elif time_clean.isdigit() and len(time_clean) <= 2:
                try:
                    hour = int(time_clean)
                    minute = 0
                except:
                    hour = DEFAULT_KICKOFF_HOUR
            
            # Handle AM/PM conversion
            if is_pm and hour < 12:
                hour += 12
            elif is_am and hour == 12:
                hour = 0
            elif not is_am and not is_pm and hour < 8:
                # If no AM/PM specified and hour is small, assume PM for college games
                hour += 12
        
        # Validate the date and create timezone-aware datetime
        try:
            # Create timezone-aware datetime in Eastern Time
            # ZoneInfo automatically handles DST transitions
            eastern_tz = ZoneInfo("America/New_York")
            result = datetime.datetime(year, month, day, hour, minute, tzinfo=eastern_tz)
            
            logger.debug(f"Successfully parsed with timezone: {result}")
            return result
        except ValueError as e:
            logger.error(f"Invalid date/time values: year={year}, month={month}, day={day}, hour={hour}, minute={minute}")
            return None
    except Exception as e:
        logger.error(f"Error parsing date/time: {date_str}, {time_str} - {str(e)}")
        return None

def validate_schedule(games, season):
    """Validate that the scraped schedule looks reasonable"""
    if not games:
        logger.error("No games found in schedule")
        return False
    
    expected_count = EXPECTED_GAMES_PER_SEASON.get(season, MIN_GAMES_THRESHOLD)
    
    if len(games) < expected_count:
        logger.error(f"Only found {len(games)} games for season {season}, expected at least {expected_count}")
        return False
    
    # A parser that cannot find kickoff times still yields a plausible-looking
    # schedule - every game just lands silently on the default. Fail instead of
    # publishing a full slate of fabricated noon kickoffs.
    timed = [game for game in games if game.get('time_known')]
    if not timed:
        logger.error(
            f"No game carries a scraped kickoff time - all {len(games)} fell back to "
            f"{DEFAULT_KICKOFF_HOUR:02d}:{DEFAULT_KICKOFF_MINUTE:02d} ET. "
            "The time parser is broken, not the schedule."
        )
        return False
    if len(timed) < len(games):
        pending = [game['title'] for game in games if not game.get('time_known')]
        logger.warning(f"{len(pending)} game(s) with no published kickoff yet: {', '.join(pending)}")

    # Yale plays both home and away every season, so an all-one-way slate means
    # the venue detection failed.
    home_count = sum(1 for game in games if game.get('is_home'))
    if len(games) >= 4 and home_count in (0, len(games)):
        side = 'home' if home_count else 'away'
        logger.error(f"All {len(games)} games came back as {side} games - home/away detection is broken")
        return False

    # Check for suspicious dates (all games on same date, etc.)
    dates = [game['start'].date() for game in games]
    unique_dates = len(set(dates))
    
    if unique_dates < len(games) * 0.8:  # At least 80% should be on different dates
        logger.error(f"Schedule has suspicious date distribution: {unique_dates} unique dates for {len(games)} games")
        return False
    
    # Check for reasonable date range (games should span Aug-Dec for college football)
    earliest = min(dates)
    latest = max(dates)
    
    if earliest.month < 8 or latest.month > 12:
        logger.warning(f"Games span unusual months: {earliest.month} to {latest.month}")
    
    # Check for games defaulting to Sept 1 (common parsing error)
    sept_1_count = sum(1 for date in dates if date.month == 9 and date.day == 1)
    if sept_1_count > 1:
        logger.error(f"Too many games defaulting to September 1st ({sept_1_count}), likely parsing error")
        return False
    
    logger.info(f"Schedule validation passed: {len(games)} games from {earliest} to {latest}")
    return True

def detect_schedule_structure(soup):
    """Dynamically detect the schedule structure on SIDEARM pages"""
    logger.info("Analyzing page structure for schedule data...")
    
    # Look for common SIDEARM schedule patterns
    possible_containers = [
        # Modern SIDEARM selectors
        '.sidearm-schedule-games',
        '.sidearm-schedule-games-container', 
        '.schedule-list',
        '.game-list',
        '.event-listing',
        
        # Table-based layouts
        'table.sidearm-table',
        'table.schedule',
        'table.schedule-table',
        '.ResponsiveTable table',
        
        # Card/item based layouts
        '.schedule-game',
        '.game-card',
        '.event-card',
        '.schedule-item',
        
        # Generic containers that might hold games
        '[data-module*="schedule"]',
        '[id*="schedule"]',
        '[class*="schedule"]'
    ]
    
    for selector in possible_containers:
        container = soup.select_one(selector)
        if container:
            # Look for individual game items within this container
            game_selectors = [
                '.sidearm-schedule-game',
                '.schedule-game', 
                '.game-item',
                '.event-item',
                'tr',  # Table rows
                '.game',
                '.event',
                '[data-game]',
                '[class*="game"]'
            ]
            
            for game_sel in game_selectors:
                games = container.select(game_sel)
                if len(games) > 3:  # Must have several games to be valid
                    logger.info(f"Found schedule structure: {selector} -> {game_sel} ({len(games)} items)")
                    return container, game_sel
    
    logger.warning("Could not detect schedule structure")
    return None, None

def normalize_opponent_poll_rank(opponent: str) -> str:
    """
    SIDEARM often emits poll rank with no space: '#15Youngstown State'.
    Keep the rank, normalize to '#15 Youngstown State'. Also handles 'No. 15 Name'.
    """
    if not opponent:
        return opponent
    m = re.match(r"^no\.?\s*(\d+)\s+(.+)$", opponent, re.IGNORECASE)
    if m:
        return f"#{m.group(1)} {m.group(2).strip()}"
    m = re.match(r"^#(\d+)\s*(.+)$", opponent)
    if m:
        rest = m.group(2).strip()
        if rest:
            return f"#{m.group(1)} {rest}"
    return opponent

def _first_date(text):
    """First date in the text, in either the word ("Sep 19") or numeric form."""
    m = _DATE_RE.search(text) or _NUMERIC_DATE_RE.search(text)
    return m.group(1) if m else ""


def _first_time(text):
    """First clock time in the text, e.g. "2:00 PM"."""
    m = _TIME_RE.search(text)
    return m.group(1) if m else ""


def _is_tba(value):
    return not value or value.strip().upper().replace(".", "") in _TBA_VALUES


def _is_yale_venue(location):
    """True when a venue string names Yale's home field."""
    low = (location or "").lower()
    return "new haven" in low or "yale bowl" in low


def extract_game_data(game_element):
    """Extract date, time, opponent, venue and home/away from a game element.

    Known SIDEARM field classes are tried first, then a regex sweep over the
    element's whole text. The sweep matters: the classic SIDEARM card keeps the
    kickoff in a second <span> inside .sidearm-schedule-game-opponent-date, so
    there is no time-classed element to find and a selector-only lookup always
    comes back empty.
    """
    try:
        text = game_element.get_text(' ', strip=True)

        # --- Date ---
        date_str = ""
        date_selectors = [
            '.sidearm-schedule-game-opponent-date',
            '.date', '.game-date', '.event-date', '.schedule-date',
            '[class*="date"]', 'time', '.datetime',
        ]
        for sel in date_selectors:
            date_elem = game_element.select_one(sel)
            if date_elem:
                date_str = _first_date(date_elem.get_text(' ', strip=True))
                if date_str:
                    break
        if not date_str:
            date_str = _first_date(text)
        if not date_str:
            logger.debug(f"No date found in game element; text: {text[:120]}")
            return None

        # --- Time ---
        # Never overwrite a good value with an empty or TBA one: keep looking.
        time_str = ""
        time_selectors = [
            '.sidearm-schedule-game-opponent-time',
            '.time', '.game-time', '.event-time', '.schedule-time',
            '[class*="time"]', '.kickoff',
        ]
        for sel in time_selectors:
            time_elem = game_element.select_one(sel)
            if not time_elem:
                continue
            candidate = time_elem.get_text(' ', strip=True)
            if _is_tba(candidate):
                continue
            found = _first_time(candidate)
            if found:
                time_str = found
                break
        if not time_str:
            time_str = _first_time(text)

        # An unpublished kickoff stays empty here rather than silently becoming
        # noon, so the caller can tell "TBA" apart from a real midday game.
        time_known = bool(time_str)

        # --- Opponent ---
        opponent = ""
        opponent_selectors = [
            '.sidearm-schedule-game-opponent-name',
            '[class*="opponent-name"]', '[class*="team__name"]',
            '[class*="team-name"]', '[class*="opponent"]',
            '.opponent', '.team-name', '.visitor', '.away-team', '.home-team',
        ]
        for sel in opponent_selectors:
            for opp_elem in game_element.select(sel):
                candidate = opp_elem.get_text(' ', strip=True)
                candidate = re.sub(r'^(vs\.?\s*|at\s*|@\s*)', '', candidate, flags=re.IGNORECASE).strip()
                candidate = normalize_opponent_poll_rank(candidate)
                if len(candidate) < 2 or candidate.upper() in ('TBA', 'TBD', 'BYE'):
                    continue
                # The current template lists both teams in the row.
                if candidate.lower() in ('yale', 'yale bulldogs', 'yale university'):
                    continue
                opponent = candidate
                break
            if opponent:
                break

        if not opponent:
            match = re.search(r'(?:vs\.?\s+|at\s+|@\s*)([A-Z][A-Za-z\s&\-\'\.]{1,40})', text)
            if match:
                candidate = re.split(r'\s{2,}|\n|\d', match.group(1))[0].strip()
                if len(candidate) >= 2:
                    opponent = normalize_opponent_poll_rank(candidate)

        if not opponent or opponent.upper() in ('TBA', 'TBD', 'BYE'):
            logger.debug(f"No valid opponent found; text: {text[:120]}")
            return None

        # --- Venue ---
        location = ""
        location_selectors = [
            '[class*="venue-text"]', '[class*="location-text"]',
            '.sidearm-schedule-game-location', '[class*="location"]', '[class*="venue"]',
        ]
        for sel in location_selectors:
            loc_elem = game_element.select_one(sel)
            if not loc_elem:
                continue
            candidate = loc_elem.get_text(' ', strip=True)
            if not _is_tba(candidate):
                location = candidate
                break

        # --- Broadcast ---
        broadcast = ""
        broadcast_selectors = [
            '[class*="tv-network"]', '[class*="tv-networks"]', '[class*="tv-link"]',
            '[class*="broadcast"]', '[class*="network"]',
        ]
        for sel in broadcast_selectors:
            tv_elem = game_element.select_one(sel)
            if not tv_elem:
                continue
            candidate = re.sub(r'^\s*(TV|Watch|Live)\s*:\s*', '',
                               tv_elem.get_text(' ', strip=True), flags=re.IGNORECASE).strip()
            # SIDEARM parks "TBA" in the TV slot until a network is assigned.
            if not _is_tba(candidate):
                broadcast = candidate
            break

        # --- Home/away ---
        # Class markers first, then the venue, which is decisive for a
        # single-team calendar: anything not at the Yale Bowl is a road game.
        # A bare "at " substring search is not a signal - SIDEARM writes
        # "Worcester, Mass. / Fitton Field", with no "at" anywhere.
        classes = ' '.join(game_element.get('class') or [])
        for tag in game_element.select('[class]'):
            classes += ' ' + ' '.join(tag.get('class') or [])
        classes = classes.lower()

        if 'venue--away' in classes or 'sidearm-schedule-game-away' in classes:
            is_home = False
        elif 'venue--home' in classes or 'sidearm-schedule-game-home' in classes:
            is_home = True
        elif location:
            is_home = _is_yale_venue(location)
        elif re.search(r'\bat\s+[A-Z]', text):
            is_home = False
        elif re.search(r'\bvs\.?\s', text, re.IGNORECASE):
            is_home = True
        elif 'away' in text.lower():
            is_home = False
        else:
            is_home = True

        return {
            'date_str': date_str,
            'time_str': time_str,
            'time_known': time_known,
            'opponent': opponent,
            'is_home': is_home,
            'location': location,
            'broadcast': broadcast,
            'raw_text': text[:100],  # For debugging
        }

    except Exception as e:
        logger.error(f"Error extracting game data: {e}")
        return None

def scrape_yale_schedule(season=None):
    """Modern SIDEARM-aware Yale schedule scraper with improved error handling and bot detection avoidance"""
    if season is None:
        season = get_current_season()
    
    logger.info(f"Scraping Yale schedule for season {season}")
    games = []
    browser_session = None
    
    try:
        # Initialize browser session with bot detection avoidance
        browser_session = BrowserSession()
        
        # Visit homepage first to establish session
        if not browser_session.visit_homepage('https://yalebulldogs.com/'):
            logger.warning("Failed to establish session via homepage, continuing anyway...")
        
        # Try the schedule page with the best URL first
        base_urls = [
            "https://yalebulldogs.com/sports/football/schedule",  # Best direct URL
            f"https://yalebulldogs.com/sports/football/schedule/{season}",
            f"https://yalebulldogs.com/schedule?sport=football&season={season}"
        ]
        
        for url in base_urls:
            try:
                logger.info(f"Trying URL: {url}")
                
                response = browser_session.get(url)
                
                response_text_lower = response.text.lower()
                
                # Check for "No Data Available" message FIRST - before bot detection check
                # This way we catch it even if bot detection is also triggered
                no_data_patterns = [
                    "no data available",
                    "no schedule available",
                    "schedule not available",
                    "no games scheduled",
                    "no events found",
                    "schedule coming soon"
                ]
                if any(pattern in response_text_lower for pattern in no_data_patterns):
                    logger.info(f"'No Data Available' detected for season {season} - schedule not yet published")
                    logger.debug(f"Response preview: {response.text[:500]}")
                    return None  # Return None to indicate "No Data Available" (distinct from empty list)
                
                # SIDEARM ships a hidden modal with "Ad Blocker Detected" in the HTML on normal pages.
                # Only treat ad-blocker copy as a hard wall when schedule markup is missing.
                has_schedule_markup = (
                    "sidearm-schedule-games" in response_text_lower
                    or "sidearm-schedule-game" in response_text_lower
                )
                adblock_wall_copy = (
                    "ad blocker" in response_text_lower
                    or "blocks ads hinders" in response_text_lower
                )
                if response.status_code == 403 or (adblock_wall_copy and not has_schedule_markup):
                    logger.warning(f"Bot/ad blocker detection triggered for {url}")
                    logger.debug(f"Response status: {response.status_code}, Preview: {response.text[:500]}")
                    continue
                
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Dynamically detect schedule structure
                container, game_selector = detect_schedule_structure(soup)
                
                if not container:
                    logger.warning(f"No schedule structure found on {url}")
                    continue
                
                # Extract games using detected structure
                game_elements = container.select(game_selector)
                logger.info(f"Found {len(game_elements)} potential game elements")
                
                untimed_sample = None
                for game_elem in game_elements:
                    game_data = extract_game_data(game_elem)
                    
                    if not game_data or not game_data['opponent']:
                        continue
                    
                    # Create game info
                    opponent = game_data['opponent']
                    is_home = game_data['is_home']

                    if is_home:
                        title = f"{opponent} at Yale"
                        # Prefer the scraped venue, but keep the full Yale Bowl
                        # name when the card only says "New Haven, Conn."
                        location = game_data['location'] or HOME_LOCATION
                        if _is_yale_venue(location) and 'Class of 1954' not in location:
                            location = HOME_LOCATION
                    else:
                        title = f"Yale at {opponent}"
                        location = game_data['location']

                    game_datetime = parse_date_time(game_data['date_str'], game_data['time_str'], season)
                    
                    if not game_datetime:
                        logger.warning(f"Could not parse datetime for {title}, skipping")
                        continue
                    
                    duration = datetime.timedelta(hours=3, minutes=30)
                    
                    game_info = {
                        'title': title,
                        'start': game_datetime,
                        'end': game_datetime + duration,
                        'location': location,
                        'broadcast': game_data['broadcast'],
                        'is_home': is_home,
                        'opponent': opponent,
                        'date_str': game_data['date_str'],
                        'time_str': game_data['time_str'],
                        'time_known': game_data['time_known'],
                    }
                    
                    games.append(game_info)
                    kickoff = (game_datetime.strftime('%I:%M %p').lstrip('0')
                               if game_data['time_known'] else 'kickoff TBA')
                    logger.info(f"Scraped: {title} on {game_datetime.date()} ({kickoff})")

                    if not game_data['time_known'] and untimed_sample is None:
                        untimed_sample = (title, game_elem)

                # A card with no clock time anywhere is either a genuine TBA or a
                # template change that moved the kickoff somewhere we do not read.
                # Dump one so the next person can tell which, without a scraper run
                # of their own.
                if untimed_sample:
                    sample_title, sample_elem = untimed_sample
                    logger.warning(
                        f"No kickoff time found in the markup for {sample_title}. "
                        f"Sample card HTML: {str(sample_elem)[:2000]}"
                    )
                
                if games:
                    logger.info(f"Successfully scraped {len(games)} games from {url}")
                    return games
                    
            except Exception as e:
                logger.error(f"Error with {url}: {e}")
                continue
        
    except Exception as e:
        logger.error(f"Error scraping Yale schedule: {str(e)}")
    finally:
        if browser_session:
            browser_session.close()
    
    return games

def scrape_espn_schedule(season=None):
    """ESPN backup scraper with improved parsing and bot detection avoidance"""
    if season is None:
        season = get_current_season()
    
    logger.info(f"Scraping ESPN for season {season}")
    games = []
    browser_session = None
    
    try:
        # Initialize browser session with bot detection avoidance
        browser_session = BrowserSession()
        
        # Visit ESPN homepage first to establish session
        if not browser_session.visit_homepage('https://www.espn.com/'):
            logger.warning("Failed to establish session via ESPN homepage, continuing anyway...")
        
        url = f"https://www.espn.com/college-football/team/schedule/_/id/43/yale-bulldogs"
        response = browser_session.get(url)
        
        # ESPN often serves an AWS WAF browser challenge (HTTP 202, challenge.js) to plain HTTP clients.
        response_text_lower = response.text.lower()
        if response.status_code == 202 or (
            "awswaf" in response_text_lower and "challenge-container" in response_text_lower
        ):
            logger.warning(
                "ESPN returned an AWS WAF challenge page instead of schedule HTML "
                "(install curl_cffi on the runner for TLS impersonation, or rely on Yale SIDEARM)"
            )
            return games
        
        # Check for "No Data Available" message - check multiple variations
        no_data_patterns = [
            "no data available",
            "no schedule available",
            "schedule not available",
            "no games scheduled",
            "no events found",
            "schedule coming soon"
        ]
        if any(pattern in response_text_lower for pattern in no_data_patterns):
            logger.info(f"'No Data Available' detected for season {season} - schedule not yet published")
            logger.debug(f"Response preview: {response.text[:500]}")
            return None  # Return None to indicate "No Data Available" (distinct from empty list)
        
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # ESPN schedule parsing - try multiple table formats
        table = soup.find('table', class_='Table')
        if not table:
            table = soup.find('div', class_='ResponsiveTable')
            if table:
                table = table.find('table')
        
        if table:
            rows = table.find_all('tr')[1:]  # Skip header
            for row in rows:
                try:
                    cells = row.find_all(['td', 'th'])
                    if len(cells) >= 2:
                        date_str = cells[0].get_text(strip=True)
                        opponent_str = cells[1].get_text(strip=True)
                        
                        # Skip header rows
                        if date_str.upper() in ['DATE', 'DAY', 'WEEK'] or opponent_str.upper() in ['OPPONENT', 'TEAM']:
                            continue
                        
                        # Skip bye weeks
                        if not opponent_str or opponent_str.lower() in ['bye', 'open']:
                            continue
                        
                        # Extract time if available
                        time_str = ""
                        if len(cells) > 2:
                            time_cell = cells[2].get_text(strip=True)
                            if not _is_tba(time_cell):
                                time_str = _first_time(time_cell)
                        time_known = bool(time_str)
                        
                        is_away = 'at ' in opponent_str.lower() or '@' in opponent_str
                        opponent = re.sub(r'^(vs\.?\s*|at\s*|@\s*)', '', opponent_str, flags=re.IGNORECASE).strip()
                        
                        if is_away:
                            title = f"Yale at {opponent}"
                            location = ""
                        else:
                            title = f"{opponent} at Yale"
                            location = "New Haven, Conn.\nYale Bowl, Class of 1954 Field"
                        
                        game_datetime = parse_date_time(date_str, time_str, season)
                        
                        if not game_datetime:
                            logger.warning(f"Could not parse ESPN datetime for {title}, skipping")
                            continue
                        
                        duration = datetime.timedelta(hours=3, minutes=30)
                        
                        game_info = {
                            'title': title,
                            'start': game_datetime,
                            'end': game_datetime + duration,
                            'location': location,
                            'broadcast': "",
                            'is_home': not is_away,
                            'opponent': opponent,
                            'date_str': date_str,
                            'time_str': time_str,
                            'time_known': time_known,
                        }
                        
                        games.append(game_info)
                        logger.info(f"ESPN: {title} on {game_datetime}")
                        
                except Exception as e:
                    logger.error(f"Error parsing ESPN row: {e}")
                    continue
        
    except Exception as e:
        logger.error(f"Error scraping ESPN: {str(e)}")
    finally:
        if browser_session:
            browser_session.close()
    
    return games

def scrape_schedule(season=None):
    """Main scraping function - returns None if 'No Data Available', empty list if failed, or games if successful"""
    if season is None:
        season = get_current_season()
    
    logger.info("Starting schedule scraping...")
    
    # Try Yale first, then ESPN - NO FALLBACK DATA
    sources = [
        ("Yale SIDEARM", scrape_yale_schedule),
        ("ESPN", scrape_espn_schedule)
    ]
    
    for source_name, scrape_func in sources:
        logger.info(f"Trying {source_name}...")
        try:
            games = scrape_func(season)
            # Check if this is the special "No Data Available" case
            if games is None:
                logger.info(f"'No Data Available' detected from {source_name} for season {season}")
                return None  # Return None to indicate "No Data Available"
            if games and validate_schedule(games, season):
                logger.info(f"Success: {len(games)} valid games from {source_name}")
                return games
            elif games:
                logger.warning(f"{source_name} returned {len(games)} games but failed validation")
            else:
                logger.warning(f"No games from {source_name}")
        except Exception as e:
            logger.error(f"{source_name} failed: {e}")
            continue
    
    logger.error("All scraping sources failed or returned insufficient/invalid data")
    return []

def create_calendar(games):
    """Create iCalendar file"""
    cal = Calendar()
    cal._prodid = "Yale Football Schedule - https://raw.githubusercontent.com/LordOfTheTrees/YaleFootballSchedule/main/yale_football.ics"
    
    for game in games:
        event = Event()
        event.name = game['title']
        event.begin = game['start']
        event.end = game['end']
        event.location = game['location']

        # Derive the UID from the matchup itself so it survives re-scrapes.
        # Letting the ics library mint a random one on every run made each
        # nightly commit look like ten deleted events and ten new ones, which
        # subscribers apply by dropping their alerts and RSVPs.
        key = f"{game['start'].year}|{game['opponent'].lower()}|{'home' if game['is_home'] else 'away'}"
        event.uid = f"{uuid.uuid5(UID_NAMESPACE, key)}@yalefootballschedule"

        description = ""
        if game['broadcast']:
            description += f"Broadcast: {game['broadcast']}\n"
        description += "Home Game" if game['is_home'] else "Away Game"
        if game['opponent']:
            description += f"\nOpponent: {game['opponent']}"
        if not game.get('time_known', True):
            description += (
                f"\nKickoff time not yet announced - placeholder "
                f"{DEFAULT_KICKOFF_HOUR % 12 or 12}:{DEFAULT_KICKOFF_MINUTE:02d} "
                f"{'PM' if DEFAULT_KICKOFF_HOUR >= 12 else 'AM'} ET"
            )

        event.description = description
        cal.events.add(event)
    
    with open(CALENDAR_FILE, 'w') as f:
        f.write(cal.serialize())
    
    logger.info(f"Calendar created with {len(games)} events")
    return cal

def update_calendar(custom_season=None):
    """Update the calendar - gracefully exits if 'No Data Available', fails if scraping unsuccessful"""
    try:
        season = custom_season or get_current_season()
        games = scrape_schedule(season)
        
        # Check for "No Data Available" case - exit gracefully without updating calendar
        if games is None:
            logger.info(f"'No Data Available' for season {season} - exiting without modifying existing calendar")
            return True  # Return True to indicate successful completion (no update needed)
        
        if not games:
            logger.error("No games found - calendar update failed")
            return False
        
        if not validate_schedule(games, season):
            logger.error("Schedule validation failed - calendar update aborted")
            return False
            
        create_calendar(games)
        logger.info(f"Calendar updated successfully with {len(games)} validated games")
        return True
        
    except Exception as e:
        logger.error(f"Error updating calendar: {str(e)}")
        return False

if __name__ == "__main__":
    # Display startup information
    current_season = get_current_season()
    logger.info(f"Starting Yale Football Schedule Scraper for season {current_season}")
    logger.info("Using improved parsing with fallback data support")
    
    # Initial calendar creation
    success = update_calendar()
    if not success:
        logger.error("Calendar update failed - script exiting with error code")
        sys.exit(1)
    else:
        logger.info(f"Calendar update completed successfully for season {current_season}")
        print(f"Calendar updated successfully for season {current_season}")