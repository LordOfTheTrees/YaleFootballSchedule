import requests
from bs4 import BeautifulSoup
import ics
from ics import Calendar, Event
import datetime
import time
import os
import re
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("yale_football_scraper.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

CALENDAR_FILE = "yale_football.ics"

# Expected number of games per season for validation
EXPECTED_GAMES_PER_SEASON = {
    2025: 10,  # Yale typically plays 10 games in Ivy League
    2024: 10,
    2023: 10,
    # Add more years as needed
}

# Minimum acceptable number of games (fallback for unknown years)
MIN_GAMES_THRESHOLD = 8

def get_current_season():
    """Get the current football season based on the current date"""
    today = datetime.datetime.now()
    if today.month > 2:
        return today.year
    else:
        return today.year - 1

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
        time_str = time_str.strip() if time_str else "12:00 PM"  # Default to 12 PM for Ivy League football
        
        logger.debug(f"Parsing date: '{date_str}', time: '{time_str}', year: {year}")
        
        # Handle various date formats
        month, day = None, None
        
        if "/" in date_str:
            # Format: MM/DD or MM/DD/YY
            parts = date_str.split("/")
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
        
        # Parse time with better handling
        hour, minute = 12, 0  # Default to 12:00 PM for Ivy League football
        
        if time_str and time_str.upper() not in ["TBA", "TBD", "", "TIME TBA"]:
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
                    hour, minute = 12, 0
            elif time_clean.isdigit() and len(time_clean) <= 2:
                try:
                    hour = int(time_clean)
                    minute = 0
                except:
                    hour = 12
            
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
            # Import timezone support
            from datetime import timezone, timedelta
            
            # Create timezone-naive datetime first
            naive_dt = datetime.datetime(year, month, day, hour, minute)
            
            # Add Eastern timezone (UTC-5 in standard time, UTC-4 in daylight time)
            # For football season (Sep-Dec), this is mostly Eastern Daylight Time (UTC-4)
            eastern_tz = timezone(timedelta(hours=-5))  # EST
            
            # Check if we're in daylight saving time period (roughly March-November)
            if month >= 3 and month <= 11:
                eastern_tz = timezone(timedelta(hours=-4))  # EDT
            
            result = naive_dt.replace(tzinfo=eastern_tz)
            
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

def extract_game_data(game_element):
    """Extract game data from a single game element using flexible selectors"""
    try:
        # Try multiple strategies to extract date
        date_str = ""
        date_selectors = [
            '.date', '.game-date', '.event-date', '.schedule-date',
            '.sidearm-schedule-game-opponent-date',
            '[class*="date"]', 'time', '.datetime',
            'td:first-child', '.first-col'
        ]
        
        for sel in date_selectors:
            date_elem = game_element.select_one(sel)
            if date_elem:
                date_str = date_elem.get_text(strip=True)
                if date_str and any(char.isdigit() for char in date_str):
                    break
        
        # Try multiple strategies to extract time
        time_str = "12:00 PM"  # Better default for Ivy League football
        time_selectors = [
            '.time', '.game-time', '.event-time', '.schedule-time',
            '.sidearm-schedule-game-opponent-time',
            '[class*="time"]', '.kickoff'
        ]
        
        for sel in time_selectors:
            time_elem = game_element.select_one(sel)
            if time_elem:
                time_str = time_elem.get_text(strip=True)
                if time_str and time_str.upper() not in ["", "TBA", "TBD"]:
                    break
        
        # Try multiple strategies to extract opponent
        opponent = ""
        opponent_selectors = [
            '.opponent', '.team-name', '.visitor', '.away-team', '.home-team',
            '.sidearm-schedule-game-opponent-name',
            '[class*="opponent"]', '[class*="team"]',
            'a[href*="team"]', 'td:nth-child(2)'
        ]
        
        for sel in opponent_selectors:
            opp_elem = game_element.select_one(sel)
            if opp_elem:
                opponent = opp_elem.get_text(strip=True)
                if opponent and len(opponent) > 2:
                    break
        
        # If still no opponent, look in all text content
        if not opponent:
            all_text = game_element.get_text()
            # Look for patterns like "vs Team" or "at Team"
            match = re.search(r'(?:vs\.?\s+|at\s+|@\s*)([A-Za-z\s&]+)', all_text, re.IGNORECASE)
            if match:
                opponent = match.group(1).strip()
        
        # Determine home/away
        all_text = game_element.get_text().lower()
        is_away = any(indicator in all_text for indicator in ['at ', '@ ', 'away'])
        is_home = not is_away
        
        # Clean opponent name
        opponent = re.sub(r'^(vs\.?\s*|at\s*|@\s*)', '', opponent, flags=re.IGNORECASE).strip()
        
        return {
            'date_str': date_str,
            'time_str': time_str, 
            'opponent': opponent,
            'is_home': is_home,
            'raw_text': game_element.get_text(strip=True)[:100]  # For debugging
        }
        
    except Exception as e:
        logger.error(f"Error extracting game data: {e}")
        return None

def scrape_yale_schedule(season=None):
    """Modern SIDEARM-aware Yale schedule scraper with improved error handling"""
    if season is None:
        season = get_current_season()
    
    logger.info(f"Scraping Yale schedule for season {season}")
    games = []
    
    try:
        headers = get_sidearm_headers()
        session = requests.Session()
        session.headers.update(headers)
        
        # Try the schedule page with the best URL first
        base_urls = [
            "https://yalebulldogs.com/sports/football/schedule",  # Best direct URL
            f"https://yalebulldogs.com/sports/football/schedule/{season}",
            f"https://yalebulldogs.com/schedule?sport=football&season={season}"
        ]
        
        for url in base_urls:
            try:
                logger.info(f"Trying URL: {url}")
                
                # Add delay to avoid being flagged as bot
                time.sleep(2)
                
                response = session.get(url, timeout=30)
                
                # Check for bot detection or ad blocker messages
                if ("ad blocker" in response.text.lower() or 
                    "blocks ads hinders" in response.text.lower() or 
                    response.status_code == 403):
                    logger.warning(f"Bot/ad blocker detection triggered for {url}")
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
                
                for game_elem in game_elements:
                    game_data = extract_game_data(game_elem)
                    
                    if not game_data or not game_data['opponent']:
                        continue
                    
                    # Create game info
                    opponent = game_data['opponent']
                    is_home = game_data['is_home']
                    
                    if is_home:
                        title = f"{opponent} at Yale"
                        location = "New Haven, Conn.\nYale Bowl, Class of 1954 Field"
                    else:
                        title = f"Yale at {opponent}"
                        location = ""
                    
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
                        'broadcast': "",
                        'is_home': is_home,
                        'opponent': opponent,
                        'date_str': game_data['date_str'],
                        'time_str': game_data['time_str']
                    }
                    
                    games.append(game_info)
                    logger.info(f"Scraped: {title} on {game_datetime}")
                
                if games:
                    logger.info(f"Successfully scraped {len(games)} games from {url}")
                    return games
                    
            except Exception as e:
                logger.error(f"Error with {url}: {e}")
                continue
        
    except Exception as e:
        logger.error(f"Error scraping Yale schedule: {str(e)}")
    
    return games

