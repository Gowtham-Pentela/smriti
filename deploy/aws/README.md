# Smriti AWS infrastructure

Provisions the minimum AWS infra to run Smriti as an internal company ChatGPT:

- **S3 bucket** for document uploads (encrypted, public access blocked, versioning on)
- **SQS queue** with 10-min visibility (long enough for video transcription), 5-receive DLQ
- **EventBridge rule** wiring `Object Created` on the bucket → SQS
- **IAM role** for the backend with least-privilege `s3:GetObject` + `sqs:ReceiveMessage` policies

## Usage

```bash
cd deploy/aws
terraform init
terraform apply \
  -var "bucket_name=acme-internal-docs" \
  -var "queue_name=smriti-ingest"
```

The outputs are the values you set in `backend/.env`:

```
S3_BUCKET=acme-internal-docs
S3_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/<account>/smriti-ingest
```

## Manual S3 trigger (for testing)

If you don't want to wire EventBridge end-to-end yet, you can use the worker's `--local-folder` mode:

```bash
python -m backend.s3_connector --local-folder ./test_uploads
```

This walks a local directory and ingests everything — no SQS, no S3.

## Scaling notes

- **S3 → SQS delivery is best-effort.** A single large upload can fire multiple events. The `ingestion_hashes` table makes this safe (re-deliveries are no-ops).
- **SQS long polling** is enabled (20s WaitTime). The worker thread is single-threaded; for high throughput, run multiple workers (each with its own boto3 client) and ensure Ollama is on a GPU instance.
- **DLQ** is for messages that fail 5 times. Inspect with `aws sqs receive-message --queue-url <dlq>`.

## Hardening checklist (production)

- [ ] Add a VPC endpoint for S3 so traffic doesn't traverse the public internet.
- [ ] Restrict the S3 bucket policy to only your VPC CIDR.
- [ ] Enable CloudTrail data events for the bucket.
- [ ] Add CloudWatch alarms on `ApproximateNumberOfMessagesVisible > 100` (queue backlog).
- [ ] Add an S3 lifecycle policy to move old objects to Glacier (optional).
