import os
import glob
import hashlib
import json
from datetime import datetime
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any

def get_file_hash(file_path):
    hasher = hashlib.md5()
    try:
        with open(file_path, 'rb') as f:
            buf = f.read()
            hasher.update(buf)
        return hasher.hexdigest()
    except Exception as e:
        print(f"Error hashing file {file_path}: {e}")
        return ""

def write_audit_log(user_email, query, accessed_files):
    # Resolve path relative to backend execution
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    audit_file = os.path.join(base_dir, "data", "audit_log.json")
    
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "user_email": user_email,
        "query": query,
        "accessed_files": list(set(accessed_files))
    }
    
    logs = []
    if os.path.exists(audit_file):
        try:
            with open(audit_file, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except Exception:
            logs = []
            
    logs.append(log_entry)
    
    try:
        with open(audit_file, "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=2)
    except Exception as e:
        print(f"Failed to write audit log: {e}")

from backend.parser import parse_document
from backend.transcription import transcribe_video
from backend.vector_store import LocalVectorStore
from backend.grounding import validate_response, MODEL_NAME

app = FastAPI(title="Local Grounded KT Assistant")

# Enable CORS for the local web UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global State
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
vector_store = LocalVectorStore(index_path=os.path.join(base_dir, "data", "vector_store.json"))
indexing_status = {
    "is_indexing": False,
    "progress": 0,
    "current_file": "",
    "total_files": 0,
    "indexed_files": [],
    "elapsed_time": 0,
    "total_time": 0
}
cancel_indexing_flag = False

class IndexRequest(BaseModel):
    folder_path: str

class QueryRequest(BaseModel):
    query: str

def run_indexing(folder_path: str):
    global indexing_status, cancel_indexing_flag
    indexing_status["is_indexing"] = True
    indexing_status["progress"] = 0
    indexing_status["current_file"] = ""
    indexing_status["indexed_files"] = []
    indexing_status["elapsed_time"] = 0
    indexing_status["total_time"] = 0
    cancel_indexing_flag = False
    
    import time
    start_time = time.time()
    last_save_time = start_time
    files_since_save = 0
    
    try:
        # Define extensions to scan
        doc_exts = {
            ".pdf", ".txt", ".md", ".markdown", ".json",
            ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css",
            ".java", ".go", ".cpp", ".c", ".h", ".rs", ".sh", ".yaml", ".yml", ".sql",
            ".png", ".jpg", ".jpeg"
        }
        video_exts = {".mp4", ".mkv", ".avi", ".mov"}
        
        all_files = []
        for root, dirs, files in os.walk(folder_path):
            # Prune directories we don't want to walk into
            dirs[:] = [d for d in dirs if d not in {'.git', 'node_modules', 'dist', 'build', '.venv', 'venv', 'env', '.gemini', '__pycache__'}]
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in doc_exts or ext in video_exts:
                    all_files.append(os.path.join(root, file))
                    
        indexing_status["total_files"] = len(all_files)
        if not all_files:
            indexing_status["is_indexing"] = False
            return
            
        print(f"Found {len(all_files)} files to index in {folder_path}.")
        
        for idx, file_path in enumerate(all_files):
            if cancel_indexing_flag:
                print("Indexing cancelled by user request. Saving progress...")
                vector_store.save()
                indexing_status["total_time"] = int(time.time() - start_time)
                indexing_status["elapsed_time"] = indexing_status["total_time"]
                break
                
            source_name = os.path.relpath(file_path, folder_path)
            file_hash = get_file_hash(file_path)
            file_size = os.path.getsize(file_path)
            
            # Skip very large non-video files to prevent massive resource usage
            ext = os.path.splitext(file_path)[1].lower()
            if ext not in video_exts and file_size > 1024 * 1024:
                print(f"Skipping large file: {source_name} ({file_size / (1024*1024):.2f} MB)")
                indexing_status["progress"] = int((idx / len(all_files)) * 100)
                indexing_status["elapsed_time"] = int(time.time() - start_time)
                continue
            
            # Incremental Indexing Check: Skip if hash matches our cache
            if not vector_store.has_file_changed(source_name, file_hash):
                indexing_status["progress"] = int((idx / len(all_files)) * 100)
                indexing_status["elapsed_time"] = int(time.time() - start_time)
                continue
                
            # File has changed (or is new): Remove old vector entries
            vector_store.remove_file_chunks(source_name, save_after=False)
            
            indexing_status["current_file"] = source_name
            indexing_status["progress"] = int((idx / len(all_files)) * 100)
            indexing_status["elapsed_time"] = int(time.time() - start_time)
            
            chunks = []
            
            if ext in video_exts:
                # Video file: transcribe
                chunks = transcribe_video(file_path, source_name=source_name)
            else:
                # Document file: parse
                chunks = parse_document(file_path, source_name=source_name)
                
            if chunks:
                vector_store.add_chunks(chunks, save_after=False)
                vector_store.file_hashes[source_name] = file_hash
                indexing_status["indexed_files"].append(source_name)
                
                # Batch saving: save every 50 files or 30 seconds
                files_since_save += 1
                if files_since_save >= 50 or (time.time() - last_save_time) > 30:
                    print("Saving vector store progress to disk...")
                    vector_store.save()
                    last_save_time = time.time()
                    files_since_save = 0
                
        # Final save at the end of the indexing process
        print("Finalizing index and saving to disk...")
        vector_store.save()
        indexing_status["progress"] = 100
        indexing_status["total_time"] = int(time.time() - start_time)
        indexing_status["elapsed_time"] = indexing_status["total_time"]
        print(f"Indexing completed in {indexing_status['total_time']} seconds.")
    except Exception as e:
        print(f"Error indexing folder: {e}")
    finally:
        indexing_status["is_indexing"] = False
        indexing_status["current_file"] = ""

@app.post("/index-folder")
def index_folder(req: IndexRequest, background_tasks: BackgroundTasks):
    if not os.path.exists(req.folder_path):
        raise HTTPException(status_code=404, detail="Provided folder path does not exist.")
    
    if indexing_status["is_indexing"]:
        raise HTTPException(status_code=400, detail="Indexing is already in progress.")
        
    background_tasks.add_task(run_indexing, req.folder_path)
    return {"status": "success", "message": "Indexing started in background."}

@app.get("/indexing-progress")
def get_indexing_progress():
    return indexing_status

@app.post("/cancel-indexing")
def cancel_indexing():
    global cancel_indexing_flag
    if indexing_status["is_indexing"]:
        cancel_indexing_flag = True
        return {"status": "success", "message": "Cancellation request received."}
    return {"status": "ignored", "message": "No active indexing task."}

@app.get("/status")
def get_status():
    return {
        "indexed_chunks_count": len(vector_store.chunks),
        "indexed_files": list(set([c["source"] for c in vector_store.chunks]))
    }

@app.post("/clear")
def clear_index():
    vector_store.clear()
    return {"status": "success", "message": "Index cleared."}

@app.post("/query")
def process_query(req: QueryRequest):
    if not vector_store.chunks:
        raise HTTPException(status_code=400, detail="No files have been indexed yet. Please index a folder first.")
        
    # 1. Retrieve relevant chunks
    retrieved_chunks = vector_store.query(req.query, top_k=8)
    if not retrieved_chunks:
        return {
            "query": req.query,
            "response": "I could not find any relevant information in the indexed folder to answer your question.",
            "citations": [],
            "retrieved_context": []
        }
        
    # 2. Format context for prompt
    context_str = ""
    for idx, chunk in enumerate(retrieved_chunks):
        context_str += f"--- CONTEXT CHUNK {idx+1} (Source: {chunk['source']}, Location: {chunk['location']}) ---\n"
        context_str += f"{chunk['content']}\n\n"
        
    # 3. Build synthesis prompt
    system_instructions = (
        "You are an elite, highly accurate Knowledge Transfer assistant.\n"
        "Your task is to answer the user query based ONLY on the Context provided below.\n"
        "Rules:\n"
        "1. Do not use any external knowledge. If the context does not contain the answer, say 'I cannot find the answer in the provided documents/videos.'\n"
        "2. Only list items or information directly relevant to the user query. For example, if the query asks for specific specialties (e.g. gastroenterology or dermatology), ONLY list doctors who are explicitly labeled with those exact specialties in the context. Do not list doctors from other specialties (like Otolaryngology/ENT or Physical Therapy).\n"
        "3. For every fact, claim, or instruction you write, you MUST cite the source and location using the exact format: `[Citation: SourceName, Location]` immediately after the statement. Multiple citations are allowed if needed.\n"
        "4. Keep your answer clear, accurate, and completely grounded in the context."
    )
    
    full_prompt = (
        f"{system_instructions}\n\n"
        f"CONTEXT:\n\"\"\"\n{context_str}\n\"\"\"\n\n"
        f"USER QUERY: {req.query}\n\n"
        "RESPONSE:"
    )
    
    # 4. Call local Ollama
    import requests
    OLLAMA_GEN_URL = "http://localhost:11434/api/generate"
    payload = {
        "model": MODEL_NAME,
        "prompt": full_prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,  # Low temperature to minimize creative liberties
            "num_ctx": 8192
        }
    }
    
    try:
        response = requests.post(OLLAMA_GEN_URL, json=payload, timeout=90)
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Ollama generation failed: {response.text}")
            
        raw_response = response.json().get("response", "")
        
        # 5. Grounding Verification (Deterministic verification)
        validated_response = validate_response(raw_response, retrieved_chunks)
        
        # Write Query Audit Log
        accessed_files = [c["source"] for c in retrieved_chunks]
        write_audit_log("local_developer@company.com", req.query, accessed_files)
        
        # Extract citations used in the validated response
        from backend.grounding import extract_citations
        citations = extract_citations(validated_response)
        
        # Format context for return (excluding embedding arrays for network efficiency)
        clean_context = [
            {
                "source": c["source"],
                "type": c["type"],
                "location": c["location"],
                "content": c["content"],
                "score": c["score"]
            }
            for c in retrieved_chunks
        ]
        
        return {
            "query": req.query,
            "response": validated_response,
            "citations": citations,
            "retrieved_context": clean_context
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error connecting to Ollama: {str(e)}")
