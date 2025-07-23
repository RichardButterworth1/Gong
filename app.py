from flask import Flask, request, jsonify
from config import GONG_API_BASE, GONG_API_KEY, GONG_API_SECRET
import requests, base64, logging, datetime

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)  # Configure logging

# Simple in-memory caches for users and deals to avoid repetitive API calls
_user_cache = None
_deal_cache = None

def get_auth_header():
    """Return Basic Auth header for Gong API using API key/secret."""
    creds = f"{GONG_API_KEY}:{GONG_API_SECRET}"
    token = base64.b64encode(creds.encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

def fetch_all_users():
    """Retrieve all users from Gong and cache them."""
    global _user_cache
    if _user_cache is not None:
        return _user_cache
    _user_cache = []
    page = 1
    per_page = 100
    while True:
        url = f"{GONG_API_BASE}/v2/users"
        params = {"limit": per_page, "page": page}
        resp = requests.get(url, headers=get_auth_header(), params=params)
        if resp.status_code != 200:
            app.logger.error(f"Failed to fetch users: {resp.status_code} - {resp.text}")
            break
        data = resp.json()
        users = data.get("users") or data.get("items") or data  # accommodate different structures
        if not users:
            break
        _user_cache.extend(users)
        # If less than requested or no pagination info, break
        if len(users) < per_page or not data.get("hasMore"):
            break
        page += 1
    app.logger.info(f"Cached {_user_cache and len(_user_cache)} Gong users")
    return _user_cache

def get_user_id_by_name(name):
    """Find a Gong user ID by name (case-insensitive). Returns None if not found or ambiguous."""
    users = fetch_all_users()
    if not name or not users:
        return None
    name_lower = name.lower()
    matches = [u for u in users if name_lower in u.get("name", "").lower()]
    if not matches:
        return None
    # If multiple matches, prefer exact full name match or the first match
    exact_matches = [u for u in matches if u.get("name", "").lower() == name_lower]
    user = (exact_matches or matches)[0]
    return user.get("id")

def fetch_deals_page(page=1, per_page=100):
    """Fetch one page of deals from Gong."""
    url = f"{GONG_API_BASE}/v2/deals"
    params = {"limit": per_page, "page": page}
    resp = requests.get(url, headers=get_auth_header(), params=params)
    if resp.status_code != 200:
        app.logger.error(f"Failed to fetch deals: {resp.status_code} - {resp.text}")
        return None
    return resp.json()

def fetch_all_deals():
    """Retrieve all deals from Gong and cache them."""
    global _deal_cache
    if _deal_cache is not None:
        return _deal_cache
    _deal_cache = []
    page = 1
    per_page = 100
    while True:
        data = fetch_deals_page(page, per_page)
        if not data:
            break
        deals = data.get("deals") or data.get("items") or data
        if not deals:
            break
        _deal_cache.extend(deals)
        if len(deals) < per_page or not data.get("hasMore"):
            break
        page += 1
    app.logger.info(f"Cached {_deal_cache and len(_deal_cache)} Gong deals")
    return _deal_cache

def get_deal_ids_by_name(company_name):
    """Find deal IDs by company name (matches accountName or deal name). Returns list of IDs."""
    deals = fetch_all_deals()
    if not company_name or not deals:
        return []
    name_lower = company_name.lower()
    # Match accountName or name (case-insensitive contains or equals)
    matches = [d for d in deals 
               if (d.get("accountName","").lower() == name_lower) or 
                  (d.get("name","").lower() == name_lower)]
    if not matches:
        # Try partial matches if no exact
        matches = [d for d in deals if name_lower in d.get("accountName","").lower() 
                                     or name_lower in d.get("name","").lower()]
    ids = [d.get("id") for d in matches if d.get("id")]
    return ids

def format_date_param(date_str, end_of_day=False):
    """Convert a date string to ISO date-time format for Gong API (append time if missing)."""
    # If already looks like ISO timestamp, use as is
    if not date_str:
        return None
    try:
        # If it's a date (YYYY-MM-DD), add time component
        if len(date_str) == 10 and date_str[4] == '-' and date_str[7] == '-':
            if end_of_day:
                # set to end of day UTC
                return date_str + "T23:59:59Z"
            else:
                return date_str + "T00:00:00Z"
        # If it's a parseable date-time, we trust it
        datetime.datetime.fromisoformat(date_str.replace("Z",""))
        return date_str
    except Exception:
        app.logger.warning(f"Unrecognized date format: {date_str}")
        return None

@app.route("/insights", methods=["GET"])
def get_insights():
    topic = request.args.get("topic", "calls")  # default to 'calls' list if not specified
    salesperson = request.args.get("salesperson")
    company = request.args.get("company")
    from_date = request.args.get("fromDate")
    to_date = request.args.get("toDate")
    call_id = request.args.get("call_id")
    deal_id = request.args.get("deal_id")

    # Prepare filters
    user_id = None
    deal_ids = []
    if salesperson:
        user_id = get_user_id_by_name(salesperson)
        if not user_id:
            app.logger.info(f"Salesperson '{salesperson}' not found – ignoring salesperson filter")
    if company:
        # If deal_id is explicitly provided, use it directly; otherwise find by name
        if not deal_id:
            deal_ids = get_deal_ids_by_name(company)
            if not deal_ids:
                app.logger.info(f"Company '{company}' not found – ignoring company filter")
        else:
            deal_ids = [deal_id]

    # Date filters formatting
    from_dt = format_date_param(from_date)
    to_dt = format_date_param(to_date, end_of_day=True)

    try:
        if topic in ["transcript", "transcripts"]:
            # Retrieve call transcripts (POST /v2/calls/transcript)
            body = {"filter": {}}
            if from_dt or to_dt:
                # Use date range filter if provided
                body["filter"]["fromDateTime"] = from_dt or "1970-01-01T00:00:00Z"
                body["filter"]["toDateTime"] = to_dt or datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            if call_id:
                # If a specific call_id is given, filter by it
                body["filter"]["callIds"] = [call_id]
            elif deal_ids:
                # If company filter exists, get all calls for those deals, then filter transcripts by those call IDs
                call_ids = []
                for d_id in deal_ids:
                    resp = requests.get(f"{GONG_API_BASE}/v2/deals/{d_id}/calls", headers=get_auth_header())
                    if resp.status_code == 200:
                        calls_data = resp.json()
                        calls_list = calls_data.get("calls") or calls_data.get("items") or calls_data
                        for c in calls_list:
                            call_ids.append(c.get("id"))
                if call_ids:
                    body["filter"]["callIds"] = call_ids
            if user_id:
                # If user filter, fetch calls by user (in date range) and filter by those IDs
                # (Gong transcripts API does not directly filter by user, so we do it via call list)
                calls_url = f"{GONG_API_BASE}/v2/calls"
                params = {}
                if from_dt: params["fromDateTime"] = from_dt
                if to_dt: params["toDateTime"] = to_dt
                params["limit"] = 50
                params["page"] = 1
                resp = requests.get(calls_url, headers=get_auth_header(), params=params)
                if resp.status_code == 200:
                    calls_data = resp.json()
                    calls_list = calls_data.get("calls") or calls_data.get("items") or calls_data
                    user_call_ids = [c.get("id") for c in calls_list 
                                     if c.get("primaryUserId")==user_id or c.get("userId")==user_id]
                    if user_call_ids:
                        body["filter"].setdefault("callIds", []).extend(user_call_ids)
            # If no filters at all provided (dangerous to pull all transcripts), default to last 7 days
            if not body["filter"]:
                to_dt_def = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                from_dt_def = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).strftime("%Y-%m-%dT%00:00:00Z")
                body["filter"]["fromDateTime"] = from_dt_def
                body["filter"]["toDateTime"] = to_dt_def
                app.logger.info("No filters provided for transcripts; defaulting to last 7 days")
            # Make the POST request to Gong transcripts API
            endpoint = f"{GONG_API_BASE}/v2/calls/transcript"
            response = requests.post(endpoint, headers=get_auth_header(), json=body)
            response.raise_for_status()
            data = response.json()
            # Optionally remove any superfluous fields (keeping callTranscripts)
            result = {"callTranscripts": data.get("callTranscripts", data)}
            return jsonify(result)

        elif topic in ["highlights", "summary", "extensive"]:
            # Retrieve AI content (summary/highlights) via extensive call API (POST /v2/calls/extensive)
            # Build filter for calls of interest
            body = {"filter": {}, "contentSelector": {"include": ["CONTENT"]}}
            if from_dt or to_dt:
                body["filter"]["fromDateTime"] = from_dt or "1970-01-01T00:00:00Z"
                body["filter"]["toDateTime"] = to_dt or datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            # If specific call_id provided, filter by it
            if call_id:
                body["filter"]["callIds"] = [call_id]
            # If company filter (deal_ids), gather calls from those deals
            all_call_ids = []
            if deal_ids:
                for d_id in deal_ids:
                    resp = requests.get(f"{GONG_API_BASE}/v2/deals/{d_id}/calls", headers=get_auth_header())
                    if resp.status_code == 200:
                        calls_data = resp.json()
                        calls_list = calls_data.get("calls") or calls_data.get("items") or calls_data
                        for c in calls_list:
                            # If user filter also given, only include calls by that user
                            if not user_id or (c.get("primaryUserId")==user_id or c.get("userId")==user_id):
                                all_call_ids.append(c.get("id"))
            if all_call_ids:
                body["filter"]["callIds"] = all_call_ids
            # If user filter given (and no specific call_ids from above), use it
            if user_id and not body["filter"].get("callIds"):
                body["filter"]["primaryUserIds"] = [user_id]
            # If no specific filters at all and no call_id, default to the most recent call
            if not body["filter"]:
                body["filter"]["limit"] = 1  # get the latest call
                app.logger.info("No filters provided; defaulting to the latest call for summary/highlights")
            endpoint = f"{GONG_API_BASE}/v2/calls/extensive"
            response = requests.post(endpoint, headers=get_auth_header(), json=body)
            response.raise_for_status()
            data = response.json()
            calls = data.get("calls") or data.get("items") or data
            # Prepare a cleaned response focusing on key insights
            result_calls = []
            for c in calls:
                call_info = {
                    "id": c.get("id"),
                    "startTime": c.get("startTime"),
                    "topic": c.get("title") or c.get("description"),
                    "salesperson": c.get("primaryUser", {}).get("name") if c.get("primaryUser") else None,
                    "company": c.get("deal", {}).get("accountName") if c.get("deal") else None
                }
                # Extract AI content if present
                content = c.get("content", {})
                if content:
                    # Summary brief and outline
                    if "brief" in content:
                        call_info["summary"] = content["brief"]
                    if "outline" in content:
                        call_info["outline"] = content["outline"]
                    # Highlights (next steps, etc.)
                    if "highlights" in content:
                        highlights = content["highlights"]
                        if "nextSteps" in highlights:
                            call_info["nextSteps"] = highlights["nextSteps"]
                        # (Other highlight fields could be added here if needed)
                result_calls.append(call_info)
            return jsonify({"calls": result_calls})

        elif topic == "deal" and (deal_id or deal_ids):
            # If specific deal ID provided or found, get that deal’s details
            target_deal = deal_id or (deal_ids[0] if deal_ids else None)
            endpoint = f"{GONG_API_BASE}/v2/deals/{target_deal}"
            response = requests.get(endpoint, headers=get_auth_header())
            response.raise_for_status()
            return jsonify(response.json())

        elif topic in ["deal_calls"] and (deal_id or deal_ids):
            # List calls for a given deal ID
            target_deal = deal_id or (deal_ids[0] if deal_ids else None)
            endpoint = f"{GONG_API_BASE}/v2/deals/{target_deal}/calls"
            params = {"limit": 10, "page": 1}
            if from_dt: params["fromDateTime"] = from_dt
            if to_dt: params["toDateTime"] = to_dt
            response = requests.get(endpoint, headers=get_auth_header(), params=params)
            response.raise_for_status()
            return jsonify(response.json())

        elif topic == "deals":
            # List deals, optionally filtered by salesperson or company
            deals_data = fetch_all_deals()
            filtered = []
            for d in deals_data:
                if user_id:
                    owner_id = d.get("owner", {}).get("id") or d.get("userId")
                    if owner_id != user_id:
                        continue
                if company and company.lower() not in (d.get("accountName","").lower() + d.get("name","").lower()):
                    continue
                filtered.append(d)
            # Limit to first 10 results by default
            result_deals = filtered[:10] if len(filtered) > 10 else filtered
            return jsonify({"deals": result_deals})

        else:
            # Default: topic "calls" (or unknown topic) -> list recent calls, possibly filtered
            url = f"{GONG_API_BASE}/v2/calls"
            params = {"limit": 10, "page": 1}
            if from_dt: params["fromDateTime"] = from_dt
            if to_dt: params["toDateTime"] = to_dt
            response = requests.get(url, headers=get_auth_header(), params=params)
            response.raise_for_status()
            calls_data = response.json()
            calls_list = calls_data.get("calls") or calls_data.get("items") or calls_data
            # Filter calls by user or company if provided
            result_calls = []
            for c in calls_list:
                if user_id and c.get("primaryUserId") != user_id and c.get("userId") != user_id:
                    continue
                if deal_ids and c.get("dealId") not in deal_ids:
                    continue
                result_calls.append(c)
            return jsonify({"calls": result_calls})
    except requests.exceptions.RequestException as e:
        # Log the error and return JSON error message
        app.logger.error(f"Error handling /insights request: {e}")
        status = getattr(e.response, "status_code", 500) if hasattr(e, 'response') else 500
        return jsonify({"error": str(e), "status_code": status}), status

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
