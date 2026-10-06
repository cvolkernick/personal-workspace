# Eng backlog

A view of `cvolkernick/personal-workspace` issues. GitHub stays the source of truth. The page is served by the workflow dashboard at `/kanban/`.

`KANBAN_WRITE` defaults to off. With it unset, the page does not render drag handles and the server does not change labels. Set `KANBAN_WRITE=1` to allow a drop onto Backlog, Ready, In Progress, or Pending Review. A drop replaces `status:*` labels and leaves every other label alone. Done is not a drop target. Rank, priority, sprint, and assignees are not written.

The browser talks only to `/api/kanban` on this host. The GitHub token stays in the dashboard process.

```bash
cd projects-dashboard/eng-kanban
npm ci
npm test
npm run build
```

Restart `workflow-dashboard.service` after the build. No new unit.

Direct dependencies are MIT, Apache-2.0, ISC, or OFL-1.1. The page's only outbound host is `api.github.com`, and only from this server.
