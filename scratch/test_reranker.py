import asyncio
import asyncpg
import os
import httpx
from dotenv import load_dotenv

# We need optimum.onnxruntime to load the reranker
from optimum.onnxruntime import ORTModelForSequenceClassification
from transformers import AutoTokenizer

load_dotenv()

class ONNXReranker:
    def __init__(self, model_dir: str):
        self.tokenizer = AutoTokenizer.from_pretrained(model_dir)
        self.model = ORTModelForSequenceClassification.from_pretrained(model_dir, provider="CPUExecutionProvider")
        
    def predict(self, pairs):
        import torch
        inputs = self.tokenizer(
            [p[0] for p in pairs],
            [p[1] for p in pairs],
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt"
        )
        with torch.no_grad():
            outputs = self.model(**inputs)
            logits = outputs.logits.squeeze(-1).tolist()
        return logits

async def main():
    db_url = os.getenv("DATABASE_URL")
    conn = await asyncpg.connect(db_url)
    try:
        tenant_id = "cf1e4b66-f36c-5d8e-8725-5dd27025dea9" # admin
        chunks = await conn.fetch("""
            SELECT source_id, document_title, content 
            FROM tenant_redwood_inference_prod.vector_chunks 
            WHERE tenant_id = $1::uuid
        """, uuid_to_uuid := __import__('uuid').UUID(tenant_id))
        
        query = "explain the architecture used?"
        
        # Load reranker
        # Ensure model is downloaded or cached locally
        model_dir = "cross-encoder/ms-marco-MiniLM-L6-v2"
        print("Loading ONNX reranker...")
        reranker = ONNXReranker(model_dir)
        
        pairs = [[query, c["content"]] for c in chunks]
        scores = reranker.predict(pairs)
        
        scored_chunks = []
        for c, score in zip(chunks, scores):
            scored_chunks.append({
                "source": c["source_id"],
                "content": c["content"],
                "score": score
            })
            
        scored_chunks.sort(key=lambda x: x["score"], reverse=True)
        
        print("\nReranked Chunks:")
        for i, sc in enumerate(scored_chunks):
            print(f"[{i+1}] Source: {sc['source']} | Reranker Score: {sc['score']:.4f}")
            print(f"Content: {sc['content'][:200]}...\n")
            
    finally:
        await conn.close()

asyncio.run(main())
