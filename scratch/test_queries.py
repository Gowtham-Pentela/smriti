import httpx
import time

url = "http://localhost:8000/query"
headers = {
    "X-Dev-User-Email": "gowthampentela2000@gmail.com"
}

# Wait for server to be fully warm
time.sleep(3)

queries = [
    "explain the architecture used?",
    "what kind of chuncking and retrieval techniquesa re used"
]

for q in queries:
    print(f"\n========================================\nQuery: {q}")
    payload = {"query": q}
    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=60.0)
        if response.status_code == 200:
            print("Status: 200 OK")
            data = response.json()
            print(f"Response:\n{data.get('response')}")
            print("\nCitations:")
            for cit in data.get("citations", []):
                print(f" - {cit}")
        else:
            print(f"Status: {response.status_code}")
            print(response.text)
    except Exception as e:
        print(f"Request failed: {e}")
