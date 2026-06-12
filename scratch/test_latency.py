import httpx
import time

url = "http://localhost:8000/query"
headers = {
    "X-Dev-User-Email": "gowthampentela2000@gmail.com"
}

query = "explain the architecture used?"
print(f"Sending query: {query} ...")
t0 = time.perf_counter()
try:
    response = httpx.post(url, json={"query": query}, headers=headers, timeout=60.0)
    elapsed = time.perf_counter() - t0
    if response.status_code == 200:
        data = response.json()
        print(f"Success! Client-side elapsed time: {elapsed:.3f}s")
        print(f"Server-side latency: {data.get('latency_seconds')}s")
        print(f"Model used: {data.get('model')}")
        print(f"Citations count: {len(data.get('citations', []))}")
    else:
        print(f"Status: {response.status_code}")
        print(response.text)
except Exception as e:
    print(f"Failed: {e}")
