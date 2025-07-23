from flask import Flask, request, jsonify
from config import GONG_API_BASE, GONG_API_KEY, GONG_API_SECRET
import requests
import base64

app = Flask(__name__)

def get_auth_header():
    creds = f"{GONG_API_KEY}:{GONG_API_SECRET}"
    b64_creds = base64.b64encode(creds.encode()).decode()
    return {"Authorization": f"Bearer {b64_creds}"}

@app.route("/insights", methods=["GET"])
def get_insights():
    topic = request.args.get("topic", "calls")
    call_id = request.args.get("call_id")
    deal_id = request.args.get("deal_id")

    # Handle nested call endpoints like highlights or transcript
    if topic in ["highlights", "transcript"] and call_id:
        endpoint = f"{GONG_API_BASE}/v2/calls/{call_id}/{topic}"

    # Handle deal-specific nested endpoints
    elif topic == "deal_calls" and deal_id:
        endpoint = f"{GONG_API_BASE}/v2/deals/{deal_id}/calls"

    elif topic == "deal" and deal_id:
        endpoint = f"{GONG_API_BASE}/v2/deals/{deal_id}"

    else:
        endpoint = f"{GONG_API_BASE}/v2/{topic}"

    params = request.args.to_dict()
    for param in ["call_id", "deal_id"]:
        params.pop(param, None)

    if "limit" not in params:
        params["limit"] = 10
    if "page" not in params:
        params["page"] = 1

    try:
        response = requests.get(endpoint, headers=get_auth_header(), params=params)
        response.raise_for_status()
        return jsonify(response.json())
    except requests.exceptions.RequestException as e:
        return jsonify({"error": str(e), "status_code": response.status_code}), response.status_code

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
