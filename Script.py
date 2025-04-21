import requests
from bs4 import BeautifulSoup
import ics
from ics import Calendar, Event
import datetime
import time
import os
from flask import Flask, Response
import logging
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

# Base URL for Yale football
BASE_URL = "https://yalebulldogs.com/sports/football/schedule/2024"
CALENDAR_FILE = "yale_football.ics"

def parse_date_time(date_str, time_str):
    """Parse date and time strings into datetime object"""
    try:
        # Handle various date/time formats that might appear on the website
        # Expected format is something like "Sep 21, 2024" and "1:00 PM"
        
        # Parse date
        date_parts = date_str.strip().replace(",", "").split()
        if len(date_parts) >= 3:
            month_str, day_str, year_str = date_parts[0], date_parts[1], date_parts[2]
            
            # Convert month name to number
            month_dict = {
                'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
                'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
            }
            
            month = month_dict.get(month_str[:3], 1)  # Default to January if not found
            day = int(day_str)
            year = int(year_str)
            
            # Parse time (if available)
            if time_str and time_str.lower() != "tba":
                # Handle AM/PM
                time_str = time_str.strip().upper()
                
                if ":" in time_str:
                    time_parts = time_str.replace("AM", "").replace("PM", "").strip().split(':')
                    hour = int(time_parts[0])
                    minute = int(time_parts[1]) if len(time_parts) > 1 else 0
                else:
                    # Handle cases where time might just be "12 PM" without colon
                    hour = int(time_str.replace("AM", "").replace("PM", "").strip())
                    minute = 0
                
                # Adjust for PM
                if "PM" in time_str and hour < 12:
                    hour += 12
                # Adjust for 12 AM
                if "AM" in time_str and hour == 12:
                    hour = 0
                    
                return datetime.datetime(year, month, day, hour, minute)
            else:
                # If no time is provided, use noon as default
                return datetime.datetime(year, month, day, 12, 0)
        else:
            # If date format is unexpected, use a fallback
            logger.warning(f"Unexpected date format: {date_str}")
            return datetime.datetime.now() + datetime.timedelta(days=30)
    except Exception as e:
        logger.error(f"Error parsing date/time: {date_str}, {time_str} - {str(e)}")
        # Return a placeholder date in the future
        return datetime.datetime.now() + datetime.timedelta(days=30)

def scrape_schedule():
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
        
        # Find the schedule table/elements
        # First, try the common class names for schedule items
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
                # Extract date - try multiple possible selectors
                date_elem = (
                    item.select_one('.sidearm-schedule-game-opponent-date, .event-date, [data-field="date"]') or
                    item.find('span', class_=lambda c: c and 'date' in c.lower()) or
                    item.find('div', class_=lambda c: c and 'date' in c.lower())
                )
                date_str = date_elem.text.strip() if date_elem else ""
                
                # Extract time
                time_elem = (
                    item.select_one('.sidearm-schedule-game-time, .event-time, [data-field="time"]') or
                    item.find('span', class_=lambda c: c and 'time' in c.lower()) or
                    item.find('div', class_=lambda c: c and 'time' in c.lower())
                )
                time_str = time_elem.text.strip() if time_elem else "TBA"
                
                # Extract opponent
                opponent_elem = (
                    item.select_one('.sidearm-schedule-game-opponent-name, .event-opponent, [data-field="opponent"]') or
                    item.find('a', class_=lambda c: c and 'opponent' in c.lower()) or
                    item.find('span', class_=lambda c: c and 'team' in c.lower())
                )
                opponent = opponent_elem.text.strip() if opponent_elem else "Unknown Opponent"
                
                # Determine if home or away
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
    
    for game in games:
        event = Event()
        event.name = game['title']
        event.begin = game['start']
        event.end = game['end']
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
    
    # Save to file
    with open(CALENDAR_FILE, 'w') as f:
        f.write(str(cal))
    
    logger.info(f"Calendar created with {len(games)} events")
    return cal

def update_calendar():
    """Update the football calendar"""
    try:
        games = scrape_schedule()
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
                <pre>http://YOUR_SERVER_URL/calendar.ics</pre>
                <p><a href="/calendar.ics">Download Calendar</a></p>
                <p>The calendar updates daily with the latest game information from the Yale Bulldogs website.</p>
            </div>
            <div class="footer">
                <p>Data sourced from yalebulldogs.com. Updated daily.</p>
                <p>This service is not affiliated with Yale University.</p>
            </div>
        </body>
    </html>
    """

@app.route('/debug')
def debug_info():
    """Show debugging information"""
    try:
        games = scrape_schedule()
        return Response(
            '<html><head><title>Debug Info</title></head><body>'
            '<h1>Yale Football Schedule - Debug Info</h1>'
            '<table border="1" cellpadding="5">'
            '<tr><th>Game</th><th>Date</th><th>Time</th><th>Location</th><th>Broadcast</th></tr>'
            + ''.join([
                f'<tr><td>{g["title"]}</td><td>{g["date_str"]}</td><td>{g["time_str"]}</td>'
                f'<td>{g["location"]}</td><td>{g["broadcast"]}</td></tr>'
                for g in games
            ])
            + '</table></body></html>',
            mimetype='text/html'
        )
    except Exception as e:
        return f"Error: {str(e)}", 500

if __name__ == "__main__":
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
