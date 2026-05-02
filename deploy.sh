#!/bin/bash
# Deploy LawTasksAI API to Google Cloud Run
# Runs from Mac mini using gcloud Cloud Build (no local Docker daemon needed)
set -e

export PATH=/opt/homebrew/share/google-cloud-sdk/bin:$PATH

PROJECT_ID="km-clio"
SERVICE_NAME="lawtasksai-api"
REGION="us-central1"
IMAGE="gcr.io/$PROJECT_ID/$SERVICE_NAME"

echo "🏗️  Building image via Cloud Build..."
gcloud builds submit --tag $IMAGE .

echo "🚀 Deploying to Cloud Run..."
gcloud run deploy $SERVICE_NAME \
  --image $IMAGE \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --max-instances 10 \
  --min-instances 0 \
  --traffic-tags='' \
  --no-traffic

echo "🔀 Routing 100% traffic to latest revision..."
gcloud run services update-traffic $SERVICE_NAME \
  --to-latest \
  --region $REGION

echo "✅ Deployment complete!"
echo "🌐 API URL: https://$SERVICE_NAME-10437713249.$REGION.run.app"
