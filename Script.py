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
import unittest.mock
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

# Base URLs for Yale football
YALE_BASE_URL = "https://yalebulldogs.com/sports/football/schedule/{season}"
ESPN_BASE_URL_TEMPLATE = "https://www.espn.com/college-football/team/schedule/_/id/43/season/{season}"
CALENDAR_FILE = "yale_football.ics"

def get_current_season():
    """Get the current football season based on the current date"""
    today = datetime.datetime.now()
    # If we're after February 1st, use current year, otherwise use previous year
    if today.month > 2:
        return today.year
    else:
        return today.year - 1

def parse_date_time(date_str, time_str):
    """Parse date and time strings into datetime object"""
    try:
        # Log raw values for debugging
        logger.debug(f"Raw date: '{date_str}', Raw time: '{time_str}'")
        
        # Clean up the input strings
        date_str = date_str.strip()
        time_str = time_str.strip() if time_str else ""
        
        # Check if year is missing from date_str
        if date_str and not any(str(year) in date_str for year in range(2023, 2027)):
            current_season = get_current_season()
            date_str = f"{date_str}, {current_season}"
        
        # Handle various date formats
        try:
            from dateutil import parser
            game_date = parser.parse(date_str)
            year, month, day = game_date.year, game_date.month, game_date.day
        except (ImportError, ValueError):
            # Fall back to manual parsing
            date_parts = date_str.replace(",", "").split()
            if len(date_parts) >= 3:
                month_str = None
                day_str = None
                year_str = None
                
                for part in date_parts:
                    if part.isdigit() and len(part) == 4:  # Year (4 digits)
                        year_str = part
                    elif part.isdigit() and int(part) <= 31:  # Day
                        day_str = part
                    elif not part.isdigit() and len(part) >= 3:  # Month name
                        month_str = part[:3]  # Take first 3 letters
                
                # Convert month name to number
                month_dict = {
                    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
                    'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
                }
                
                if month_str and day_str and year_str:
                    month = month_dict.get(month_str, 1)
                    day = int(day_str)
                    year = int(year_str)
                else:
                    logger.warning(f"Could not extract date components from: {date_str}")
                    now = datetime.datetime.now()
                    month, day, year = now.month, now.day, now.year
            else:
                logger.warning(f"Unexpected date format: {date_str}")
                now = datetime.datetime.now()
                month, day, year = now.month, now.day, now.year
        
        # Parse time (if available)
        hour, minute = 12, 0  # Default to noon
        if time_str and time_str.lower() not in ["tba", "tbd", ""]:
            logger.debug(f"Parsing time: '{time_str}'")
            
            # Handle AM/PM
            is_pm = "PM" in time_str.upper() or "P.M." in time_str.upper()
            is_am = "AM" in time_str.upper() or "A.M." in time_str.upper()
            
            # Clean the time string
            clean_time = time_str.upper().replace("AM", "").replace("PM", "").replace("A.M.", "").replace("P.M.", "").strip()
            
            if ":" in clean_time:
                time_parts = clean_time.split(':')
                hour = int(time_parts[0])
                minute = int(time_parts[1]) if len(time_parts) > 1 else 0
            else:
                try:
                    hour = int(clean_time) if clean_time.isdigit() else 12
                    minute = 0
                except ValueError:
                    logger.warning(f"Could not parse time: {time_str}, using default noon")
                    hour, minute = 12, 0
            
            # Adjust for PM
            if is_pm and hour < 12:
                hour += 12
            # Adjust for 12 AM
            if is_am and hour == 12:
                hour = 0
        
        # Create the datetime object
        game_datetime = datetime.datetime(year, month, day, hour, minute)
        logger.debug(f"Parsed datetime: {game_datetime}")
        
        return game_datetime
    
    except Exception as e:
        logger.error(f"Error parsing date/time: {date_str}, {time_str} - {str(e)}")
        # Return a placeholder date in the future
        return datetime.datetime.now() + datetime.timedelta(days=30)

