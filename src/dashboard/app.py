"""
Flask dashboard application – provides REST API and web UI.
"""

import time
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify, request

from src.common.config import config
from src.common.redis_client import RedisClient
from src.common.logger import get_logger

logger = get_logger("dashboard")

redis_client = RedisClient()


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.secret_key = config.secret_key

    # ── Web UI Routes ──────────────────────────────────────

    @app.route("/")
    def index():
        return render_template("dashboard.html")

    # ── API: Metrics ───────────────────────────────────────

    @app.route("/api/metrics/recent")
    def api_recent_metrics():
        count = request.args.get("count", 200, type=int)
        metrics = redis_client.get_recent_metrics(count)
        return jsonify({"metrics": metrics, "count": len(metrics)})

    @app.route("/api/metrics/history/<host>/<metric_type>")
    def api_metric_history(host, metric_type):
        minutes = request.args.get("minutes", 60, type=int)
        history = redis_client.get_metric_history(host, metric_type, minutes)
        return jsonify({
            "host": host,
            "metric_type": metric_type,
            "data": history,
            "count": len(history),
        })

    @app.route("/api/metrics/series")
    def api_metric_series():
        keys = redis_client.get_all_metric_keys()
        series = []
        for key in keys:
            parts = key.split(":")
            if len(parts) >= 4:
                series.append({
                    "key": key,
                    "host": parts[2],
                    "metric_type": parts[3],
                })
        return jsonify({"series": series, "count": len(series)})

    # ── API: Anomalies ─────────────────────────────────────

    @app.route("/api/anomalies/recent")
    def api_recent_anomalies():
        count = request.args.get("count", 50, type=int)
        anomalies = redis_client.get_recent_anomalies(count)
        return jsonify({"anomalies": anomalies, "count": len(anomalies)})

    # ── API: Alerts ────────────────────────────────────────

    @app.route("/api/alerts/recent")
    def api_recent_alerts():
        count = request.args.get("count", 50, type=int)
        alerts = redis_client.get_recent_alerts(count)
        return jsonify({"alerts": alerts, "count": len(alerts)})

    @app.route("/api/alerts/<alert_id>/acknowledge", methods=["POST"])
    def api_acknowledge_alert(alert_id):
        user = request.json.get("user", "dashboard_user") if request.json else "dashboard_user"
        # In a full implementation, this would reach the alert manager
        logger.info(f"Alert {alert_id} acknowledged by {user}")
        return jsonify({"status": "acknowledged", "alert_id": alert_id})

    @app.route("/api/alerts/<alert_id>/resolve", methods=["POST"])
    def api_resolve_alert(alert_id):
        logger.info(f"Alert {alert_id} resolved via API")
        return jsonify({"status": "resolved", "alert_id": alert_id})

    # ── API: System Health ─────────────────────────────────

    @app.route("/api/health")
    def api_health():
        services = redis_client.get_all_service_health()
        redis_ok = redis_client.ping()
        return jsonify({
            "status": "healthy" if redis_ok else "degraded",
            "redis_connected": redis_ok,
            "services": services,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    @app.route("/api/stats")
    def api_stats():
        stats = redis_client.get_stats()
        return jsonify(stats)

    # ── API: Dashboard Summary ─────────────────────────────

    @app.route("/api/dashboard/summary")
    def api_dashboard_summary():
        stats = redis_client.get_stats()
        recent_anomalies = redis_client.get_recent_anomalies(20)
        recent_alerts = redis_client.get_recent_alerts(20)
        services = redis_client.get_all_service_health()

        # Count anomalies by severity
        severity_counts = {}
        for a in recent_anomalies:
            sev = a.get("severity", "unknown")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        # Count alerts by status
        alert_status_counts = {}
        for al in recent_alerts:
            status = al.get("status", "unknown")
            alert_status_counts[status] = alert_status_counts.get(status, 0) + 1

        return jsonify({
            "stats": stats,
            "severity_counts": severity_counts,
            "alert_status_counts": alert_status_counts,
            "recent_anomalies": recent_anomalies[:10],
            "recent_alerts": recent_alerts[:10],
            "services": services,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    @app.route("/healthz")
    def healthz():
        return jsonify({"status": "ok"}), 200

    return app


def main():
    app = create_app()
    app.run(
        host=config.dashboard.host,
        port=config.dashboard.port,
        debug=not config.is_production,
    )


if __name__ == "__main__":
    main()