import sys
from pathlib import Path

# Add parent directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the Flask app
# Vercel will auto-detect Flask when 'app' variable exists
from web_app import app

