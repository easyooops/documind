#!/bin/bash
set -euo pipefail
exec > >(tee /var/log/documind-bootstrap.log | logger -t documind-bootstrap -s 2>/dev/console) 2>&1

export DEBIAN_FRONTEND=noninteractive
SOURCE_ARCHIVE_ETAG="${source_archive_etag}"
PRODUCT_ARCHIVE_ETAG="${product_archive_etag}"

echo "DocuMind bootstrap artifacts: source=$SOURCE_ARCHIVE_ETAG product=$PRODUCT_ARCHIVE_ETAG"

wait_apt_lock() {
  for _ in $(seq 1 90); do
    if ! fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock /var/lib/dpkg/lock >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done
  return 1
}

apt_run() {
  local attempt
  for attempt in 1 2 3 4 5; do
    wait_apt_lock || true
    if apt-get "$@"; then
      return 0
    fi
    echo "apt-get $* failed on attempt $attempt; retrying in 15s" >&2
    sleep 15
  done
  return 1
}

apt_run update -y
apt_run install -y docker.io docker-compose-v2 curl ca-certificates unzip awscli
update-ca-certificates

systemctl enable --now docker
for _ in $(seq 1 30); do
  docker info >/dev/null 2>&1 && break
  sleep 2
done

PUBLIC_IP="${expected_public_ip}"
if [ -z "$PUBLIC_IP" ]; then
  IMDS_TOKEN=$(curl -sS -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" || true)
  if [ -n "$IMDS_TOKEN" ]; then
    PUBLIC_IP=$(curl -sS -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4 || true)
  else
    PUBLIC_IP=$(curl -sS http://169.254.169.254/latest/meta-data/public-ipv4 || true)
  fi
  if [ -z "$PUBLIC_IP" ]; then
    PUBLIC_IP="localhost"
  fi
fi

mkdir -p /opt/documind /var/lib/documind/outputs /var/lib/documind/logs
cd /opt/documind

aws s3 cp "s3://${stage_bucket}/${source_object_key}" ./documind-source.zip --region "${aws_region}"
aws s3 cp "s3://${stage_bucket}/${product_object_key}" ./documind-product.zip --region "${aws_region}"

rm -rf src product deploy
mkdir -p src product deploy
unzip -q -o documind-source.zip -d src
unzip -q -o documind-product.zip -d product

API_URL="http://$PUBLIC_IP:${documind_api_port}"

docker build \
  -f product/images/api/Dockerfile \
  -t "${documind_api_image_tag}" \
  src

docker build \
  -f product/images/web/Dockerfile \
  -t "${documind_web_image_tag}" \
  --build-arg "NEXT_PUBLIC_API_URL=$API_URL" \
  --build-arg "NEXT_PUBLIC_STREAM_API_URL=$API_URL" \
  --build-arg "DOCUMIND_INTERNAL_API_URL=http://documind-api:8000" \
  src/web

cp product/compose/docker-compose.app.yml deploy/

cat > deploy/.env <<EOF
DOCUMIND_API_IMAGE=${documind_api_image_tag}
DOCUMIND_WEB_IMAGE=${documind_web_image_tag}
DOCUMIND_API_HOST_PORT=${documind_api_port}
DOCUMIND_WEB_HOST_PORT=${documind_web_port}

APP_ENV=production
APP_PORT=8000
LOG_LEVEL=INFO
LOG_FILE=/var/lib/documind/logs/documind.log
CORS_ORIGINS=http://$PUBLIC_IP:${documind_web_port},http://$PUBLIC_IP:${documind_api_port}

DATABASE_TYPE=${database_type}
DATABASE_PATH=/var/lib/documind/documind.db
DB_HOST=${db_host}
DB_PORT=${db_port}
DB_NAME=${db_name}
DB_USER=${db_user}
DB_PASSWORD=${db_password}

STORAGE_TYPE=${storage_type}
STORAGE_LOCAL_PATH=/var/lib/documind/outputs
AWS_S3_BUCKET=${s3_bucket}
AWS_S3_REGION=${aws_region}

LLM_PROVIDER=${llm_provider}
USE_DEFAULT_MODELS=${use_default_models}
DEFAULT_LLM_MODEL=${default_llm_model}
DEFAULT_VLM_MODEL=${default_vlm_model}
DEFAULT_IMAGE_MODEL=${default_image_model}

AWS_REGION=${aws_bedrock_region}

OPENAI_API_KEY=${openai_api_key}
ANTHROPIC_API_KEY=${anthropic_api_key}
GOOGLE_API_KEY=${google_api_key}
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_API_VERSION=2024-06-01
AZURE_OPENAI_DEPLOYMENT=
GCP_PROJECT_ID=
GCP_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=
CUSTOM_LLM_BASE_URL=${custom_llm_base_url}
CUSTOM_LLM_API_KEY=${custom_llm_api_key}

NEXT_PUBLIC_API_URL=$API_URL
NEXT_PUBLIC_STREAM_API_URL=$API_URL
DOCUMIND_INTERNAL_API_URL=http://documind-api:8000
EOF
chmod 600 deploy/.env

cd deploy
docker compose -f docker-compose.app.yml --env-file .env up -d

for _ in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:${documind_api_port}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 5
done

docker compose -f docker-compose.app.yml --env-file .env ps

echo "DocuMind bootstrap finished."
echo "  API : http://$PUBLIC_IP:${documind_api_port}"
echo "  Web : http://$PUBLIC_IP:${documind_web_port}"
