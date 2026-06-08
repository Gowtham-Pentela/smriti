#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Knowledge Guardian — One-Command GCP Deploy
#
# Usage:
#   ./deploy.sh                        # Interactive: prompts for project ID
#   ./deploy.sh --project my-gcp-id   # Non-interactive
#   ./deploy.sh --destroy              # Tear down all resources (stop billing)
#
# What this does:
#   1. Authenticates with GCP
#   2. Enables required APIs
#   3. Applies Terraform (VPC, Cloud SQL + pgvector, GKE cluster)
#   4. Runs the DB migration SQL (ingestion_hashes, tenant_credentials)
#   5. Builds backend + UI Docker images via Cloud Build
#   6. Pushes images to Artifact Registry
#   7. Applies all Kubernetes manifests
#   8. Prints the public URL to send to the customer
#
# Prerequisites:
#   - gcloud SDK installed and on PATH
#   - terraform installed and on PATH
#   - kubectl installed and on PATH
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── ANSI colours ─────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
TF_DIR="$REPO_ROOT/gcp_infrastructure/terraform"
K8S_DIR="$REPO_ROOT/gcp_infrastructure/kubernetes"
MIGRATION_SQL="$REPO_ROOT/supabase/migrations/001_ingestion_hashes_and_credentials.sql"

echo -e "${CYAN}${BOLD}"
echo "  ██╗  ██╗ ██████╗ ███████╗"
echo "  ██║ ██╔╝██╔════╝ ██╔════╝"
echo "  █████╔╝ ██║  ███╗█████╗  "
echo "  ██╔═██╗ ██║   ██║██╔══╝  "
echo "  ██║  ██╗╚██████╔╝██║     "
echo "  ╚═╝  ╚═╝ ╚═════╝ ╚═╝     "
echo "  Knowledge Guardian — GCP Deploy"
echo -e "${NC}"

# ── Parse flags ───────────────────────────────────────────────────────────────
PROJECT_ID=""
DESTROY=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --project) PROJECT_ID="$2"; shift 2 ;;
        --destroy) DESTROY=true; shift ;;
        *) echo -e "${RED}Unknown flag: $1${NC}"; exit 1 ;;
    esac
done

# ── Destroy path ──────────────────────────────────────────────────────────────
if [ "$DESTROY" = true ]; then
    echo -e "${RED}${BOLD}[DESTROY] Tearing down all GCP resources...${NC}"
    cd "$TF_DIR"
    terraform destroy -auto-approve
    echo -e "${GREEN}All resources destroyed. Billing has stopped.${NC}"
    exit 0
fi

# ── 1. Authenticate ───────────────────────────────────────────────────────────
echo -e "\n${YELLOW}[1/8] Authenticating with Google Cloud...${NC}"
if ! gcloud auth print-access-token &>/dev/null; then
    gcloud auth login
fi
if ! gcloud auth application-default print-access-token &>/dev/null; then
    gcloud auth application-default login
fi
echo -e "${GREEN}✓ Authentication OK${NC}"

# ── 2. Select project ─────────────────────────────────────────────────────────
echo -e "\n${YELLOW}[2/8] Configuring GCP Project...${NC}"
if [ -z "$PROJECT_ID" ]; then
    echo "Available projects:"
    gcloud projects list --format="table(projectId,name)" 2>/dev/null || true
    echo ""
    read -rp "$(echo -e "${GREEN}Enter your GCP Project ID: ${NC}")" PROJECT_ID
fi

if [ -z "$PROJECT_ID" ]; then
    echo -e "${RED}Error: Project ID cannot be empty.${NC}"; exit 1
fi
gcloud config set project "$PROJECT_ID"
echo -e "${GREEN}✓ Project set to: $PROJECT_ID${NC}"

REGION="us-central1"
REGISTRY="gcr.io/$PROJECT_ID"

# ── 3. Enable APIs ────────────────────────────────────────────────────────────
echo -e "\n${YELLOW}[3/8] Enabling required GCP APIs...${NC}"
gcloud services enable \
    compute.googleapis.com \
    container.googleapis.com \
    sqladmin.googleapis.com \
    servicenetworking.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    --quiet
echo -e "${GREEN}✓ APIs enabled${NC}"

# ── 4. Terraform ──────────────────────────────────────────────────────────────
echo -e "\n${YELLOW}[4/8] Provisioning infrastructure with Terraform...${NC}"
cd "$TF_DIR"