def scrape_yale_schedule(season=None):
    """Scrape from the official Yale Bulldogs website"""
    if season is None:
        season = get_current_season()
    
    logger.info(f"Scraping Yale Bulldogs website for season {season}")
    games = []
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        url = YALE_BASE_URL.format(season=season)
        logger.info(f"Fetching from: {url}")
        
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Look for Yale's schedule structure
        # Try different selectors that Yale might use
        schedule_items = []
        
        # Look for Sidearm schedule items (common in college athletics)
        schedule_items = soup.select('.sidearm-schedule-game')
        
        if not schedule_items:
            # Try alternative selectors
            schedule_items = soup.select('.schedule-item, .game-item, .event-item')
        
        if not schedule_items:
            # Try table-based structure
            schedule_items = soup.select('table.schedule tbody tr, .schedule-table tbody tr')
        
        if not schedule_items:
            # Try div-based structure with data attributes
            schedule_items = soup.select('div[data-game], div[data-event]')
        
        if not schedule_items:
            # Generic fallback - look for any structure containing game data
            schedule_items = soup.select('div:has(.date):has(.opponent), tr:has(.date):has(.opponent)')
        
        logger.info(f"Found {len(schedule_items)} potential schedule items on Yale site")
        
        for item in schedule_items:
            try:
                # Extract date information
                date_elem = (
                    item.select_one('.sidearm-schedule-game-opponent-date, .game-date, .event-date, .date') or
                    item.find('div', class_=lambda c: c and 'date' in c.lower()) or
                    item.find('span', class_=lambda c: c and 'date' in c.lower()) or
                    item.find('td', class_=lambda c: c and 'date' in c.lower())
                )
                
                date_str = ""
                if date_elem:
                    # Look for nested date elements
                    month_elem = date_elem.select_one('.month, .sidearm-schedule-game-opponent-date-month')
                    day_elem = date_elem.select_one('.day, .sidearm-schedule-game-opponent-date-day')
                    
                    if month_elem and day_elem:
                        month_text = month_elem.get_text(strip=True)
                        day_text = day_elem.get_text(strip=True)
                        date_str = f"{month_text} {day_text}"
                    else:
                        date_str = date_elem.get_text(strip=True)
                
                # Extract time information
                time_elem = (
                    item.select_one('.sidearm-schedule-game-opponent-time, .game-time, .event-time, .time') or
                    item.find('div', class_=lambda c: c and 'time' in c.lower()) or
                    item.find('span', class_=lambda c: c and 'time' in c.lower()) or
                    item.find('td', class_=lambda c: c and 'time' in c.lower())
                )
                time_str = time_elem.get_text(strip=True) if time_elem else "TBA"
                
                # Extract opponent information
                opponent_elem = (
                    item.select_one('.sidearm-schedule-game-opponent-name, .opponent, .team-name') or
                    item.find('div', class_=lambda c: c and 'opponent' in c.lower()) or
                    item.find('span', class_=lambda c: c and 'opponent' in c.lower()) or
                    item.find('td', class_=lambda c: c and 'opponent' in c.lower())
                )
                opponent = opponent_elem.get_text(strip=True) if opponent_elem else "Unknown Opponent"
                
                # Clean up opponent name
                opponent = re.sub(r'^(vs\.?\s*|at\s*|@\s*)', '', opponent, flags=re.IGNORECASE).strip()
                
                # Extract location information
                location_elem = (
                    item.select_one('.sidearm-schedule-game-location, .location, .venue') or
                    item.find('div', class_=lambda c: c and 'location' in c.lower()) or
                    item.find('span', class_=lambda c: c and 'location' in c.lower()) or
                    item.find('td', class_=lambda c: c and 'location' in c.lower())
                )
                location = location_elem.get_text(strip=True) if location_elem else ""
                
                # Extract broadcast information
                broadcast_elem = (
                    item.select_one('.sidearm-schedule-game-links a, .broadcast, .tv, .stream') or
                    item.find('div', class_=lambda c: c and ('tv' in c.lower() or 'broadcast' in c.lower())) or
                    item.find('a', string=re.compile(r'ESPN|Fox|CBS|NBC|Stream|Watch', re.I))
                )
                broadcast = broadcast_elem.get_text(strip=True) if broadcast_elem else ""
                
                # Determine if it's a home or away game
                is_home = True  # Default assumption
                is_away = False
                
                # Check for home/away indicators
                home_away_elem = item.select_one('.sidearm-schedule-game-home-away, .home-away')
                if home_away_elem:
                    home_away_text = home_away_elem.get_text(strip=True).lower()
                    is_away = 'away' in home_away_text or 'at' in home_away_text
                    is_home = not is_away
                else:
                    # Check location for away indicators
                    if location and ('away' in location.lower() or 'at ' in location.lower()):
                        is_away = True
                        is_home = False
                
                # Set default location for home games
                if is_home and not location:
                    location = "New Haven, Conn.\nYale Bowl, Class of 1954 Field"
                
                # Create readable title
                if is_home:
                    title = f"{opponent} at Yale"
                else:
                    title = f"Yale at {opponent}"
                
                # Skip items without date information
                if not date_str:
                    logger.warning(f"Skipping item without date information: {title}")
                    continue
                
                # Get datetime object
                game_datetime = parse_date_time(date_str, time_str)
                
                # Game duration (default 3.5 hours)
                duration = datetime.timedelta(hours=3, minutes=30)
                
                game_info = {
                    'title': title,
                    'start': game_datetime,
                    'end': game_datetime + duration,
                    'location': location,
                    'broadcast': broadcast,
                    'is_home': is_home,
                    'opponent': opponent,
                    'date_str': date_str,
                    'time_str': time_str
                }
                
                games.append(game_info)
                logger.info(f"Scraped game: {title} on {game_datetime}")
                
            except Exception as e:
                logger.error(f"Error parsing game item: {str(e)}")
                continue
    
    except Exception as e:
        logger.error(f"Error scraping Yale website: {str(e)}")
    
    return games

