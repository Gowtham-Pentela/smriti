import httpx
import os

def main():
    api_url = "http://localhost:8000"
    headers = {
        "X-Dev-User-Email": "admin.smritione@gmail.com"
    }

    # 1. Clear index
    print("Clearing index for admin.smritione@gmail.com...")
    r = httpx.post(f"{api_url}/clear", headers=headers, timeout=30.0)
    print(f"Clear status: {r.status_code}, Response: {r.json()}")

    # 2. Ingest README.md
    print("\nIngesting README.md...")
    readme_path = "README.md"
    if os.path.exists(readme_path):
        with open(readme_path, "rb") as f:
            files = {"file": ("README.md", f, "text/markdown")}
            r = httpx.post(f"{api_url}/ingest", headers=headers, files=files, timeout=60.0)
            print(f"README ingest status: {r.status_code}, Response: {r.json()}")
    else:
        print("README.md not found in current directory.")

    # 3. Ingest yc_application_pitch_script.md
    pitch_path = "yc_application_pitch_script.md"
    print("\nIngesting yc_application_pitch_script.md...")
    if os.path.exists(pitch_path):
        with open(pitch_path, "rb") as f:
            files = {"file": ("yc_application_pitch_script.md", f, "text/markdown")}
            r = httpx.post(f"{api_url}/ingest", headers=headers, files=files, timeout=60.0)
            print(f"Pitch script ingest status: {r.status_code}, Response: {r.json()}")
    else:
        print("yc_application_pitch_script.md not found.")

if __name__ == "__main__":
    main()
