# OpenPPX Plugin marketplace example

This directory is a complete local marketplace and portable Plugin fixture.
Add this directory as a **local** source in **Extensions → Plugins →
Marketplaces**, refresh it, then preview and install **OpenPPX Workbench**.

The example intentionally exercises all portable Plugin component boundaries:

- `.agent-plugin/plugin.json`
- a standards-shaped `SKILL.md`
- `.mcp.json`
- `.app.json`
- an explicitly trusted synchronous lifecycle Hook
- a package asset referenced by the interface metadata

Installing the Plugin does not execute the Hook. Open its details and review the
exact command before granting Hook trust. Updating any Hook definition revokes
that trust automatically.
