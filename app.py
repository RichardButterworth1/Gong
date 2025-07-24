from flask import Flask, request, jsonify, abort
from config import GONG_API_BASE_URL, GONG_API_KEY, GONG_API_SECRET
import requests, base64, logging, datetime

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)  # Configure logging for info and errors

# Initialize a session for reusing TCP connection and setting auth
session = requests.Session()
# Use HTTP Basic Auth for all requests with the provided Gong API credentials
session.auth = (GONG_API_KEY, GONG_API_SECRET)

# Simple in-memory caches to avoid redundant API calls
_user_cache = None
_deal_cache = None

def fetch_all_users():
    """Retrieve all users from Gong (with pagination) and cache them."""
    global _user_cache
    if _user_cache is not None:
        return _user_cache
    users = []
    url = f"{GONG_API_BASE_URL}/v2/users"
    params = {"limit": 100}  # try to get up to 100 per page (max allowed)
    while True:
        resp = session.get(url, params=params)
        if resp.status_code != 200:
            app.logger.error(f"Failed to fetch users: {resp.status_code} - {resp.text}")
            break
        data = resp.json()
        # Gong may return users list under different keys or directly as list
        page_users = data.get("users") or data.get("items") or (data if isinstance(data, list) else [])
        if not page_users:
            break
        users.extend(page_users)
        # Check if there's more data (could be via 'cursor' token or hasMore flag)
        cursor = data.get("cursor")
        has_more = data.get("hasMore")
        if cursor:
            params = {"cursor": cursor}       # use cursor for next page if provided
        elif has_more or (has_more is None and len(page_users) == 100):
            # If hasMore is true (or unknown but we got a full page), attempt next page number
            params["page"] = params.get("page", 1) + 1
        else:
            break
    _user_cache = users
    app.logger.info(f"Cached {len(users)} Gong users.")
    return users

def get_user_id_by_name(name):
    """Find a Gong user ID by (case-insensitive) name."""
    if not name:
        return None
    users = fetch_all_users()
    name = name.lower()
    matches = [u for u in users if u.get("name","").lower() == name or name in u.get("name","").lower()]
    if not matches:
        return None
    # If multiple matches, prefer exact or first match
    return matches[0].get("id")

def fetch_all_deals():
    """Retrieve all deals from Gong (with pagination) and cache them."""
    global _deal_cache
    if _deal_cache is not None:
        return _deal_cache
    deals = []
    url = f"{GONG_API_BASE_URL}/v2/deals"
    params = {"limit": 100}
    while True:
        resp = session.get(url, params=params)
        if resp.status_code != 200:
            app.logger.error(f"Failed to fetch deals: {resp.status_code} - {resp.text}")
            break
        data = resp.json()
        page_deals = data.get("deals") or data.get("items") or (data if isinstance(data, list) else [])
        if not page_deals:
            break
        deals.extend(page_deals)
        cursor = data.get("cursor")
        has_more = data.get("hasMore")
        if cursor:
            params = {"cursor": cursor}
        elif has_more or (has_more is None and len(page_deals) == 100):
            params["page"] = params.get("page", 1) + 1
        else:
            break
    _deal_cache = deals
    app.logger.info(f"Cached {len(deals)} Gong deals.")
    return deals

def get_deal_ids_by_name(company_name):
    """Find Gong deal IDs by company (account) name or deal name (case-insensitive)."""
    if not company_name:
        return []
    deals = fetch_all_deals()
    name = company_name.lower()
    # First look for exact matches on accountName or deal name
    matches = [d for d in deals 
               if d.get("accountName","").lower() == name or d.get("name","").lower() == name]
    if not matches:
        # Fallback: partial match
        matches = [d for d in deals 
                   if name in d.get("accountName","").lower() or name in d.get("name","").lower()]
    return [d.get("id") for d in matches if d.get("id")]

def format_datetime(dt_str, end_of_day=False):
    """Format a date or datetime string to ISO 8601 as required by Gong API."""
    if not dt_str:
        return None
    try:
        # If input is just a date (YYYY-MM-DD), append time (start or end of day)
        if len(dt_str) == 10 and dt_str[4] == '-' and dt_str[7] == '-':
            return dt_str + ("T23:59:59Z" if end_of_day else "T00:00:00Z")
        # If it's already a full datetime string, pass through (assuming ISO format)
        datetime.datetime.fromisoformat(dt_str.replace("Z", ""))  # validate format
        return dt_str
    except Exception:
        app.logger.warning(f"Unrecognized date format: {dt_str}")
        return None

