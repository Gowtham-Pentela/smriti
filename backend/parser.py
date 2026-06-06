import os
import base64
import requests
import re
from pypdf import PdfReader

def sanitize_secrets_and_pii(text):
    # AWS Access Key / Secret
    text = re.sub(r'(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}', '[REDACTED_AWS_KEY]', text)
    # Generic API Keys / Secrets / Passwords
    text = re.sub(r'(?i)(?:key|secret|token|password|auth)\s*[:=]\s*[\'"][a-zA-Z0-9_\-]{16,}[\'"]', lambda m: m.group(0).split(':')[0].split('=')[0] + ': "[REDACTED_SECRET]"', text)
    # Email addresses
    text = re.sub(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', '[REDACTED_EMAIL]', text)
    return text

def chunk_code(text, filename):
    lines = text.splitlines()
    chunks = []
    current_chunk = []
    line_count = 0
    chunk_idx = 1
    
    # Identify code block starting points (e.g. def, class, function, struct, interface)
    block_start_pattern = re.compile(r'^\s*(class\s+|def\s+|async\s+def\s+|function\s+|func\s+|struct\s+|interface\s+)')
    
    for line in lines:
        if block_start_pattern.match(line) and line_count >= 30:
            chunk_content = "\n".join(current_chunk)
            if chunk_content.strip():
                chunks.append({
                    "source": filename,
                    "type": "code",
                    "location": f"Block {chunk_idx}",
                    "content": sanitize_secrets_and_pii(chunk_content)
                })
                chunk_idx += 1
            current_chunk = []
            line_count = 0
            
        current_chunk.append(line)
        line_count += 1
        
        if line_count >= 80:
            chunk_content = "\n".join(current_chunk)
            if chunk_content.strip():
                chunks.append({
                    "source": filename,
                    "type": "code",
                    "location": f"Block {chunk_idx}",
                    "content": sanitize_secrets_and_pii(chunk_content)
                })
                chunk_idx += 1
            current_chunk = []
            line_count = 0
            
    if current_chunk:
        chunk_content = "\n".join(current_chunk)
        if chunk_content.strip():
            chunks.append({
                "source": filename,
                "type": "code",
                "location": f"Block {chunk_idx}",
                "content": sanitize_secrets_and_pii(chunk_content)
            })
            
    return chunks

def chunk_text(text, chunk_size=2500, overlap=400):
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks

def parse_pdf(file_path, source_name=None):
    chunks = []
    try:
        reader = PdfReader(file_path)
        filename = source_name or os.path.basename(file_path)
        for page_num, page in enumerate(reader.pages):
            text = page.extract_text()
            if not text:
                continue
            # Chunk the page text
            page_chunks = chunk_text(text)
            for chunk_idx, chunk_text_content in enumerate(page_chunks):
                chunks.append({
                    "source": filename,
                    "type": "document",
                    "location": f"Page {page_num + 1}",
                    "content": sanitize_secrets_and_pii(chunk_text_content)
                })
    except Exception as e:
        print(f"Error parsing PDF {file_path}: {e}")
    return chunks

def parse_text_file(file_path, source_name=None):
    chunks = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        filename = source_name or os.path.basename(file_path)
        text_chunks = chunk_text(text)
        for idx, chunk_text_content in enumerate(text_chunks):
            chunks.append({
                "source": filename,
                "type": "document",
                "location": f"Section {idx + 1}",
                "content": sanitize_secrets_and_pii(chunk_text_content)
            })
    except Exception as e:
        print(f"Error parsing text file {file_path}: {e}")
    return chunks

def parse_image_via_moondream(file_path, source_name=None):
    try:
        with open(file_path, "rb") as image_file:
            img_b64 = base64.b64encode(image_file.read()).decode('utf-8')
            
        payload = {
            "model": "moondream",
            "prompt": "Describe this image in detail, listing any visible text, code blocks, UI elements, flowcharts, or system components.",
            "images": [img_b64],
            "stream": False
        }
        response = requests.post("http://localhost:11434/api/generate", json=payload, timeout=60)
        filename = source_name or os.path.basename(file_path)
        if response.status_code == 200:
            description = response.json().get("response", "").strip()
            return [{
                "source": filename,
                "type": "image",
                "location": "Image Analysis",
                "content": f"Visual Content Description of {filename}: {description}"
            }]
        else:
            print(f"Ollama vision error: {response.text}")
            return []
    except Exception as e:
        print(f"Error describing image {file_path}: {e}")
        return []

def parse_document(file_path, source_name=None):
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return parse_pdf(file_path, source_name)
    elif ext in [".png", ".jpg", ".jpeg"]:
        return parse_image_via_moondream(file_path, source_name)
    elif ext in [".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".cpp", ".c", ".h", ".rs", ".sh"]:
        # Code files get chunked using code-aware function block segmenter
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            filename = source_name or os.path.basename(file_path)
            return chunk_code(text, filename)
        except Exception as e:
            print(f"Error parsing code file {file_path}: {e}")
            return []
    elif ext in [".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".sql"]:
        return parse_text_file(file_path, source_name)
    return []
