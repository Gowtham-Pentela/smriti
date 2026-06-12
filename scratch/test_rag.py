import httpx
import json

def main():
    url = "http://localhost:8000/query"
    headers = {
        "Content-Type": "application/json",
        "X-Dev-User-Email": "admin.smritione@gmail.com"
    }
    payload = {
        "query": "explain the architecture used?",
        "top_k": 8
    }
    
    print("Sending query to live backend...")
    r = httpx.post(url, headers=headers, json=payload, timeout=90.0)
    print(f"Status Code: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print("\n=== RESPONSE ===")
        print(data.get("response"))
        print("\n=== CITATIONS ===")
        print(json.dumps(data.get("citations"), indent=2))
        print("\n=== TELEMETRY ===")
        print(f"Latency: {data.get('latency_seconds')}s")
    else:
        print(r.text)

if __name__ == "__main__":
    main()
