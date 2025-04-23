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

# Base URLs for Yale football on ESPN
BASE_URL_TEMPLATE = "https://www.espn.com/college-football/team/schedule/_/id/43/season/{season}"
BASE_URL = BASE_URL_TEMPLATE.format(season=datetime.datetime.now().year)
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
        if date_str and not any(str(year) in date_str for year in range(2023, 2026)):
            # Extract year from URL or use current year
            year_from_url = None
            if BASE_URL:
                import re
                year_match = re.search(r'/(\d{4})/?', BASE_URL)
                if year_match:
                    year_from_url = year_match.group(1)
            
            # If we found a year in the URL, use it
            if year_from_url:
                date_str = f"{date_str}, {year_from_url}"
            else:
                # Default to current year
                current_year = datetime.datetime.now().year
                date_str = f"{date_str}, {current_year}"
        
        # Handle various date formats
        # First, try to use dateutil's parser if available
        try:
            from dateutil import parser
            game_date = parser.parse(date_str)
            year, month, day = game_date.year, game_date.month, game_date.day
        except (ImportError, ValueError):
            # Fall back to manual parsing
            date_parts = date_str.replace(",", "").split()
            if len(date_parts) >= 3:
                # Handle formats like "Sat, Nov 1, 2024" or "Nov 1, 2024"
                # Find the month, day, and year elements
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
                    month = month_dict.get(month_str, 1)  # Default to January if not found
                    day = int(day_str)
                    year = int(year_str)
                else:
                    logger.warning(f"Could not extract date components from: {date_str}")
                    now = datetime.datetime.now()
                    month, day, year = now.month, now.day, now.year
            else:
                # If date format is unexpected, log it and use current year/future date
                logger.warning(f"Unexpected date format: {date_str}")
                now = datetime.datetime.now()
                month, day, year = now.month, now.day, now.year
        
        # Parse time (if available)
        hour, minute = 12, 0  # Default to noon
        if time_str and time_str.lower() not in ["tba", "tbd"]:
            # Log for debugging
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
                # Handle cases where time might just be "12 PM" without colon
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
        
        # Sanity check - if the date is way in the future or past, it might be wrong
        now = datetime.datetime.now()
        if abs((game_datetime - now).days) > 365:
            logger.warning(f"Parsed date {game_datetime} is more than a year away from current date. This might be incorrect.")
        
        return game_datetime
    
    except Exception as e:
        logger.error(f"Error parsing date/time: {date_str}, {time_str} - {str(e)}")
        # Return a placeholder date in the future
        return datetime.datetime.now() + datetime.timedelta(days=30)

