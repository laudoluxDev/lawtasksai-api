#!/bin/bash
# Deploy LawTasksAI API to Google Cloud Run
set -e

PROJECT_ID="km-clio"
SERVICE_NAME="lawtasksai-api"
REGION="us-central1"
IMAGE="gcr.io/$PROJECT_ID/$SERVICE_NAME"

echo "🏗️  Building Docker image..."
docker build -t $IMAGE .

echo "📤 Pushing to Google Container Registry..."
docker push $IMAGE

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
  --set-env-vars "DATABASE_URL=$DATABASE_URL,STRIPE_SECRET_KEY=$STRIPE_SECRET_KEY,ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY,API_BASE_URL=https://$SERVICE_NAME-431860735682.$REGION.run.app,FRONTEND_URL=https://lawtasksai.com"

echo "✅ Deployment complete!"
echo "🌐 API URL: https://$SERVICE_NAME-431860735682.$REGION.run.app"
