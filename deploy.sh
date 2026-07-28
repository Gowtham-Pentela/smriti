#!/bin/bash
# Build and push the Smriti backend image to ECR, then trigger an ECS deploy.
# Prereqs:
#   - aws cli configured (instance role or env vars)
#   - ECR repo already created:  aws ecr create-repository --repository-name smriti
#   - ECS cluster + service + task definition exist (see deploy/aws/)
set -euo pipefail

cd "$(dirname "$0")"

AWS_REGION=${AWS_REGION:-us-east-1}
ECR_REPO=${ECR_REPO:-smriti}
IMAGE_TAG=${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d-%H%M%S)}

# 1. ECR login
ECR_REGISTRY=$(aws ecr describe-repositories --repository-names "$ECR_REPO" --query 'repositories[0].repositoryUri' --output text --region "$AWS_REGION" | cut -d/ -f1)
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ECR_REGISTRY"

# 2. Build
echo "→ Building image..."
docker build -f Dockerfile.backend -t "$ECR_REGISTRY/$ECR_REPO:$IMAGE_TAG" .
docker tag "$ECR_REGISTRY/$ECR_REPO:$IMAGE_TAG" "$ECR_REGISTRY/$ECR_REPO:latest"

# 3. Push
echo "→ Pushing to ECR..."
docker push "$ECR_REGISTRY/$ECR_REPO:$IMAGE_TAG"
docker push "$ECR_REGISTRY/$ECR_REPO:latest"

# 4. Trigger ECS rollout (no-op if ECS_STACK not set)
if [ -n "${ECS_SERVICE:-}" ] && [ -n "${ECS_CLUSTER:-}" ]; then
    echo "→ Forcing ECS service update..."
    aws ecs update-service --cluster "$ECS_CLUSTER" --service "$ECS_SERVICE" \
        --force-new-deployment --region "$AWS_REGION" >/dev/null
    echo "✅ ECS service updated"
else
    echo "(Set ECS_SERVICE + ECS_CLUSTER env vars to trigger an automatic ECS rollout.)"
fi

echo ""
echo "✅ Deployed $ECR_REGISTRY/$ECR_REPO:$IMAGE_TAG"
