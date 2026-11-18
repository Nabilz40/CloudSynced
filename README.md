# CloudSynced: Distributed Media Synchronizer

A cloud-native, containerized backend microservice built with **Python (FastAPI)**, **Docker**, **Redis**, **Prometheus**, and **Kubernetes** to synchronize media playback states across remote clients with high availability, persistent configuration storage, and CPU-based horizontal autoscaling.

## System Architecture

```text
                       +--------------------------+
Remote Clients ------> | Kubernetes LoadBalancer  | (port 8080 -> 8000)
                       +------------+-------------+
                                    |
          +-------------------------+-------------------------+
          v                         v                         v
+-------------------+     +-------------------+     +-------------------+
| cloudsynced-app   |     | cloudsynced-app   | ... | cloudsynced-app   |
| (Pod Replica 1)   |     | (Pod Replica 2)   |     | (HPA max: 5)      |
+---------+---------+     +---------+---------+     +-------------------+
          |                         |
          +------------+------------+
                       |
         +-------------+-------------+
         v                           v
+------------------+       +-------------------------+
| Redis Service    |       | PersistentVolumeClaim   |
| (Shared State)   |       | (/data/config.json)     |
+------------------+       +-------------------------+
                                     ^
                                     |
                           +---------+---------+
                           | Prometheus Server | (scrapes /metrics :8000)
                           +-------------------+
```

## Cloud Concepts & Features

- **Containerization:** Multi-layer `Dockerfile` running FastAPI/Uvicorn as a non-root user (`appuser`).
- **High Availability:** Minimum 2 active replicas behind a Kubernetes `LoadBalancer` Service with `/health` readiness and liveness probes.
- **Distributed State & Storage:** Shared live playback state via Redis (`ClusterIP` Service) + persistent configuration via Kubernetes `PersistentVolumeClaim`.
- **Automated Horizontal Scaling:** `HorizontalPodAutoscaler` (`autoscaling/v2`) scales `cloudsynced-app` from 2 to 5 replicas when average CPU utilization exceeds 50%.
- **Observability & Monitoring:** Prometheus service discovery scraping `/metrics` across all pods + `metrics-server` CPU/memory telemetry.

## Project Structure

- `app/` — FastAPI microservice, built-in sync web UI, `config.json`, `requirements.txt`, `Dockerfile`
- `k8s/` — Kubernetes manifests (`storage.yaml`, `redis.yaml`, `deployment.yaml`, `service.yaml`, `hpa.yaml`)
- `monitoring/` — Prometheus deployment & RBAC/service-discovery config (`prometheus.yaml`)
- `scripts/` — Concurrent CPU load generator (`load_test.py`)

## Deployment Quick Start (Minikube)

```bash
minikube start --driver=docker
minikube addons enable metrics-server

docker build -t cloudsynced:1.0 ./app
minikube image load cloudsynced:1.0

kubectl apply -f k8s/
kubectl apply -f monitoring/prometheus.yaml

# In a separate terminal (keep open):
minikube tunnel
```

Access the app at `http://127.0.0.1:8080` and Prometheus at `http://127.0.0.1:9090`.

## Demo Scenarios

1. **Load Balancing:** Open multiple clients at `http://127.0.0.1:8080` or run `for i in {1..6}; do curl -s http://127.0.0.1:8080/health; echo ""; done` to observe traffic distributed across pods while state stays synchronized via Redis.
2. **Failure & Self-Healing:** Terminate a pod (`kubectl delete pod <pod-name>`) while clients stay connected; the LoadBalancer reroutes traffic to surviving replicas while Kubernetes recreates the deleted pod.
3. **CPU Autoscaling:** Run `python scripts/load_test.py` while watching `kubectl get hpa -w` to observe automatic scale-out from 2 to 5 replicas.
