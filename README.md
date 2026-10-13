# routing-lab

routing-lab is a fault-injection testbench that proves LiteLLM router failover semantics. It combines a deterministic mock upstream with configurable fault modes, an ephemeral litellm-lab proxy pinned to the production router contract, and a scenario runner that produces JSON evidence reports. An interactive dashboard for browsing those reports is planned.

## Layout

| Directory | Purpose |
| --- | --- |
| `mock_upstream/` | Deterministic upstream stub with injectable fault modes. |
| `lab_config/` | Lab proxy configuration, including the pinned router contract. |
| `runner/` | Scenario runner that drives the lab and emits JSON evidence reports. |
| `deploy/` | Deployment assets, including pinned image versions. |
| `docs/` | Design notes and documentation. |
| `dashboard/` | Vite/React/TS, planned. |
| `api/` | Programmatic interface for lab control and results. |
| `prober/` | Probes for exercising router behavior. |
| `tests/` | Automated tests for lab components. |
| `reports/` | Generated JSON evidence reports. |

## Status

Scaffold

## License

MIT
