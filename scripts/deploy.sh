#!/usr/bin/env bash
set -euo pipefail

#
# Deploy script for AI Cloud Monitor
# Usage:
#   ./scripts/deploy.sh [docker|k8s] [build|deploy|all]
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC} \$1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} \$1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} \$1"; }
log_error() { echo -e "${RED}[ERROR]${NC} \$1"; }

MODE="${1:-docker}"
ACTION="${2:-all}"

# ── Docker Compose ─────────────────────────────────────────
docker_build() {
    log_info "Building Docker images..."
    docker compose build --parallel
    log_ok "Docker images built successfully"
}

docker_deploy() {
    log_info "Starting services with Docker Compose..."
    docker compose up -d
    log_ok "Services started!"

    log_info "Waiting for services to be healthy..."
    sleep 10

    log_info "Service status:"
    docker compose ps

    echo ""
    log_ok "Dashboard available at: http://localhost:5000"
}

docker_stop() {
    log_info "Stopping services..."
    docker compose down
    log_ok "Services stopped"
}

docker_logs() {
    docker compose logs -f --tail=100
}

# ── Kubernetes ─────────────────────────────────────────────
k8s_build() {
    log_info "Building Docker images for Kubernetes..."
    docker build -t cloud-monitor-collector:latest -f Dockerfile.collector .
    docker build -t cloud-monitor-detector:latest -f Dockerfile.detector .
    docker build -t cloud-monitor-dashboard:latest -f Dockerfile.dashboard .
    docker build -t cloud-monitor-alertmanager:latest -f Dockerfile.alertmanager .
    log_ok "All images built"
}

k8s_deploy() {
    log_info "Deploying to Kubernetes..."

    kubectl apply -f k8s/namespace.yaml
    log_ok "Namespace created"

    kubectl apply -f k8s/configmap.yaml
    log_ok "ConfigMap & Secrets applied"

    kubectl apply -f k8s/redis-deployment.yaml
    log_ok "Redis deployed"

    log_info "Waiting for Redis to be ready..."
    kubectl -n cloud-monitor wait --for=condition=ready pod -l app=redis --timeout=120s

    kubectl apply -f k8s/collector-deployment.yaml
    log_ok "Collector deployed"

    kubectl apply -f k8s/detector-deployment.yaml
    log_ok "Detector deployed"

    kubectl apply -f k8s/alertmanager-deployment.yaml
    log_ok "Alert Manager deployed"

    kubectl apply -f k8s/dashboard-deployment.yaml
    log_ok "Dashboard deployed"

    kubectl apply -f k8s/ingress.yaml
    log_ok "Ingress configured"

    echo ""
    log_info "Kubernetes deployment status:"
    kubectl -n cloud-monitor get pods
    kubectl -n cloud-monitor get svc

    echo ""
    log_ok "Deployment complete!"
    log_info "Port-forward dashboard: kubectl -n cloud-monitor port-forward svc/dashboard-service 5000:80"
}

k8s_delete() {
    log_warn "Deleting all cloud-monitor resources..."
    kubectl delete namespace cloud-monitor --ignore-not-found
    log_ok "Resources deleted"
}

# ── Main ───────────────────────────────────────────────────
echo "========================================"
echo "  AI Cloud Monitor - Deploy Script"
echo "  Mode: $MODE | Action: $ACTION"
echo "========================================"
echo ""

case "$MODE" in
    docker)
        case "$ACTION" in
            build)   docker_build ;;
            deploy)  docker_deploy ;;
            stop)    docker_stop ;;
            logs)    docker_logs ;;
            all)     docker_build && docker_deploy ;;
            *)       log_error "Unknown action: $ACTION" ;;
        esac
        ;;
    k8s|kubernetes)
        case "$ACTION" in
            build)   k8s_build ;;
            deploy)  k8s_deploy ;;
            delete)  k8s_delete ;;
            all)     k8s_build && k8s_deploy ;;
            *)       log_error "Unknown action: $ACTION" ;;
        esac
        ;;
    *)
        log_error "Unknown mode: $MODE"
        echo "Usage: \$0 [docker|k8s] [build|deploy|stop|logs|delete|all]"
        exit 1
        ;;
esac