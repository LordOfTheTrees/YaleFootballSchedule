import requests
from bs4 import BeautifulSoup
import ics
from ics import Calendar, Event
import datetime
import time
import os
import re
from flask import Flask, Response, request
import logging
import json
from apscheduler.schedulers.background import BackgroundScheduler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("yale_football_scraper.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

CALENDAR_FILE = "yale_football.ics"

# Expected number of games per season for validation
EXPECTED_GAMES_PER_SEASON = {
    2025: 10,  # Yale plays 10 games in 2025
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
    """Improved date/time parsing with better fallbacks"""
    try:
        if year is None:
            year = get_current_season()
            
        # Clean inputs
        date_str = date_str.strip() if date_str else ""
        time_str = time_str.strip() if time_str else "1:00 PM"  # Default to 1 PM for college football
        
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
        hour, minute = 13, 0  # Default to 1:00 PM for college football
        
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
                    hour, minute = 13, 0
            elif time_clean.isdigit() and len(time_clean) <= 2:
                try:
                    hour = int(time_clean)
                    minute = 0
                except:
                    hour = 13
            
            # Handle AM/PM conversion
            if is_pm and hour < 12:
                hour += 12
            elif is_am and hour == 12:
                hour = 0
            elif not is_am and not is_pm and hour < 8:
                # If no AM/PM specified and hour is small, assume PM for college games
                hour += 12
        
        # Validate the date
        try:
            result = datetime.datetime(year, month, day, hour, minute)
            logger.debug(f"Successfully parsed: {result}")
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
        time_str = "1:00 PM"  # Better default for college football
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
        
        # Try the schedule page with multiple approaches
        base_urls = [
            f"https://yalebulldogs.com/sports/football/schedule/{season}",
            f"https://yalebulldogs.com/sports/football/schedule",
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
        url = f"https://www.espn.com/college-football/team/schedule/_/id/43/season/{season}"
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
                        
                        # Skip bye weeks
                        if not opponent_str or opponent_str.lower() in ['bye', 'open']:
                            continue
                        
                        # Extract time if available
                        time_str = "1:00 PM"  # Default
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
        f.write(str(cal))
    
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

@app.route('/calendar.ics')
def serve_calendar():
    """Serve the calendar file"""
    try:
        with open(CALENDAR_FILE, 'r') as f:
            cal_content = f.read()
            
        # Add raw GitHub URL to the PRODID field if not already present
        if "PRODID:" in cal_content and "raw.githubusercontent.com" not in cal_content:
            cal_content = cal_content.replace(
                "PRODID:ics.py - http://git.io/lLljaA",
                "PRODID:Yale Football Schedule - https://raw.githubusercontent.com/LordOfTheTrees/YaleFootballSchedule/main/yale_football.ics"
            )
            
        return Response(cal_content, mimetype='text/calendar')
    except Exception as e:
        logger.error(f"Error serving calendar: {str(e)}")
        return "Calendar not available", 500

@app.route('/')
def index():
    """Landing page"""
    return """
    <html>
        <head>
            <title>Yale Football Calendar</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    max-width: 800px;
                    margin: 0 auto;
                    padding: 20px;
                    line-height: 1.6;
                }
                h1 {
                    color: #00356b; /* Yale Blue */
                }
                .container {
                    border: 1px solid #ddd;
                    padding: 20px;
                    border-radius: 5px;
                    background-color: #f9f9f9;
                }
                pre {
                    background-color: #eee;
                    padding: 10px;
                    border-radius: 5px;
                    overflow-x: auto;
                }
                a {
                    color: #00356b;
                    text-decoration: none;
                }
                a:hover {
                    text-decoration: underline;
                }
                .footer {
                    margin-top: 30px;
                    font-size: 0.8em;
                    color: #777;
                }
            </style>
        </head>
        <body>
            <h1>Yale Football Calendar</h1>
            <div class="container">
                <p>This calendar provides a schedule of Yale Football games that you can add to your calendar app.</p>
                <p>To subscribe to this calendar in your calendar app, use this URL:</p>
                <pre>https://raw.githubusercontent.com/LordOfTheTrees/YaleFootballSchedule/main/yale_football.ics</pre>
                <p><a href="https://raw.githubusercontent.com/LordOfTheTrees/YaleFootballSchedule/main/yale_football.ics">Direct Link to Calendar File</a></p>
                <p>The calendar updates daily with the latest game information. Now includes fallback data to ensure schedule availability.</p>
            </div>
            <div class="footer">
                <p>Data sourced from yalebulldogs.com with ESPN and fallback data as backups. Updated daily.</p>
                <p>This service is not affiliated with Yale University.</p>
                <p>Source code available on <a href="https://github.com/LordOfTheTrees/YaleFootballSchedule">GitHub</a>.</p>
            </div>
        </body>
    </html>
    """

@app.route('/debug')
def debug_info():
    """Debug information"""
    try:
        current_season = get_current_season()
        games = scrape_schedule(current_season)
        
        if not games:
            return "No games found. Check the logs for error details.", 500
        
        return Response(
            '<html><head><title>Debug Info</title>'
            '<style>'
            'body { font-family: Arial, sans-serif; padding: 20px; }'
            'table { border-collapse: collapse; width: 100%; }'
            'th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }'
            'tr:nth-child(even) { background-color: #f2f2f2; }'
            'th { background-color: #00356b; color: white; }'
            'h1 { color: #00356b; }'
            '.season-selector { margin: 20px 0; }'
            '</style>'
            '</head><body>'
            f'<h1>Yale Football Schedule - Debug Info (Season {current_season})</h1>'
            '<div class="season-selector">'
            '<p>View a different season: '
            '<a href="/season/2023">2023</a> | '
            '<a href="/season/2024">2024</a> | '
            '<a href="/season/2025">2025</a>'
            '</p></div>'
            '<p>This page shows the raw data extracted with improved parsing and fallback data.</p>'
            '<table>'
            '<tr><th>Game</th><th>Date</th><th>Time</th><th>Location</th><th>Broadcast</th></tr>'
            + ''.join([
                f'<tr><td>{g["title"]}</td><td>{g["date_str"]}</td><td>{g["time_str"]}</td>'
                f'<td>{g["location"]}</td><td>{g["broadcast"]}</td></tr>'
                for g in games
            ])
            + '</table>'
            '<p>Total games found: ' + str(len(games)) + '</p>'
            '</body></html>',
            mimetype='text/html'
        )
    except Exception as e:
        return f"Error: {str(e)}", 500

@app.route('/season/<int:year>')
def set_season(year):
    """Allow changing the season via URL"""
    try:
        # Validate year is reasonable (between 2020 and current year + 2)
        current_year = datetime.datetime.now().year
        if year < 2020 or year > current_year + 2:
            return f"Invalid season year: {year}. Must be between 2020 and {current_year + 2}", 400
        
        logger.info(f"Manual season change request to {year}")
        
        # Scrape the specified season
        games = scrape_schedule(year)
        
        # Create/update the calendar
        if games:
            create_calendar(games)
            return f"Calendar updated for season {year}. Found {len(games)} games. <a href='/calendar.ics'>Download Calendar</a>", 200
        else:
            return f"No games found for season {year}. Please check the logs for details.", 500
            
    except Exception as e:
        logger.error(f"Error processing season {year}: {str(e)}")
        return f"Error: {str(e)}", 500

if __name__ == "__main__":
    # Display startup information
    current_season = get_current_season()
    logger.info(f"Starting Yale Football Schedule Scraper for season {current_season}")
    logger.info("Using improved parsing with fallback data support")
    
    # Create scheduler for daily updates
    scheduler = BackgroundScheduler()
    
    # Initial calendar creation
    success = update_calendar()
    if not success:
        logger.error("Initial calendar creation failed")
    
    # Schedule daily updates at 3 AM
    scheduler.add_job(update_calendar, 'cron', hour=3)
    scheduler.start()
    
    # Run the Flask app
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
