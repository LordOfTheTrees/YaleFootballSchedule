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
        logger.info(f"Parsing date: '{date_str}', time: '{time_str}'")
        
        # First, determine the current football season year
        today = datetime.datetime.now()
        current_year = today.year
        current_month = today.month
        
        # If we're in February or later, we're likely looking at the upcoming season
        if current_month >= 2:
            football_season_year = current_year
        else:
            football_season_year = current_year - 1
            
        logger.info(f"Current date: {today}, determined football season year: {football_season_year}")
        
        # Clean up the input date string
        # Fix cases like "SaturdayNov 22" where day of week and month are joined
        weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        cleaned_date_str = date_str
        
        # Try to split day of week from month when they're joined
        for day in weekdays:
            if day.lower() in date_str.lower():
                # Find where the weekday ends
                day_pos = date_str.lower().find(day.lower()) + len(day)
                if day_pos < len(date_str):
                    # Insert a space after the weekday if there isn't one
                    if date_str[day_pos:day_pos+1] != ' ':
                        cleaned_date_str = date_str[:day_pos] + ' ' + date_str[day_pos:]
                        logger.info(f"Fixed joined weekday-month: '{date_str}' -> '{cleaned_date_str}'")
                        break
        
        date_str = cleaned_date_str
        
        # Extract year, month, day
        year = None
        month = None
        day = None
        
        # Try various date formats
        # Format 1: MM/DD/YYYY
        if "/" in date_str and len(date_str.split("/")) >= 2:
            parts = date_str.strip().split("/")
            try:
                month = int(parts[0])
                day = int(parts[1])
                if len(parts) > 2 and parts[2].strip():
                    year = int(parts[2])
                    if year < 100:
                        year += 2000
            except (ValueError, IndexError):
                logger.warning(f"Failed to parse MM/DD/YYYY format: {date_str}")
        
        # Format 2: Month + Day pattern
        # This format handles various cases like:
        # - "April 26, 2025"
        # - "April 26" 
        # - "Apr 26"
        # - "Saturday Apr 26"
        # - "Saturday, Apr 26"
        else:
            # Define month mapping
            month_map = {
                'january': 1, 'jan': 1,
                'february': 2, 'feb': 2,
                'march': 3, 'mar': 3,
                'april': 4, 'apr': 4,
                'may': 5,
                'june': 6, 'jun': 6,
                'july': 7, 'jul': 7,
                'august': 8, 'aug': 8,
                'september': 9, 'sep': 9, 'sept': 9,
                'october': 10, 'oct': 10,
                'november': 11, 'nov': 11,
                'december': 12, 'dec': 12
            }
            
            # Try to find any month name in the string
            for month_name, month_num in month_map.items():
                if month_name.lower() in date_str.lower():
                    month = month_num
                    logger.info(f"Found month '{month_name}' -> {month}")
                    
                    # Now find the day number that follows the month
                    month_pos = date_str.lower().find(month_name.lower())
                    after_month = date_str[month_pos + len(month_name):]
                    
                    # Find day after month
                    day_match = re.search(r'\b(\d{1,2})\b', after_month)
                    if day_match:
                        day = int(day_match.group(1))
                        logger.info(f"Found day: {day}")
                    else:
                        # If day is not after month, try to find any number in the string
                        day_match = re.search(r'\b(\d{1,2})\b', date_str)
                        if day_match:
                            day = int(day_match.group(1))
                            logger.info(f"Found day anywhere in string: {day}")
                    
                    # Look for year in the string
                    year_match = re.search(r'\b(20\d{2})\b', date_str)
                    if year_match:
                        year = int(year_match.group(1))
                        logger.info(f"Found year in string: {year}")
                    
                    break
        
        # If we have month and day but no year, determine year based on football season
        if month is not None and day is not None:
            if year is None:
                # For college football:
                # Spring games (April) are in the next calendar year
                # August-December games are in the current football season year
                # January games (bowl games) are in the next calendar year
                if month == 4:  # April (spring game)
                    year = football_season_year + 1
                elif month >= 8:  # August-December
                    year = football_season_year
                else:  # January-July (except April)
                    year = football_season_year + 1
                
                logger.info(f"Determined year {year} based on month {month} and football season")
            
            # Parse time (if available)
            if time_str and time_str.lower() not in ["tba", "tbd"]:
                # Handle AM/PM
                time_str = time_str.strip().upper()
                
                # Extract the time part if there's additional text
                time_match = re.search(r'(\d+:?\d*\s*[AP]M)', time_str)
                if time_match:
                    time_str = time_match.group(1)
                
                if ":" in time_str:
                    time_parts = time_str.replace("AM", "").replace("PM", "").strip().split(':')
                    hour = int(time_parts[0])
                    minute = int(time_parts[1]) if len(time_parts) > 1 else 0
                else:
                    # Handle cases where time might just be "12 PM" without colon
                    hour_match = re.search(r'(\d+)\s*[AP]M', time_str)
                    if hour_match:
                        hour = int(hour_match.group(1))
                        minute = 0
                    else:
                        hour, minute = 13, 0  # Default to 1 PM
                
                # Adjust for PM
                if "PM" in time_str and hour < 12:
                    hour += 12
                # Adjust for 12 AM
                if "AM" in time_str and hour == 12:
                    hour = 0
            else:
                # If time is TBA or TBD, use 1 PM ET (13:00 local)
                hour, minute = 13, 0
            
            # Create the datetime object
            try:
                game_datetime = datetime.datetime(year, month, day, hour, minute)
                logger.info(f"Final parsed date and time: {game_datetime}")
                return game_datetime
            except ValueError as e:
                logger.error(f"Invalid date components: year={year}, month={month}, day={day}, hour={hour}, minute={minute}")
                logger.error(f"ValueError: {str(e)}")
                raise
        else:
            logger.warning(f"Failed to extract complete date from: {date_str}")
            raise ValueError(f"Could not parse date from: {date_str}")
    
    except Exception as e:
        logger.error(f"Error parsing date/time: {date_str}, {time_str} - {str(e)}")
        # Return None to indicate parsing failure, caller should handle this
        return None

