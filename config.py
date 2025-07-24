import os

# Base URL for Gong API
GONG_API_BASE_URL = "https://api.gong.io"

# Gong API credentials (from environment or hard-coded for now)
# Ensure these are set as env vars in production for security.
GONG_API_KEY = os.environ.get("GONG_API_KEY", "<YOUR_GONG_ACCESS_KEY>")
GONG_API_SECRET = os.environ.get("GONG_API_SECRET", "<YOUR_GONG_SECRET>")
