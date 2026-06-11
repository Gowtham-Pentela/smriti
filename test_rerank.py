import time
import os
t0 = time.time()
print("importing...")
from sentence_transformers import CrossEncoder
print(f"imported in {time.time()-t0:.2f}s, loading...")
model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L6-v2", max_length=512, device='cpu')
print(f"loaded in {time.time()-t0:.2f}s")