def scrape_schedule(year=None):
    """Scrape the Yale football schedule and return game details"""
    logger.info("Starting Yale football schedule scraping...")
    games = []
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(BASE_URL, headers=headers)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find the schedule table/elements - try multiple approaches
        # First, try ESPN style (Table__TR)
        schedule_items = soup.select('tr.Table__TR, tr.filled')
        
        # If not found, try the common class names for schedule items
        if not schedule_items:
            schedule_items = soup.select('.sidearm-schedule-games-container .sidearm-schedule-game, .event-row')
        
        # If we couldn't find the elements with the common class names, try some alternatives
        if not schedule_items:
            schedule_items = soup.select('div[id*="schedule"] tr, div[class*="schedule"] .event')
        
        # If still nothing, try a more general approach
        if not schedule_items:
            schedule_items = soup.select('table tbody tr[data-url], div[class*="schedule"] li, div[class*="events"] li')
        
        logger.info(f"Found {len(schedule_items)} potential schedule items")
        
        for item in schedule_items:
            try:
                # Extract date - try ESPN format first, then fallback to others
                date_elem = (
                    item.select_one('[data-testid="date"]') or
                    item.select_one('.sidearm-schedule-game-opponent-date, .event-date, [data-field="date"]') or
                    item.find('span', class_=lambda c: c and 'date' in c.lower()) or
                    item.find('div', class_=lambda c: c and 'date' in c.lower())
                )
                date_str = date_elem.text.strip() if date_elem else ""
                
                # Extract time - try ESPN format first, then fallback
                time_elem = (
                    item.select_one('[data-testid="time"]') or
                    item.select_one('.sidearm-schedule-game-time, .event-time, [data-field="time"]') or
                    item.find('span', class_=lambda c: c and 'time' in c.lower()) or
                    item.find('div', class_=lambda c: c and 'time' in c.lower())
                )
                time_str = time_elem.text.strip() if time_elem else "TBA"
                
                # Clean up time if it contains extra content
                if time_str:
                    # If time contains a link, extract just the time text
                    if time_elem and time_elem.find('a'):
                        time_str = time_elem.find('a').text.strip()
                    # If it's TBD or similar
                    if 'TBD' in time_str:
                        time_str = "TBD"
                
                # Extract opponent - try ESPN format first
                opponent = None
                opponent_container = item.select_one('[data-testid="opponent"]')
                if opponent_container:
                    # Look for the text link inside the opponent container
                    opponent_links = opponent_container.select('a.AnchorLink')
                    if opponent_links:
                        # Get the last link (usually the team name link)
                        opponent_text = opponent_links[-1].text.strip()
                        # Remove any trailing spaces or dashes that ESPN might add
                        opponent = opponent_text.replace('--', '').strip()
                    else:
                        # If no link, try to get text content (excluding "vs" or "at")
                        opponent_text = opponent_container.get_text(strip=True)
                        if opponent_text.lower().startswith('vs '):
                            opponent = opponent_text[3:].strip()
                        elif opponent_text.lower().startswith('at '):
                            opponent = opponent_text[3:].strip()
                        else:
                            opponent = opponent_text
                
                # If still no opponent, try the original selectors
                if not opponent:
                    opponent_elem = (
                        item.select_one('.sidearm-schedule-game-opponent-name, .event-opponent, [data-field="opponent"]') or
                        item.find('span', class_=lambda c: c and ('opponent' in c.lower() or 'team' in c.lower())) or
                        item.find('div', class_=lambda c: c and ('opponent' in c.lower() or 'team' in c.lower()))
                    )
                    opponent = opponent_elem.text.strip() if opponent_elem else "Unknown Opponent"
                
                # Check for home/away indicators
                is_away = False
                is_home = True  # Default to home game
                
                # Check for ESPN style indicators
                vs_indicator = None
                if opponent_container:
                    vs_indicator = opponent_container.select_one('span.pr2')
                
                if vs_indicator and vs_indicator.text.strip().lower() == "at":
                    is_away = True
                    is_home = False
                elif vs_indicator and vs_indicator.text.strip().lower() == "vs":
                    is_home = True
                    is_away = False
                else:
                    # Check traditional indicators
                    location_elem = (
                        item.select_one('.sidearm-schedule-game-location, .event-location, [data-field="location"]') or
                        item.find('span', class_=lambda c: c and 'location' in c.lower()) or
                        item.find('div', class_=lambda c: c and 'location' in c.lower())
                    )
                    location = location_elem.text.strip() if location_elem else ""
                    
                    # Check for specific "at" indicators in the opponent text or location
                    is_away = (
                        "at " in opponent.lower() or 
                        "@ " in opponent.lower() or 
                        "away" in location.lower() or
                        "at " in location.lower()
                    )
                    
                    # If no explicit away indicators, check if class indicates away
                    if not is_away:
                        is_away = "away" in item.get('class', [])
                    
                    is_home = not is_away
                
                # Clean up opponent name (remove "at " prefix if present)
                if opponent.lower().startswith("at "):
                    opponent = opponent[3:].strip()
                elif opponent.lower().startswith("@ "):
                    opponent = opponent[2:].strip()
                
                # Ensure we have a valid opponent name
                if not opponent or opponent == "Unknown Opponent":
                    # Look for any link that might have team info
                    team_links = item.select('a[href*="team"], a[href*="school"]')
                    if team_links:
                        for link in team_links:
                            link_text = link.text.strip()
                            if link_text and link_text not in ["vs", "at", "TBD", "TBA"]:
                                opponent = link_text
                                break
                
                # Extract location (if not already done)
                if not location_elem:
                    location_elem = item.select_one('td:not(:has([data-testid="opponent"])):not(:has([data-testid="date"])):not(:has([data-testid="time"]))')
                location = location_elem.text.strip() if location_elem else ""
                
                # Try to find location in other ways if still empty
                if not location:
                    # Check for venue name in a dedicated element
                    venue_elem = item.select_one('[data-field="venue"], .venue')
                    if venue_elem:
                        location = venue_elem.text.strip()
                    elif is_home:
                        location = "New Haven, Conn.\nYale Bowl, Class of 1954 Field"
                
                # Extract broadcast info
                broadcast_elem = (
                    item.select_one('.sidearm-schedule-game-network, .event-network, [data-field="network"]') or
                    item.find('span', class_=lambda c: c and 'network' in c.lower()) or
                    item.find('div', class_=lambda c: c and 'tv' in c.lower())
                )
                broadcast = broadcast_elem.text.strip() if broadcast_elem else ""
                
                # Create readable title based on home/away status
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
        logger.error(f"Error scraping schedule: {str(e)}")
    
    logger.info(f"Scraped {len(games)} games")
    return games

