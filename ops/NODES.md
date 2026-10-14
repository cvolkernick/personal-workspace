---
title: "Nodes (public overview)"
tags: [ops, pi, nodes]
status: active
created: 2026-10-14
---

# Nodes

This is a public, high-level overview. Operational detail is kept off-repo on
the prod node and moves to the private repo when it splits (#1159). Don't add
ports, unit names, paths, addresses, usernames or backup gaps here: CI
(`ops/check_nodes_md.py`) fails if you do. The same file must exist
byte-for-byte on `master` and `work/treasury`.

| | prism-gateway | finley-gateway |
|---|---|---|
| Role | Production: dashboards, schedulers and agents | Backup and vault-primary (target): pulls prod state on a schedule |
| Hardware | Raspberry Pi 4 | Raspberry Pi 4 |
| Health | Heartbeat with host vitals and threshold alerts | Same heartbeat |

## prism-gateway

Production node. Runs the personal dashboards, the scheduler and the agent
runtime. Reachable only on the private tailnet, except one public endpoint
exposed through Tailscale Funnel.

## finley-gateway

Backup node. Pulls a copy of production state on a schedule, and is the
intended primary for the knowledge vault, with a replica planned.

## Topology

The Horizon `/swarm` page draws its diagram from this section. On the prod node
it prefers a private, untracked copy of this file with full detail, when one is present.
Format: `- id | label | detail`, and `- from -> to | label` for flows.

### Planned and external

- pi3-relay | Pi 3 (planned, #109) | vault replica + relay
- r2 | Cloudflare R2 (planned) | off-site backup
- vercel | Vercel | public frontends
- internet | Public internet | one Funnel endpoint

### Flows

- prism-gateway -> finley-gateway | scheduled state pull
- finley-gateway -> pi3-relay | vault replica (planned)
- finley-gateway -> r2 | off-site copy (planned)
- prism-gateway -> finley-gateway | heartbeat poll
- internet -> prism-gateway | Tailscale Funnel
- vercel -> prism-gateway | public frontends call APIs
