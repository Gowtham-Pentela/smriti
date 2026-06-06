#!/bin/bash
# GCP Deployment & Ephemeral Lifecycle Management Script
# Designed for Knowledge Guardian local-to-cloud transition

set -e

# ANSI Color Codes
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}==================================================================${NC}"
echo -e "${CYAN}             KNOWLEDGE GUARDIAN GCP DEPLOYMENT UTILITY            ${NC}"
echo -e "${CYAN}==================================================================${NC}"
echo -e "This utility helps you authenticate, select your project, configure Terraform,"
echo -e "and manage the GKE Spot GPU cluster for cost-optimized demo runs."
echo -e ""

# 1. Authenticate with GCP
echo -e "${YELLOW}[1/4] Authenticating with Google Cloud...${NC}"
echo -e "A browser window will open for you to sign in to your Google Account."
gcloud auth login

echo -e "\n${YELLOW}Setting up Application Default Credentials for Terraform...${NC}"
echo -e "Please authenticate in the browser once more to generate local Terraform access tokens."
gcloud auth application-default login

# 2. Select Project
echo -e "\n${YELLOW}[2/4] Configuring GCP Project...${NC}"
echo "Retrieving available projects..."
gcloud projects list || true

echo -e "\n${GREEN}Please enter the GCP Project ID you want to deploy to:${NC}"
read -p "Project ID: " PROJECT_ID

if [ -z "$PROJECT_ID" ]; then
    echo -e "${RED}Error: Project ID cannot be empty.${NC}"
    exit 1
fi

gcloud config set project "$PROJECT_ID"

# 3. Enable Required APIs
echo -e "\n${YELLOW}[3/4] Enabling required GCP APIs (Compute, GKE, Cloud SQL)...${NC}"
gcloud services enable \
    compute.googleapis.com \
    container.googleapis.com \
    sqladmin.googleapis.com \
    servicenetworking.googleapis.com

# 4. Generate terraform.tfvars
echo -e "\n${YELLOW}[4/4] Configuring Terraform variables...${NC}"
TF_VARS_FILE="gcp_infrastructure/terraform/terraform.tfvars"
echo "project_id = \"$PROJECT_ID\"" > "$TF_VARS_FILE"
echo -e "${GREEN}Created: $TF_VARS_FILE${NC}"

echo -e "\n${CYAN}==================================================================${NC}"
echo -e "${GREEN}                  CONFIGURATION COMPLETE!                         ${NC}"
echo -e "${CYAN}==================================================================${NC}"
echo -e "To launch your cost-optimized demo (which costs ~$0.22/hour for the L4 GPU):"
echo -e "  1. Move to the Terraform directory: ${CYAN}cd gcp_infrastructure/terraform${NC}"
echo -e "  2. Initialize Terraform:            ${CYAN}terraform init${NC}"
echo -e "  3. Deploy the resources:            ${CYAN}terraform apply${NC}"
echo -e ""
echo -e "${RED}CRITICAL STEP FOR COST CONTROL:${NC}"
echo -e "Once your demo is finished, run the following command in the same directory"
echo -e "to tear down all resources and stop GCP billing immediately:"
echo -e "  ${RED}terraform destroy${NC}"
echo -e "${CYAN}==================================================================${NC}"