def scrape_espn_schedule(season=None):
    """Scrape from ESPN as backup"""
    if season is None:
        season = get_current_season()
    
    logger.info(f"Scraping ESPN for season {season}")
    games = []
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        url = ESPN_BASE_URL_TEMPLATE.format(season=season)
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # ESPN schedule parsing (existing logic)
        schedule_items = soup.select('tr.Table__TR, tr.filled')
        
        if not schedule_items:
            schedule_items = soup.select('.sidearm-schedule-games-container .sidearm-schedule-game, .event-row')
        
        logger.info(f"Found {len(schedule_items)} potential schedule items on ESPN")
        
        for item in schedule_items:
            try:
                # ESPN-specific parsing logic (existing code)
                date_elem = item.select_one('[data-testid="date"]')
                date_str = date_elem.text.strip() if date_elem else ""
                
                time_elem = item.select_one('[data-testid="time"]')
                time_str = time_elem.text.strip() if time_elem else "TBA"
                
                # Clean up time if it contains extra content
                if time_str and time_elem and time_elem.find('a'):
                    time_str = time_elem.find('a').text.strip()
                
                # Extract opponent
                opponent = None
                opponent_container = item.select_one('[data-testid="opponent"]')
                if opponent_container:
                    opponent_links = opponent_container.select('a.AnchorLink')
                    if opponent_links:
                        opponent_text = opponent_links[-1].text.strip()
                        opponent = opponent_text.replace('--', '').strip()
                    else:
                        opponent_text = opponent_container.get_text(strip=True)
                        if opponent_text.lower().startswith('vs '):
                            opponent = opponent_text[3:].strip()
                        elif opponent_text.lower().startswith('at '):
                            opponent = opponent_text[3:].strip()
                        else:
                            opponent = opponent_text
                
                if not opponent:
                    opponent = "Unknown Opponent"
                
                # Check for home/away indicators
                is_away = False
                is_home = True
                
                vs_indicator = None
                if opponent_container:
                    vs_indicator = opponent_container.select_one('span.pr2')
                
                if vs_indicator and vs_indicator.text.strip().lower() == "at":
                    is_away = True
                    is_home = False
                elif vs_indicator and vs_indicator.text.strip().lower() == "vs":
                    is_home = True
                    is_away = False
                
                # Clean up opponent name
                if opponent.lower().startswith("at "):
                    opponent = opponent[3:].strip()
                elif opponent.lower().startswith("@ "):
                    opponent = opponent[2:].strip()
                
                # Set location
                location = ""
                if is_home:
                    location = "New Haven, Conn.\nYale Bowl, Class of 1954 Field"
                
                # Extract broadcast info (ESPN doesn't always have this easily accessible)
                broadcast = ""
                
                # Create readable title
                if is_home:
                    title = f"{opponent} at Yale"
                else:
                    title = f"Yale at {opponent}"
                
                # Skip items without date information
                if not date_str:
                    logger.warning(f"Skipping item without date information: {title}")
                    continue
                
                # Get datetime object
                game_datetime = parse_date_time(date_str, time_str)
                
                # Game duration (default 3.5 hours)
                duration = datetime.timedelta(hours=3, minutes=30)
                
                game_info = {
                    'title': title,
                    'start': game_datetime,
                    'end': game_datetime + duration,
                    'location': location,
                    'broadcast': broadcast,
                    'is_home': is_home,
                    'opponent': opponent,
                    'date_str': date_str,
                    'time_str': time_str
                }
                
                games.append(game_info)
                logger.info(f"Scraped game from ESPN: {title} on {game_datetime}")
                
            except Exception as e:
                logger.error(f"Error parsing ESPN game item: {str(e)}")
                continue
    
    except Exception as e:
        logger.error(f"Error scraping ESPN schedule: {str(e)}")
    
    return games