def scrape_schedule(custom_season_url=None):
    """Scrape the Yale football schedule and return game details
    
    Args:
        custom_season_url: Optional URL to use instead of the current season URL
    """
    logger.info("Starting schedule scraping...")
    games = []
    
    try:
        # Get the current season or use a specified season
        season = get_current_season()
        
        if custom_season_url:
            base_url = custom_season_url
            logger.info(f"Using custom URL: {base_url}")
        else:
            base_url = BASE_URL_TEMPLATE.format(season=season)
            logger.info(f"Using season {season} for schedule URL: {base_url}")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(base_url, headers=headers)
        response.raise_for_status()
        
        logger.info(f"Successfully fetched page. Status code: {response.status_code}")
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # ESPN-specific selectors for the schedule table and items
        schedule_items = []
        
        # Try to find the schedule table
        # ESPN typically uses tables for their schedules
        schedule_table = soup.select_one('table.Table')
        
        if schedule_table:
            logger.info("Found ESPN schedule table")
            # Get the rows from the table
            schedule_items = schedule_table.select('tbody tr')
            logger.info(f"Found {len(schedule_items)} schedule rows in ESPN table")
        
        # If we didn't find the table or rows, try alternative selectors
        if not schedule_items:
            # Try alternative table selectors
            for selector in ['table.schedule-table', 'table.schedule', '.Schedule__Table']:
                table = soup.select_one(selector)
                if table:
                    schedule_items = table.select('tbody tr, tr.Table__TR')
                    logger.info(f"Found {len(schedule_items)} items using alternative table selector: {selector}")
                    break
                    
        # If still no items, try more generic selectors
        if not schedule_items:
            all_selectors = [
                '.Table__TR',  # ESPN's typical row class
                'div[class*="event-cell"]', 
                'div[class*="game-row"]',
                'div[class*="schedule-row"]',
                'li[class*="schedule-item"]'
            ]
            
            for selector in all_selectors:
                items = soup.select(selector)
                if items:
                    logger.info(f"Found {len(items)} items using direct selector: {selector}")
                    schedule_items = items
                    break
        
        # If no valid items found after all attempts, log the error but do not use fallback data
        if not schedule_items:
            logger.error("No schedule items found on the ESPN page. Please check the URL and HTML structure.")
            logger.error("No fallback data will be used. Please update the scraping selectors.")
            return games  # Return empty list instead of using fallback data
        
        # If we found schedule items, process them
        if schedule_items:
            logger.info(f"Processing {len(schedule_items)} schedule items")
            
            for item in schedule_items:
                try:
                    # Dump the HTML of the first few items for debugging
                    if len(games) < 2:
                        logger.info(f"Sample item HTML: {item}")
                    
                    # ESPN-specific extraction
                    # In ESPN's format, typically:
                    # - First column: Date
                    # - Opponent column: Opponent name with "vs" or "@" prefix
                    # - Time column: Game time
                    # - TV column: Network information
                    
                    # Extract date
                    date_str = ""
                    
                    # ESPN specific date selectors
                    espn_date_selectors = [
                        'td[class*="date"]',
                        'span[class*="date"]',
                        'div[class*="date"]',
                        'td:first-child'  # Often the first column is the date
                    ]
                    
                    for selector in espn_date_selectors:
                        date_elem = item.select_one(selector)
                        if date_elem and date_elem.text.strip():
                            date_str = date_elem.text.strip()
                            logger.info(f"Found ESPN date: {date_str}")
                            break
                    
                    # If still no date, try generic methods
                    if not date_str:
                        # Look for any cell with date-like content
                        for cell in item.select('td, th, div'):
                            cell_text = cell.text.strip()
                            
                            # Check if the text matches date patterns
                            date_patterns = [
                                r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}(?:,? \d{4})?\b',
                                r'\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b',
                                r'\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\.?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}\b'
                            ]
                            
                            for pattern in date_patterns:
                                if re.search(pattern, cell_text, re.IGNORECASE):
                                    date_str = cell_text
                                    logger.info(f"Found date from pattern: {date_str}")
                                    break
                            
                            if date_str:
                                break
                    
                    # Skip if no date found
                    if not date_str:
                        logger.warning("No date found, skipping this item")
                        continue
                    
                    # Extract opponent and determine home/away
                    opponent = "Unknown Opponent"
                    is_home = True
                    
                    # ESPN typically has opponent in cells with team links or logos
                    opponent_elem = item.select_one('td a[href*="team"], td[class*="opponent"], td[class*="team"], a[class*="team-name"]')
                    
                    if opponent_elem:
                        opponent_text = opponent_elem.text.strip()
                        # Exclude Yale from being identified as opponent (since this is Yale's schedule)
                        if "yale" not in opponent_text.lower():
                            opponent = opponent_text
                            logger.info(f"Found ESPN opponent: {opponent}")
                    
                    # If no specific opponent element found or opponent is Yale, try to extract from any cell
                    if opponent == "Unknown Opponent" or "yale" in opponent.lower():
                        for cell in item.select('td, th, div'):
                            cell_text = cell.text.strip()
                            
                            # Skip cells that only contain Yale
                            if cell_text.lower() == "yale" or cell_text == "":
                                continue
                            
                            # Check for "vs" or "@" prefixes which often indicate opponents
                            opponent_patterns = [
                                r'(?:vs\.?|versus)\s+([A-Za-z\s&\.\']+)',
                                r'(?:at|@)\s+([A-Za-z\s&\.\']+)',
                                r'^([A-Za-z\s&\.\']+)$'  # Just a team name
                            ]
                            
                            for pattern in opponent_patterns:
                                match = re.search(pattern, cell_text, re.IGNORECASE)
                                if match:
                                    potential_opponent = match.group(1).strip()
                                    # Make sure we're not setting Yale as the opponent
                                    if "yale" not in potential_opponent.lower():
                                        opponent = potential_opponent
                                        # If it has "at" or "@", it's an away game
                                        if re.search(r'at|@', cell_text, re.IGNORECASE):
                                            is_home = False
                                        logger.info(f"Found opponent from text: {opponent}, is_home: {is_home}")
                                        break
                            
                            if opponent != "Unknown Opponent" and "yale" not in opponent.lower():
                                break
                    
                    # Check for home/away indicators
                    home_away_elem = None
                    for cell in item.select('td, span, div'):
                        cell_text = cell.text.lower()
                        if 'vs' in cell_text and 'yale' not in cell_text:
                            is_home = True
                            home_away_elem = cell
                            break
                        elif 'at ' in cell_text or '@ ' in cell_text:
                            # If it says "at Yale", it's a home game
                            if 'yale' in cell_text:
                                is_home = True
                            else:
                                is_home = False
                            home_away_elem = cell
                            break
                    
                    # Parse location from home/away element if found
                    location = ""
                    if home_away_elem:
                        location_match = re.search(r'(?:at|@)\s+(.+?)(?:\s*\(|$)', home_away_elem.text)
                        if location_match:
                            location = location_match.group(1).strip()
                            logger.info(f"Extracted location: {location}")
                    
                    # If no location found yet, look for specific location elements
                    if not location:
                        location_elem = item.select_one('td[class*="location"], span[class*="location"], div[class*="venue"]')
                        if location_elem:
                            location = location_elem.text.strip()
                            logger.info(f"Found location element: {location}")
                    
                    # Set appropriate location based on home/away
                    if not location:
                        if is_home:
                            location = "Yale Bowl, New Haven, CT"
                        else:
                            # For away games, try to determine the opponent's venue
                            if opponent != "Unknown Opponent":
                                location = f"{opponent} venue"  # Generic placeholder
                    
                    # Extract time
                    time_str = ""
                    
                    # ESPN specific time selectors
                    time_elem = item.select_one('td[class*="time"], span[class*="time"], div[class*="time"]')
                    if time_elem:
                        time_str = time_elem.text.strip()
                        logger.info(f"Found ESPN time: {time_str}")
                    else:
                        # Look for time patterns in any cell
                        for cell in item.select('td, th, div'):
                            cell_text = cell.text.strip()
                            
                            time_patterns = [
                                r'\b\d{1,2}:\d{2}\s*[AP]M\b',
                                r'\b\d{1,2}\s*[AP]M\b',
                                r'\bTBA\b',
                                r'\bTBD\b'
                            ]
                            
                            for pattern in time_patterns:
                                match = re.search(pattern, cell_text, re.IGNORECASE)
                                if match:
                                    time_str = match.group(0)
                                    logger.info(f"Found time from pattern: {time_str}")
                                    break
                            
                            if time_str:
                                break
                    
                    # If still no time, default to TBA
                    if not time_str:
                        time_str = "TBA"
                        logger.info("No time found, defaulting to TBA")
                    
                    # Extract broadcast info (TV network)
                    broadcast = ""
                    
                    # ESPN specific broadcast selectors
                    broadcast_elem = item.select_one('td[class*="network"], span[class*="network"], div[class*="broadcast"]')
                    if broadcast_elem:
                        broadcast = broadcast_elem.text.strip()
                        logger.info(f"Found ESPN broadcast: {broadcast}")
                    
                    # If no broadcast info, default to TBA
                    if not broadcast:
                        broadcast = "TBA"
                    
                    # Clean up opponent name (remove any "vs" or "@" prefixes)
                    opponent = re.sub(r'^(?:vs\.?|versus|at|@)\s+', '', opponent).strip()
                    
                    # Make sure opponent is not Yale
                    if "yale" in opponent.lower():
                        logger.warning(f"Opponent appears to be Yale, which is incorrect. Skipping: {opponent}")
                        continue
                    
                    # Create game title - ALWAYS include Yale
                    if is_home:
                        title = f"{opponent} at Yale"
                    else:
                        title = f"Yale at {opponent}"
                    
                    # Get datetime object
                    game_datetime = parse_date_time(date_str, time_str)
                    
                    # Skip games with invalid dates
                    if game_datetime is None:
                        logger.warning(f"Skipping game with invalid date: {title} on {date_str}")
                        continue
                    
                    # Game duration (3.5 hours)
                    duration = datetime.timedelta(hours=3, minutes=30)
                    
                    # Don't add the game if essential information is missing
                    if not opponent or opponent == "Unknown Opponent" or "yale" in opponent.lower():
                        logger.warning(f"Skipping game with missing or invalid opponent on {date_str}")
                        continue

                    # Create game info
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
                    
                    # Verify that Yale is in the title
                    if "yale" not in game_info['title'].lower():
                        logger.warning(f"Yale not found in title: {game_info['title']}. Fixing.")
                        if game_info['is_home']:
                            game_info['title'] = f"{opponent} at Yale"
                        else:
                            game_info['title'] = f"Yale at {opponent}"
                    
                    games.append(game_info)
                    logger.info(f"Added game: {game_info['title']} on {game_datetime}")
                
                except Exception as e:
                    logger.error(f"Error processing game item: {str(e)}")
                    continue
    
    except Exception as e:
        logger.error(f"Error scraping schedule: {str(e)}")
    
    # Deduplicate games (in case we have overlaps between scraped and known games)
    deduplicated_games = []
    seen_games = set()
    
    for game in games:
        # Create a unique identifier based on date and opponent
        game_id = f"{game['start'].date()}_{game['opponent']}"
        
        if game_id not in seen_games:
            seen_games.add(game_id)
            deduplicated_games.append(game)
            logger.info(f"Keeping unique game: {game['title']} on {game['start'].date()}")
        else:
            logger.info(f"Skipping duplicate game: {game['title']} on {game['start'].date()}")
    
    logger.info(f"Deduplicated from {len(games)} to {len(deduplicated_games)} games")
    return deduplicated_games

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