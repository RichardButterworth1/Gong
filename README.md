# Gong.io API + GPT Integration

## 🚀 Setup

1. Clone the repo and `cd` in.
2. Add your API keys in the Render dashboard.
3. Deploy on Render (https://render.com).
4. The service will expose `/insights?topic=calls` or other Gong endpoints.

## 📬 Sample Usage

```http
GET https://your-service.onrender.com/insights?topic=calls

---

## 🔌 OpenAI Custom GPT Action (OpenAPI Schema)

```json
{
  "schema_version": "v1",
  "name_for_model": "gonginsights",
  "name_for_human": "Gong Insights",
  "description_for_model": "Fetch sales and call insights from Gong.io",
  "description_for_human": "Query Gong.io for recent sales activity, key topics, rep performance, etc.",
  "auth": {
    "type": "none"
  },
  "api": {
    "type": "openapi",
    "url": "https://your-service.onrender.com/openapi.json"
  },
  "logo_url": "https://your-company.com/logo.png",
  "contact_email": "your-email@company.com",
  "legal_info_url": "https://your-company.com/legal"
}