# ─────────────────────────────────────────────────────────────────────────────
# Smriti — AWS deployment (Terraform)
# ─────────────────────────────────────────────────────────────────────────────
# Provisions the minimum infrastructure to run Smriti on AWS:
#   - S3 bucket for document uploads (EventBridge fires on ObjectCreated)
#   - SQS queue (raw-message-delivery) that the backend worker long-polls
#   - EventBridge rule wiring the bucket to the queue
#   - IAM role for the backend to read S3 + receive/delete SQS messages
#
# Usage:
#   cd deploy/aws
#   terraform init
#   terraform apply \
#     -var "bucket_name=acme-internal-docs" \
#     -var "queue_name=smriti-ingest"
#
# After apply, set in the backend's .env:
#   S3_BUCKET=acme-internal-docs
#   S3_QUEUE_URL=<output queue_url>
# ─────────────────────────────────────────────────────────────────────────────

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "bucket_name" {
  type        = string
  description = "Name of the S3 bucket where users drop documents. Must be globally unique."
}

variable "queue_name" {
  type        = string
  default     = "smriti-ingest"
  description = "Name of the SQS queue the backend worker long-polls."
}

variable "dead_letter_queue_name" {
  type        = string
  default     = "smriti-ingest-dlq"
  description = "Dead-letter queue for messages that fail repeatedly."
}

# ─── Provider ────────────────────────────────────────────────────────────────
provider "aws" {
  region = var.region
}

# ─── S3 bucket ───────────────────────────────────────────────────────────────
resource "aws_s3_bucket" "docs" {
  bucket = var.bucket_name
  force_destroy = false  # ponytail: set true for dev so `terraform destroy` works
}

resource "aws_s3_bucket_versioning" "docs" {
  bucket = aws_s3_bucket.docs.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "docs" {
  bucket = aws_s3_bucket.docs.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "docs" {
  bucket                  = aws_s3_bucket.docs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ─── SQS queues ──────────────────────────────────────────────────────────────
resource "aws_sqs_queue" "ingest" {
  name                       = var.queue_name
  visibility_timeout_seconds = 600       # 10 min — long enough for video transcription
  message_retention_seconds  = 1209600   # 14 days
  receive_wait_time_seconds  = 20        # long polling
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_sqs_queue" "dlq" {
  name                    = var.dead_letter_queue_name
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled = true
}

# SQS queue policy — allow EventBridge to send messages
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

resource "aws_sqs_queue_policy" "ingest" {
  queue_url = aws_sqs_queue.ingest.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowEventBridgeSendMessage"
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = "sqs:SendMessage"
        Resource  = aws_sqs_queue.ingest.arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = aws_cloudwatch_event_rule.s3_created.arn
          }
        }
      }
    ]
  })
}

# ─── EventBridge: S3 ObjectCreated → SQS ─────────────────────────────────────
resource "aws_cloudwatch_event_rule" "s3_created" {
  name        = "${var.bucket_name}-object-created"
  description = "Fires on S3 ObjectCreated events in the docs bucket"
  state       = "ENABLED"

  event_pattern = jsonencode({
    "source"      = ["aws.s3"]
    "detail-type" = ["Object Created"]
    "detail" = {
      "bucket" = { "name" = [var.bucket_name] }
    }
  })
}

resource "aws_cloudwatch_event_target" "s3_to_sqs" {
  rule      = aws_cloudwatch_event_rule.s3_created.name
  target_id = "smriti-sqs"
  arn       = aws_sqs_queue.ingest.arn

  # Raw message delivery — the worker parses the S3 envelope directly,
  # so we don't need EventBridge to wrap the event in its own JSON.
  # ponytail: if you ever need a transformer, add `input_transformer` here.
  sqs_target {
    message_group_id = "default"  # not used by standard queues but required by the schema
  }
}

# ─── IAM role for the backend ───────────────────────────────────────────────
# Attach this role to the EC2 instance, ECS task, or EKS service account that
# runs the backend. The role is intentionally minimal: read-only S3, scoped SQS.

data "aws_iam_policy_document" "backend_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com", "ecs-tasks.amazonaws.com", "eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "backend" {
  name               = "smriti-backend"
  assume_role_policy = data.aws_iam_policy_document.backend_assume.json
}

data "aws_iam_policy_document" "backend_inline" {
  # S3 read on the docs bucket only
  statement {
    sid    = "S3Read"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:ListBucket",
    ]
    resources = [
      aws_s3_bucket.docs.arn,
      "${aws_s3_bucket.docs.arn}/*",
    ]
  }

  # SQS on the ingest queue + DLQ only
  statement {
    sid    = "SQSRead"
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
      "sqs:ChangeMessageVisibility",
    ]
    resources = [
      aws_sqs_queue.ingest.arn,
      aws_sqs_queue.dlq.arn,
    ]
  }
}

resource "aws_iam_role_policy" "backend_inline" {
  name   = "smriti-backend-policy"
  role   = aws_iam_role.backend.id
  policy = data.aws_iam_policy_document.backend_inline.json
}

# ─── Outputs ─────────────────────────────────────────────────────────────────
output "bucket_name" {
  value = aws_s3_bucket.docs.id
}

output "queue_url" {
  value = aws_sqs_queue.ingest.url
}

output "queue_arn" {
  value = aws_sqs_queue.ingest.arn
}

output "dlq_url" {
  value = aws_sqs_queue.dlq.url
}

output "backend_role_arn" {
  value = aws_iam_role.backend.arn
}
