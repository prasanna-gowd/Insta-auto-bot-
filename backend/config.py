import os
from dotenv import load_dotenv

load_dotenv()

INSTAGRAM_USERNAME = os.getenv('INSTAGRAM_USERNAME', '')
INSTAGRAM_PASSWORD = os.getenv('INSTAGRAM_PASSWORD', '')
DATABASE_PATH = os.getenv('DATABASE_PATH', 'bot.db')
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', '45'))
SESSION_FILE = os.getenv('SESSION_FILE', 'session.json')
SESSION_DATA = os.getenv('SESSION_DATA', '')
SECRET_KEY = os.getenv('SECRET_KEY', 'change-me-in-production')
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'frontend')