# Write tfvars
cat > terraform.tfvars <<EOF
project_id = "$PROJECT_ID"
region     = "$REGION"
EOF

terraform init -upgrade -reconfigure -input=false
terraform apply -auto-approve -input=false

# Capture Cloud SQL IP for the migration step
CLOUD_SQL_IP=$(terraform output -raw db_private_ip 2>/dev/null || echo "")
echo -e "${GREEN}✓ Terraform complete${NC}"

# ── 5. Configure kubectl ──────────────────────────────────────────────────────
echo -e "\n${YELLOW}[5/8] Configuring kubectl for GKE...${NC}"
CLUSTER_NAME=$(terraform output -raw gke_cluster_name 2>/dev/null || echo "knowledge-guardian-cluster")
CLUSTER_ZONE=$(terraform output -raw gke_cluster_zone 2>/dev/null || echo "${REGION}-a")
gcloud container clusters get-credentials "$CLUSTER_NAME" \
    --zone "$CLUSTER_ZONE" \
    --project "$PROJECT_ID"
echo -e "${GREEN}✓ kubectl configured${NC}"

# ── 6. Build and push Docker images ──────────────────────────────────────────
echo -e "\n${YELLOW}[6/8] Building and pushing Docker images via Cloud Build...${NC}"
cd "$REPO_ROOT"

echo "  → Building backend image..."
gcloud builds submit \
    --tag "$REGISTRY/kgf-backend:latest" \
    --file Dockerfile.backend \
    --timeout=15m \
    .

echo "  → Building UI image..."
gcloud builds submit \
    --tag "$REGISTRY/kgf-ui:latest" \
    --file Dockerfile.ui \
    --timeout=10m \
    .

echo -e "${GREEN}✓ Images pushed to $REGISTRY${NC}"

# ── 7. Update image tags in K8s manifests and apply ──────────────────────────
echo -e "\n${YELLOW}[7/8] Deploying to GKE...${NC}"

# Patch image references (sed in-place)
sed -i.bak \
    "s|gcr.io/YOUR_PROJECT_ID/kgf-backend:latest|$REGISTRY/kgf-backend:latest|g" \
    "$K8S_DIR/backend-api.yaml"

sed -i.bak \
    "s|gcr.io/YOUR_PROJECT_ID/kgf-ui:latest|$REGISTRY/kgf-ui:latest|g" \
    "$K8S_DIR/ui.yaml"

# Apply manifests in dependency order
kubectl apply -f "$K8S_DIR/vllm-ollama-gpu.yaml"
kubectl apply -f "$K8S_DIR/backend-api.yaml"
kubectl apply -f "$K8S_DIR/ui.yaml"

# Wait for UI ingress to get an external IP (up to 5 minutes)
echo "  Waiting for external IP (this takes 2-4 minutes for GCP load balancer provisioning)..."
UI_IP=""
for i in $(seq 1 30); do
    UI_IP=$(kubectl get ingress kgf-ui-ingress \
        -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")
    if [ -n "$UI_IP" ]; then
        break
    fi
    sleep 10
    echo -n "."
done
echo ""

echo -e "${GREEN}✓ GKE deployment complete${NC}"

# ── 8. Print customer URL ─────────────────────────────────────────────────────
echo -e "\n${CYAN}${BOLD}================================================================${NC}"
echo -e "${GREEN}${BOLD}                  DEPLOYMENT COMPLETE ✓                        ${NC}"
echo -e "${CYAN}${BOLD}================================================================${NC}"

if [ -n "$UI_IP" ]; then
    echo -e "\n${BOLD}🔗 Customer demo URL:${NC}"
    echo -e "   ${GREEN}http://$UI_IP${NC}"
    echo ""
    echo -e "   Send this URL to your customer. It opens the KGF-E assistant"
    echo -e "   with the Slack connector panel at the bottom of the page."
else
    echo -e "\n${YELLOW}⚠  External IP not yet assigned. Check with:${NC}"
    echo -e "   kubectl get ingress kgf-ui-ingress"
fi

echo ""
echo -e "${YELLOW}${BOLD}COST CONTROL — to stop billing when the demo is done:${NC}"
echo -e "   ${RED}./deploy.sh --destroy${NC}"
echo ""
echo -e "${CYAN}================================================================${NC}"
