"""Tool specs waiting for a human to approve them. Approval does not ship code."""

from __future__ import annotations

ARCHIVE_DASHBOARD_TOOL = "propose_archive_dashboard"

ARCHIVE_DASHBOARD_REASON = (
    "Dead import — no WarehousePG/EDW instance running, no metrics scraped, all panels empty."
)

ARCHIVE_DASHBOARD_UIDS = (
    {"uid": "cluster_dashboard_edb", "title": "Cluster Dashboard"},
    {"uid": "warehousepg_cluster_details_edb", "title": "Cluster Details"},
    {"uid": "warehousepg_cluster_log_details_edb", "title": "Log Details"},
    {"uid": "warehousepg_query_details_edb", "title": "Query Details"},
    {"uid": "warehousepg_dashboard_recomendations_edb", "title": "Recommendations"},
    {"uid": "warehousepg_resource_group_details", "title": "Resource Group Details"},
    {"uid": "warehousepg_segmentdetails_edb", "title": "Segment Details"},
)


def archive_dashboard_spec() -> dict:
    return {
        "name": ARCHIVE_DASHBOARD_TOOL,
        "description": "Propose archiving an existing Grafana dashboard. Requires human approval before execution.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Dashboard UID to archive"},
                "reason": {"type": "string", "description": "Why this dashboard is being archived"},
                "hard_delete": {
                    "type": "boolean",
                    "description": "False moves the dashboard to folder _archived. True calls DELETE and is irreversible.",
                    "default": False,
                },
            },
            "required": ["uid", "reason"],
        },
        "behavior": {
            "validate": "GET /api/dashboards/uid/{uid}; 404 is not_found",
            "action_type": "archive_dashboard",
            "payload": ["uid", "title", "folder", "reason", "hard_delete"],
            "soft_archive_folder": "_archived",
            "hard_delete_endpoint": "DELETE /api/dashboards/uid/{uid}",
            "approval_url": "https://10.216.4.80/approvals/",
        },
        "dashboards": list(ARCHIVE_DASHBOARD_UIDS),
        "reason": ARCHIVE_DASHBOARD_REASON,
        "out_of_scope": ["bulk archive", "folder-level archive", "restore"],
    }
