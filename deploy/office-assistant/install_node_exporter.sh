#!/bin/bash
set -e

VERSION="1.9.1"
TARBALL="/tmp/node_exporter-${VERSION}.linux-amd64.tar.gz"
INSTALL_DIR="/opt/node_exporter"
SERVICE_USER="nodeexporter"

echo "=== Node Exporter v${VERSION} Installer ==="

if [ ! -f "$TARBALL" ]; then
    echo "ERROR: $TARBALL not found."
    echo "Copy the tarball to /tmp/ first:"
    echo "  scp node_exporter-${VERSION}.linux-amd64.tar.gz root@$(hostname):/tmp/"
    exit 1
fi

if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    echo "[+] Created user: $SERVICE_USER"
fi

mkdir -p "$INSTALL_DIR"
tar -xzf "$TARBALL" -C /tmp/
cp "/tmp/node_exporter-${VERSION}.linux-amd64/node_exporter" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/node_exporter"
chown "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR/node_exporter"
rm -rf "/tmp/node_exporter-${VERSION}.linux-amd64"
echo "[+] Installed binary: $INSTALL_DIR/node_exporter"

cat > /etc/systemd/system/node_exporter.service << 'EOF'
[Unit]
Description=Node Exporter
Wants=network-online.target
After=network-online.target

[Service]
User=nodeexporter
Group=nodeexporter
Type=simple
ExecStart=/opt/node_exporter/node_exporter \
    --collector.systemd \
    --collector.processes \
    --collector.filesystem.mount-points-exclude="^/(sys|proc|dev|host|etc)(\$|/)" \
    --web.listen-address=:9100

Restart=on-failure
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable node_exporter
systemctl start node_exporter

echo ""
echo "=== Verifying ==="
sleep 2
if systemctl is-active --quiet node_exporter; then
    echo "[OK] node_exporter is RUNNING"
    curl -s http://localhost:9100/metrics | head -5
    echo ""
    echo "[OK] Metrics endpoint responding on :9100"
else
    echo "[FAIL] node_exporter failed to start"
    journalctl -u node_exporter --no-pager -n 20
    exit 1
fi

echo ""
echo "=== Done ==="
echo "Host: $(hostname) ($(hostname -I | awk '{print $1}'))"
echo "Port: 9100"