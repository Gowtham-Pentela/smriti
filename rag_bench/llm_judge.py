import os
import json
import asyncio
import httpx
import argparse
import random
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
QUESTIONS_FILE = "/Users/gowtham/EnterpriseRAG-Bench/questions.jsonl"
QUERY_URL = "http://localhost:8000/query"

JUDGE_PROMPT = """
You are an expert evaluator. Given the Question, the Gold Answer (expected), and the Generated Answer, evaluate the quality of the Generated Answer.
Score it out of 5 based on:
1: Completely wrong, hallucinated, or irrelevant
2: Mostly wrong, but some correct terms
3: Partially correct, but missing key facts
4: Mostly correct, minor details missing
5: Perfect match in semantic meaning to Gold Answer

Return only a JSON object: {"score": <int>, "reasoning": "<short string>"}
"""

async def evaluate_question(client, q):
    try:
        # Query the RAG system
        rag_resp = await client.post(
            QUERY_URL,
            json={"query": q["question"], "top_k": 5},
            headers={"X-Dev-User-Email": "test@redwood.com"}, # using dev header
            timeout=120.0
        )
        if rag_resp.status_code != 200:
            return 0, f"Error from RAG: {rag_resp.text}"
        
        rag_data = rag_resp.json()
        generated_answer = rag_data.get("answer", "")
        
        if not GROQ_API_KEY:
            return 0, "No GROQ_API_KEY set. Cannot run LLM judge."

        # Evaluate with Groq (llama-3.3-70b-versatile)
        groq_resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": JUDGE_PROMPT},
                    {"role": "user", "content": f"Question: {q['question']}\nGold Answer: {q['gold_answer']}\nGenerated Answer: {generated_answer}"}
                ],
                "response_format": {"type": "json_object"}
            },
            timeout=30.0
        )
        
        if groq_resp.status_code != 200:
            return 0, f"Groq Error: {groq_resp.text}"
            
        content = groq_resp.json()["choices"][0]["message"]["content"]
        result = json.loads(content)
        return result.get("score", 0), result.get("reasoning", "")
    except Exception as e:
        return 0, str(e)

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=10, help="Number of questions to sample")
    args = parser.parse_args()

    if not GROQ_API_KEY:
        print("❌ Error: GROQ_API_KEY is not set in environment.")
        return

    questions = []
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                questions.append(json.loads(line))
                
    sample = random.sample(questions, min(args.sample, len(questions)))
    print(f"Starting LLM-as-a-judge evaluation for {len(sample)} questions...")
    
    total_score = 0
    valid_evals = 0
    
    async with httpx.AsyncClient() as client:
        for i, q in enumerate(sample):
            print(f"\n[{i+1}/{len(sample)}] Q: {q['question']}")
            score, reasoning = await evaluate_question(client, q)
            print(f"  Score: {score}/5")
            print(f"  Reasoning: {reasoning}")
            if score > 0:
                total_score += score
                valid_evals += 1
                
    if valid_evals > 0:
        avg = total_score / valid_evals
        print(f"\n✅ End-to-End Evaluation Complete.")
        print(f"Average Answer Quality Score: {avg:.2f}/5.00")
    else:
        print("\n❌ No valid evaluations completed.")

if __name__ == "__main__":
    asyncio.run(main())
