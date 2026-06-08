# Benchmark Evaluation Results

- **Timestamp:** Sun Jun  7 23:43:25 2026
- **Source:** EnterpriseRAG-Bench questions.jsonl
- **Chunks indexed:** 4048
- **Questions evaluated:** 345

## Slack-Only Recall (the meaningful number)
- **Slack questions:** 79
- **Slack Recall @ 10:** 74.66% (72/79 successfully retrieved)
- **Slack capture failures:** 7

## Overall (all 345 questions)
- **Mean Precision @ 10:** 2.12%
- **Mean Recall @ 10:** 17.10% (depressed by 266 non-Slack questions with expected=0)

## Latency
- **p50:** 0.1901s (190.1ms)
- **p95:** 0.2437s (243.7ms)

## Failure Breakdown
- **Total failures (recall=0):** 273
- **Slack failures:** 7
- **Non-Slack failures:** 266 (expected — only Slack indexed)