def scrape_espn_schedule(season=None):
    """ESPN backup scraper with improved parsing"""
    if season is None:
        season = get_current_season()
    
    logger.info(f"Scraping ESPN for season {season}")
    games = []
    
    try:
        headers = get_sidearm_headers()
        url = f"https://www.espn.com/college-football/team/schedule/_/id/43/yale-bulldogs"
        response = requests.get(url, headers=headers, timeout=30)
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
                        time_str = "12:00 PM"  # Default
                        if len(cells) > 2:
                            time_cell = cells[2].get_text(strip=True)
                            if any(char.isdigit() for char in time_cell) and ("AM" in time_cell or "PM" in time_cell):
                                time_str = time_cell
                        
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
                            'time_str': time_str
                        }
                        
                        games.append(game_info)
                        logger.info(f"ESPN: {title} on {game_datetime}")
                        
                except Exception as e:
                    logger.error(f"Error parsing ESPN row: {e}")
                    continue
        
    except Exception as e:
        logger.error(f"Error scraping ESPN: {str(e)}")
    
    return games

def scrape_schedule(season=None):
    """Main scraping function - fails if insufficient games found"""
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
        
        description = ""
        if game['broadcast']:
            description += f"Broadcast: {game['broadcast']}\n"
        description += "Home Game" if game['is_home'] else "Away Game"
        if game['opponent']:
            description += f"\nOpponent: {game['opponent']}"
            
        event.description = description
        cal.events.add(event)
    
    with open(CALENDAR_FILE, 'w') as f:
        f.write(cal.serialize())
    
    logger.info(f"Calendar created with {len(games)} events")
    return cal

def update_calendar(custom_season=None):
    """Update the calendar - fails if scraping unsuccessful"""
    try:
        season = custom_season or get_current_season()
        games = scrape_schedule(season)
        
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
        logger.error("Initial calendar creation failed")
    else:
        print(f"Calendar updated successfully for season {current_season}")