def create_calendar(games):
    """Create an iCalendar file from the scraped games"""
    cal = Calendar()
    valid_games = 0
    skipped_games = 0
    
    for game in games:
        # Verify that we have all required data before creating an event
        if (game['opponent'] and game['opponent'] != "Unknown Opponent" and
            'start' in game and game['start']):
            
            event = Event()
            event.name = game['title']
            event.begin = game['start']
            event.end = game['end']
            
            if game['location']:
                event.location = game['location']
            
            # Add broadcast info to description
            description = ""
            if game['broadcast']:
                description += f"Broadcast on: {game['broadcast']}\n"
            
            # Add home/away info
            if game['is_home']:
                description += "Home Game"
            else:
                description += "Away Game"
                
            event.description = description
            cal.events.add(event)
            valid_games += 1
            logger.info(f"Added event to calendar: {game['title']} on {game['start']}")
        else:
            # Log games that were skipped due to missing data
            skipped_games += 1
            missing = []
            if not game['opponent'] or game['opponent'] == "Unknown Opponent":
                missing.append("opponent")
            if not game.get('start'):
                missing.append("date/time")
            
            logger.warning(f"Skipped game due to missing {', '.join(missing)}: {game.get('title', 'Unknown')}")
    
    # Save to file using the serialize() method instead of str()
    with open(CALENDAR_FILE, 'w') as f:
        f.write(cal.serialize())
    
    logger.info(f"Calendar created with {valid_games} events (skipped {skipped_games} incomplete entries)")
    return cal

def update_calendar(custom_season_url=None):
    """Update the football calendar, optionally using a custom season URL"""
    try:
        games = scrape_schedule(custom_season_url)
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
        return Response(cal_content, mimetype='text/calendar')
    except Exception as e:
        logger.error(f"Error serving calendar: {str(e)}")
        return "Calendar not available", 500

@app.route('/')
def index():
    """Simple landing page"""
    # Get current season for display
    current_season = get_current_season()
    
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
                .season-selector {
                    margin-top: 20px;
                    padding: 10px;
                    background-color: #f0f0f0;
                    border-radius: 5px;
                }
            </style>
        </head>
        <body>
            <h1>Yale Football Calendar</h1>
            <div class="container">
                <p>This calendar provides a schedule of Yale Football games that you can add to your calendar app.</p>
                <p>To subscribe to this calendar in your calendar app, use this URL:</p>
                <pre>""" + request.url_root + """calendar.ics</pre>
                <p><a href="/calendar.ics">Download Calendar</a></p>
                <p>The calendar updates daily with the latest game information from ESPN.</p>
                <p>Current season: """ + str(current_season) + """</p>
                
                <div class="season-selector">
                    <p><strong>View a different season:</strong></p>
                    <p>
                        <a href="/season/2023">2023</a> | 
                        <a href="/season/2024">2024</a> | 
                        <a href="/season/2025">2025</a>
                    </p>
                    <p><a href="/debug">View Debug Information</a></p>
                </div>
            </div>
            <div class="footer">
                <p>Data sourced from ESPN. Updated daily.</p>
                <p>This service is not affiliated with Yale University.</p>
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
        games = scrape_schedule()
        
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
            '<p>This page shows the raw data extracted from the ESPN website.</p>'
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
        
        # Build the specific URL for this season
        season_url = f"https://www.espn.com/college-football/team/schedule/_/id/43/season/{year}"
        logger.info(f"Manual season change request to {year}. Using URL: {season_url}")
        
        # Scrape the specified season using the custom URL
        games = scrape_schedule(custom_season_url=season_url)
        
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
    logger.info(f"Using ESPN URL: {BASE_URL_TEMPLATE.format(season=current_season)}")
    
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