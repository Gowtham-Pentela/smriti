from transformers import AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained("cross-encoder/ms-marco-MiniLM-L6-v2")
pairs = [["query", "text one"], ["query", "text two"]]

inputs_direct = tokenizer(pairs, padding=True, truncation=True, return_tensors="pt")
inputs_split = tokenizer([p[0] for p in pairs], [p[1] for p in pairs], padding=True, truncation=True, return_tensors="pt")

print("Direct input keys:", inputs_direct.keys())
print("Split input keys:", inputs_split.keys())
print("Equal input_ids?", (inputs_direct['input_ids'] == inputs_split['input_ids']).all().item())
