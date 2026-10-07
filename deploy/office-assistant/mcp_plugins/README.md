# MCP plugins (snapshot of the live Lab)

These files mirror `/var/lib/hermes-office-gateway/mcp_plugins/` inside the
`office-office-gateway-1` volume on the Lab VM. Agents install them with the
gateway's `add_mcp_tool` (approval required); the gateway loads them on start.

This directory is a **backup and review copy**, not the live location: shipping the
tree does not install or update plugins. To bring a plugin back after a lost volume,
copy the file into the volume (or propose it again through the gateway).

When an agent adds or changes a plugin on the Lab, copy it here and commit it so the
repo stays in sync. `*.py.bak` files (made by the gateway on replace) are not tracked.
