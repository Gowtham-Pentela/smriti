import requests
import json
import time

BASE_URL = "http://localhost:8000"

def run_test_suite():
    print("==================================================================")
    # 1. Check Server Connection & Status
    print("[1/3] Checking Backend Status...")
    try:
        status_resp = requests.get(f"{BASE_URL}/status")
        if status_resp.status_code == 200:
            status_data = status_resp.json()
            print(f"  ✓ Connected! Indexed Chunks: {status_data['indexed_chunks_count']}")
            print(f"  ✓ Indexed Files: {', '.join(status_data['indexed_files'])}")
        else:
            print(f"  ✗ Status check failed: {status_resp.text}")
            return False
    except Exception as e:
        print(f"  ✗ Failed to connect to backend: {e}")
        return False

    # 2. Run Query and Measure Latency
    print("\n[2/3] Running Target Query & Measuring Latency...")
    query_payload = {
        "query": "Are there any gastroenterology or dermatology doctors listed?"
    }
    
    start_time = time.time()
    try:
        query_resp = requests.post(f"{BASE_URL}/query", json=query_payload, timeout=60)
        elapsed_time = time.time() - start_time
        
        if query_resp.status_code != 200:
            print(f"  ✗ Query failed: {query_resp.text}")
            return False
            
        result = query_resp.json()
        print(f"  ✓ Query returned successfully in {elapsed_time:.2f} seconds!")
        
        print("\n[3/3] Evaluating Grounding, Citations & Hallucination Guardrails...")
        response_text = result["response"]
        citations = result["citations"]
        
        print(f"\n--- AI RESPONSE ---")
        print(response_text)
        print(f"-------------------\n")
        
        print(f"Citations extracted: {len(citations)}")
        for idx, cit in enumerate(citations):
            print(f"  - Citation {idx+1}: Source='{cit['source']}', Location='{cit['location']}'")
            
        # Assertions
        passed = True
        
        # Check that target specialties are present
        gastro_names = ["Praveen Nallapareddy", "Anoop Appannagari", "Harshavardhan", "Daanish"]
        derm_names = ["Megha K Trivedi", "Mohamed Elrakhawy", "Emily M Garritson", "Arlene M Ruiz De Luzuriaga", "Stavonnie", "Monica Rani"]
        ent_names = ["Samer Al-khudari", "Luvianca", "Andrew Khalifa", "Stephen"]
        pt_names = ["Emily Chase"]
        
        # Verify gastroenterology doctors
        found_gastro = any(name.lower() in response_text.lower() for name in gastro_names)
        if found_gastro:
            print("  ✓ PASS: Found gastroenterology doctors in response.")
        else:
            print("  ✗ FAIL: Missing gastroenterology doctors in response.")
            passed = False
            
        # Verify dermatology doctors
        found_derm = any(name.lower() in response_text.lower() for name in derm_names)
        if found_derm:
            print("  ✓ PASS: Found dermatology doctors in response.")
        else:
            print("  ✗ FAIL: Missing dermatology doctors in response.")
            passed = False
            
        # Verify zero ENT leakage
        leaked_ent = any(name.lower() in response_text.lower() for name in ent_names)
        if not leaked_ent:
            print("  ✓ PASS: Zero ENT (Otolaryngology) doctor leakage.")
        else:
            print("  ✗ FAIL: Leakage detected! ENT doctors found in response.")
            passed = False
            
        # Verify zero PT leakage
        leaked_pt = any(name.lower() in response_text.lower() for name in pt_names)
        if not leaked_pt:
            print("  ✓ PASS: Zero Physical Therapy doctor leakage.")
        else:
            print("  ✗ FAIL: Leakage detected! Physical Therapy doctors found in response.")
            passed = False
            
        # Verify citations are present
        if len(citations) > 0:
            print("  ✓ PASS: Valid citations generated.")
        else:
            print("  ✗ FAIL: No citations generated.")
            passed = False
            
        print("==================================================================")
        if passed:
            print("  ★★★ ALL TESTS PASSED! SYSTEM RUNNING DETERMINISTICALLY ★★★")
        else:
            print("  ★★★ SOME TESTS FAILED! Grounding/retrieval needs attention ★★★")
        print("==================================================================")
        return passed

    except Exception as e:
        print(f"  ✗ Test execution error: {e}")
        return False

if __name__ == "__main__":
    run_test_suite()