def scrape_schedule(season=None):
    """Scrape the Yale football schedule, trying Yale first, then ESPN as backup"""
    logger.info("Starting Yale football schedule scraping...")
    
    # Try Yale website first
    games = scrape_yale_schedule(season)
    
    # If Yale scraping didn't work or returned no games, try ESPN as backup
    if not games:
        logger.info("Yale website scraping failed or returned no games, trying ESPN as backup...")
        games = scrape_espn_schedule(season)
    
    logger.info(f"Total scraped games: {len(games)}")
    return games

def create_calendar(games):
    """Create an iCalendar file from the scraped games"""
    cal = Calendar()
    
    # Update the PRODID to use raw.githubusercontent.com for direct file access
    cal._prodid = "Yale Football Schedule - https://raw.githubusercontent.com/LordOfTheTrees/YaleFootballSchedule/main/yale_football.ics"
    
    for game in games:
        event = Event()
        event.name = game['title']
        event.begin = game['start']
        event.end = game['end']
        event.location = game['location']
        
        # Add broadcast info to description
        description = ""
        if game['broadcast']:
            description += f"Broadcast/Stream: {game['broadcast']}\n"
        
        # Add home/away info
        if game['is_home']:
            description += "Home Game"
        else:
            description += "Away Game"
            
        # Add opponent info
        if game['opponent']:
            description += f"\nOpponent: {game['opponent']}"
            
        event.description = description
        cal.events.add(event)
    
    # Save to file
    with open(CALENDAR_FILE, 'w') as f:
        f.write(str(cal))
    
    logger.info(f"Calendar created with {len(games)} events")
    return cal

def update_calendar(custom_season=None):
    """Update the football calendar"""
    try:
        games = scrape_schedule(custom_season)
        create_calendar(games)
        logger.info("Calendar updated successfully")
    except Exception as e:
        logger.error(f"Error updating calendar: {str(e)}")

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
    """Simple landing page"""
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
                <p>The calendar updates daily with the latest game information from the Yale Bulldogs website.</p>
            </div>
            <div class="footer">
                <p>Data sourced from yalebulldogs.com with ESPN as backup. Updated daily.</p>
                <p>This service is not affiliated with Yale University.</p>
                <p>Source code available on <a href="https://github.com/LordOfTheTrees/YaleFootballSchedule">GitHub</a>.</p>
            </div>
        </body>
    </html>
    """

@app.route('/debug')
def debug_info():
    """Show debugging information"""
    try:
        # Get current season
        current_season = get_current_season()
        
        # Scrape the schedule for the current season
        games = scrape_schedule(current_season)
        
        # Check if we got any games
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
            '<p>This page shows the raw data extracted from yalebulldogs.com (with ESPN as backup).</p>'
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
        # Validate year is reasonable (between 2000 and current year + 1)
        current_year = datetime.datetime.now().year
        if year < 2000 or year > current_year + 1:
            return f"Invalid season year: {year}. Must be between 2000 and {current_year + 1}", 400
        
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
    logger.info(f"Primary source: {YALE_BASE_URL.format(season=current_season)}")
    logger.info(f"Backup source: {ESPN_BASE_URL_TEMPLATE.format(season=current_season)}")
    
    # Create scheduler for daily updates
    scheduler = BackgroundScheduler()
    
    # Initial calendar creation
    update_calendar()
    
    # Schedule daily updates at 3 AM
    scheduler.add_job(update_calendar, 'cron', hour=3)
    scheduler.start()
    
    # Run the Flask app
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
