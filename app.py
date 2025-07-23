from flask import Flask, request, jsonify
from config import GONG_API_BASE, GONG_API_KEY, GONG_API_SECRET
import requests
import base64

app = Flask(__name__)

def get_auth_header():
    creds = f"{GONG_API_KEY}:{GONG_API_SECRET}"
    b64_creds = base64.b64encode(creds.encode()).decode()
    return {"Authorization": f"Basic {b64_creds}"}

@app.route("/insights", methods=["GET"])
def get_insights():
    topic = request.args.get("topic", "calls")
    endpoint = f"{GONG_API_BASE}/v2/{topic}"
    response = requests.get(endpoint, headers=get_auth_header())
    
    if response.status_code != 200:
        return jsonify({"error": response.json()}), response.status_code

    return jsonify(response.json())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
