import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.parser import parse_document

def main():
    pdf_path = "/Users/gowtham/Desktop/Thank you.pdf"
    if not os.path.exists(pdf_path):
        print(f"Error: {pdf_path} does not exist.")
        return

    print(f"Parsing {pdf_path}...")
    chunks = parse_document(pdf_path)
    print(f"Extracted {len(chunks)} chunks:")
    for idx, chunk in enumerate(chunks):
        content = chunk.get("content", "")
        has_gowtham = "gowtham" in content.lower()
        has_pentela = "pentela" in content.lower()
        print(f"[{idx+1}] Page {chunk.get('page_number')} | Length: {len(content)} | Contains Gowtham: {has_gowtham} | Contains Pentela: {has_pentela}")
        if has_gowtham or has_pentela:
            print(f"--- MATCH CONTENT ---\n{content}\n--------------------")

if __name__ == "__main__":
    main()