@app.route("/users", methods=["GET"])
def list_users():
    """List all Gong users."""
    try:
        users = fetch_all_users()
        # Return only relevant fields for brevity
        result = [{"id": u.get("id"), "name": u.get("name"), "email": u.get("email")} for u in users]
        return jsonify({"users": result})
    except requests.RequestException as e:
        return _handle_request_exception(e)

@app.route("/calls", methods=["GET"])
def list_calls():
    """List calls with optional filters by rep (salesperson), deal (company), and date range."""
    salesperson = request.args.get("repName") or request.args.get("salesperson")
    rep_id = request.args.get("repId") or request.args.get("userId")
    company = request.args.get("dealName") or request.args.get("company")
    deal_id = request.args.get("dealId")
    from_date = request.args.get("from")
    to_date = request.args.get("to")
    try:
        # Resolve rep_id from name if provided
        if rep_id is None and salesperson:
            rep_id = get_user_id_by_name(salesperson)
            if rep_id is None:
                app.logger.info(f"Salesperson '{salesperson}' not found.")
        # Resolve deal IDs from company name if provided
        deal_ids = []
        if company:
            deal_ids = get_deal_ids_by_name(company) if not deal_id else [deal_id]
            if not deal_ids:
                app.logger.info(f"Company '{company}' not found in deals.")
        elif deal_id:
            deal_ids = [deal_id]
        # Format date filters
        from_dt = format_datetime(from_date)
        to_dt = format_datetime(to_date, end_of_day=True)
        calls = []
        if deal_ids:
            # Fetch calls for each specified deal
            for d_id in deal_ids:
                url = f"{GONG_API_BASE_URL}/v2/deals/{d_id}/calls"
                params = {"limit": 100}
                if from_dt: params["fromDateTime"] = from_dt
                if to_dt: params["toDateTime"] = to_dt
                # Loop through pages of calls for this deal
                while True:
                    resp = session.get(url, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                    page_calls = data.get("calls") or data.get("items") or (data if isinstance(data, list) else [])
                    if not page_calls:
                        break
                    calls.extend(page_calls)
                    cursor = data.get("cursor")
                    has_more = data.get("hasMore")
                    if cursor:
                        params = {"cursor": cursor}
                    elif has_more or (has_more is None and len(page_calls) == 100):
                        params["page"] = params.get("page", 1) + 1
                    else:
                        break
        else:
            # If no deal filter, fetch recent calls (with date range if provided)
            url = f"{GONG_API_BASE_URL}/v2/calls"
            params = {}
            if from_dt: params["fromDateTime"] = from_dt
            if to_dt: params["toDateTime"] = to_dt
            # If no date filters given, limit to a small number of recent calls by default
            if not from_dt and not to_dt:
                params["limit"] = 10
            # Page through results
            while True:
                resp = session.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                page_calls = data.get("calls") or data.get("items") or (data if isinstance(data, list) else [])
                if not page_calls:
                    break
                calls.extend(page_calls)
                cursor = data.get("cursor")
                has_more = data.get("hasMore")
                if cursor:
                    params = {"cursor": cursor}
                elif has_more or (has_more is None and len(page_calls) == (params.get("limit", 100))):
                    # If limit was set, use it; otherwise 100 is default page size
                    params["page"] = params.get("page", 1) + 1
                else:
                    break
        # Apply rep filter on the collected calls (if rep specified)
        if rep_id:
            calls = [c for c in calls 
                     if c.get("primaryUserId") == rep_id or c.get("userId") == rep_id]
        # If multiple deals were matched by name, you may have duplicate call entries – dedupe by ID
        unique_calls = {}
        for c in calls:
            unique_calls[c.get("id")] = c
        # Return the list of calls (basic info only for brevity)
        result = []
        for c in unique_calls.values():
            result.append({
                "id": c.get("id"),
                "startTime": c.get("startTime"),
                "title": c.get("title") or c.get("description"),
                "primaryUserId": c.get("primaryUserId") or c.get("userId"),
                "dealId": c.get("dealId") or (c.get("deal", {}).get("id") if c.get("deal") else None)
            })
        # Sort results by startTime descending (most recent first) for convenience
        result.sort(key=lambda x: x.get("startTime", ""), reverse=True)
        return jsonify({"calls": result})
    except requests.RequestException as e:
        return _handle_request_exception(e)

@app.route("/calls/detailed", methods=["GET"])
def get_call_details():
    """Retrieve detailed call data (AI content like summary, highlights) for calls."""
    salesperson = request.args.get("repName") or request.args.get("salesperson")
    rep_id = request.args.get("repId") or request.args.get("userId")
    company = request.args.get("dealName") or request.args.get("company")
    deal_id = request.args.get("dealId")
    call_id = request.args.get("callId") or request.args.get("call_id")
    from_date = request.args.get("from")
    to_date = request.args.get("to")
    try:
        # Resolve filters similar to above
        if rep_id is None and salesperson:
            rep_id = get_user_id_by_name(salesperson)
        deal_ids = []
        if company:
            deal_ids = get_deal_ids_by_name(company) if not deal_id else [deal_id]
        elif deal_id:
            deal_ids = [deal_id]
        from_dt = format_datetime(from_date)
        to_dt = format_datetime(to_date, end_of_day=True)
        # Build request body for extensive API
        body = {"filter": {}, "contentSelector": {"include": ["CONTENT"]}}
        if from_dt or to_dt:
            body["filter"]["fromDateTime"] = from_dt or "1970-01-01T00:00:00Z"
            body["filter"]["toDateTime"] = to_dt or datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        if call_id:
            # If a specific call is requested, filter by that call ID
            body["filter"]["callIds"] = [call_id]
        elif deal_ids:
            # If filtering by company/deal, get all call IDs for those deals (optionally also by rep)
            all_call_ids = []
            for d_id in deal_ids:
                resp = session.get(f"{GONG_API_BASE_URL}/v2/deals/{d_id}/calls")
                resp.raise_for_status()
                calls_data = resp.json()
                calls_list = calls_data.get("calls") or calls_data.get("items") or calls_data
                for c in (calls_list or []):
                    # If rep filter also given, include only calls where this user is the primary user
                    if not rep_id or c.get("primaryUserId") == rep_id or c.get("userId") == rep_id:
                        all_call_ids.append(c.get("id"))
            if all_call_ids:
                body["filter"]["callIds"] = list(set(all_call_ids))  # deduplicate
        if rep_id and "callIds" not in body["filter"]:
            # Filter by rep (primary user) at the API level if we haven't narrowed by callIds
            body["filter"]["primaryUserIds"] = [rep_id]
        if not body["filter"]:
            # If no filters at all were provided, default to the latest call
            body["filter"]["limit"] = 1
            app.logger.info("No filters provided for detailed call data; defaulting to latest call.")
        # Call the extensive API to get detailed call info
        endpoint = f"{GONG_API_BASE_URL}/v2/calls/extensive"
        resp = session.post(endpoint, json=body)
        resp.raise_for_status()
        data = resp.json()
        calls_data = data.get("calls") or data.get("items") or data
        # Construct a clean response focusing on key insights
        result_calls = []
        for c in calls_data:
            info = {
                "id": c.get("id"),
                "startTime": c.get("startTime"),
                "topic": c.get("title") or c.get("description"),
                "salesperson": c.get("primaryUser", {}).get("name") if c.get("primaryUser") else None,
                "company": c.get("deal", {}).get("accountName") if c.get("deal") else None
            }
            content = c.get("content", {})
            if content:
                # Include AI summary and outline if present
                if "brief" in content:
                    info["summary"] = content["brief"]
                if "outline" in content:
                    info["outline"] = content["outline"]
                # Include Next Steps (and potentially other highlights)
                if "highlights" in content:
                    highlights = content["highlights"]
                    if "nextSteps" in highlights:
                        info["nextSteps"] = highlights["nextSteps"]
                    # (Other highlight categories can be added as needed)
            result_calls.append(info)
        return jsonify({"calls": result_calls})
    except requests.RequestException as e:
        return _handle_request_exception(e)

@app.route("/calls/transcripts", methods=["GET"])
def get_transcripts():
    """Retrieve call transcript(s) for given filters or date range."""
    salesperson = request.args.get("repName") or request.args.get("salesperson")
    rep_id = request.args.get("repId") or request.args.get("userId")
    company = request.args.get("dealName") or request.args.get("company")
    deal_id = request.args.get("dealId")
    call_id = request.args.get("callId") or request.args.get("call_id")
    from_date = request.args.get("from")
    to_date = request.args.get("to")
    try:
        # Resolve rep and deal similar to above
        if rep_id is None and salesperson:
            rep_id = get_user_id_by_name(salesperson)
        deal_ids = []
        if company:
            deal_ids = get_deal_ids_by_name(company) if not deal_id else [deal_id]
        elif deal_id:
            deal_ids = [deal_id]
        from_dt = format_datetime(from_date)
        to_dt = format_datetime(to_date, end_of_day=True)
        # Build filter for transcript request
        filter_obj = {}
        if from_dt or to_dt:
            filter_obj["fromDateTime"] = from_dt or "1970-01-01T00:00:00Z"
            filter_obj["toDateTime"] = to_dt or datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        if call_id:
            filter_obj["callIds"] = [call_id]
        elif deal_ids:
            # If filtering by deal, fetch all call IDs for those deals within date range (if any)
            call_ids = []
            for d_id in deal_ids:
                url = f"{GONG_API_BASE_URL}/v2/deals/{d_id}/calls"
                params = {}
                if from_dt: params["fromDateTime"] = from_dt
                if to_dt: params["toDateTime"] = to_dt
                resp = session.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                calls_list = data.get("calls") or data.get("items") or data
                for c in (calls_list or []):
                    call_ids.append(c.get("id"))
            if call_ids:
                filter_obj["callIds"] = list(set(call_ids))
        if rep_id:
            if "callIds" in filter_obj:
                # If we already have specific call IDs (from deal filter), narrow them by rep
                filter_obj["callIds"] = [cid for cid in filter_obj["callIds"] 
                                         if _call_belongs_to_user(cid, rep_id)]
            else:
                # If no callIds specified, we cannot directly filter transcripts by user via API,
                # so we will handle it by retrieving all transcripts in range and then filtering below.
                pass  # We'll handle rep filter after fetching transcripts.
        # Default to last 7 days if no filter provided (to avoid pulling all transcripts)
        if not filter_obj:
            to_dt_def = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            from_dt_def = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
            filter_obj = {"fromDateTime": from_dt_def, "toDateTime": to_dt_def}
            app.logger.info("No filters provided for transcripts; defaulting to last 7 days.")
        # Call the transcripts API
        endpoint = f"{GONG_API_BASE_URL}/v2/calls/transcript"
        resp = session.post(endpoint, json={"filter": filter_obj})
        resp.raise_for_status()
        data = resp.json()
        transcripts = data.get("callTranscripts") or data.get("items") or data
        # If we still need to apply a rep filter (and we didn't filter by callIds already)
        if rep_id and "callIds" not in filter_obj:
            transcripts = [t for t in transcripts if _transcript_belongs_to_user(t, rep_id)]
        return jsonify({"callTranscripts": transcripts})
    except requests.RequestException as e:
        return _handle_request_exception(e)

def _call_belongs_to_user(call_id, user_id):
    """Helper to check if a given call's primary user (owner) matches the user_id."""
    try:
        resp = session.get(f"{GONG_API_BASE_URL}/v2/calls/{call_id}")
        if resp.status_code == 200:
            call = resp.json()
            puid = call.get("primaryUserId") or call.get("userId") or (call.get("primaryUser", {}) or {}).get("id")
            return puid == user_id
    except requests.RequestException:
        pass
    return False

def _transcript_belongs_to_user(transcript_record, user_id):
    """Helper to check if a transcript record corresponds to a call owned by user_id."""
    # Transcript records typically include callId and speaker info. We check call ownership via call metadata.
    call_id = transcript_record.get("callId") or transcript_record.get("id")
    return _call_belongs_to_user(call_id, user_id)

def _handle_request_exception(e):
    """Generic error handler for requests exceptions to return JSON error."""
    status = getattr(e.response, "status_code", 500) if hasattr(e, "response") else 500
    error_body = None
    try:
        error_body = e.response.json() if hasattr(e, "response") else None
    except Exception:
        error_body = e.response.text if hasattr(e, "response") else None
    app.logger.error(f"[Gong API Error] {status} - {str(e)} - Details: {error_body}")
    return jsonify({"error": str(e), "status_code": status, "details": error_body}), status

if __name__ == "__main__":
    # Run the app (for local testing; in production, a WSGI server like gunicorn would be used)
    app.run(host="0.0.0.0", port=5000)
