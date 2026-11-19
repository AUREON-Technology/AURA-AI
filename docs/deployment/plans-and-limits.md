# Plans and limits (SaaS P1-C)

Every workspace has exactly one plan: Free, Pro or Team. The server enforces each plan's limits:
- chat messages per month;
- the Engineer agent;
- knowledge storage;
- team seats.

Plans follow the workspace, not the person. A Pro user inside someone's team gets that team's
limits there.

## Changing the numbers (no code change)

The built-in numbers are placeholders until prices are set (spec §8.1):

| Plan | Messages / month | Engineer | Storage | Seats | Workspaces |
|---|---|---|---|---|---|
| free | 50 | no | 25 MiB | 1–3 | personal, team |
| pro | 1500 | yes | 1 GiB | 1 | personal |
| team | 1500 per seat, pooled | yes | 5 GiB | 2–50 | team |

To change them, set `AURA_PLAN_CATALOGUE` on the Railway `AURA-AI` service to JSON of the same
shape and redeploy. It must define `free`. A malformed catalogue stops the service at start with a
`ConfigurationError` naming the problem.

```json
{
  "free": {"messages_per_month": 50, "messages_pooled_per_seat": false, "engineer": false,
           "storage_bytes": 26214400, "min_seats": 1, "max_seats": 3, "workspace_kinds": ["personal", "team"]},
  "pro":  {"messages_per_month": 1500, "messages_pooled_per_seat": false, "engineer": true,
           "storage_bytes": 1073741824, "min_seats": 1, "max_seats": 1, "workspace_kinds": ["personal"]},
  "team": {"messages_per_month": 1500, "messages_pooled_per_seat": true, "engineer": true,
           "storage_bytes": 5368709120, "min_seats": 2, "max_seats": 50, "workspace_kinds": ["team"]}
}
```

## Giving a customer a plan by hand (until billing, Plan D)

```bash
curl -X PUT https://api.aureon-ai.tech/api/v3/admin/workspaces/<workspace_id>/plan \
  -H "Authorization: Bearer <a SuperAdmin access token>" -H "Content-Type: application/json" \
  -d '{"plan": "team", "seats": 5}'
```

- The call needs a SuperAdmin and is audited (`plan.set`).
- It refuses a plan that doesn't fit the workspace (Pro on a team, Team on a personal workspace),
  and seats outside the plan's range.
- It takes effect on the workspace's next request.
- Members can see their plan and usage at `GET /api/v3/workspaces/<id>/plan`.

## What counts

- **Messages.**
  - One unit per chat turn on `/chat` or `/chat/stream`, counted per UTC calendar month.
  - A turn that fails, or a stream that ends without its `done` frame, is given back.
  - Live voice turns are not metered yet.
- **Engineer.** A turn with `agent: "engineer"` needs a plan that includes it.
- **Storage.**
  - The stored text of each knowledge document. Re-uploading identical content adds nothing.
    Deleting a document gives its bytes back.
  - Not monthly; the count only changes when documents are added or deleted.
- **Seats.** Members plus pending invites. Lowering seats below the current member count keeps
  everyone, but refuses new invites.
- **Unmetered.** Operators (platform role Developer or above) and service keys.

## What a refusal looks like

The status is HTTP **402**:

```json
{"error": "plan_limit", "message": "this workspace has used its messages for the month", "retryable": false,
 "details": {"limit": "messages", "plan": "free", "used": 50, "allowed": 50, "resets_at": "2026-12-01T00:00:00+00:00"}}
```

`limit` is one of `messages`, `engineer`, `storage` or `seats`. The app words each one: when it
resets, which plans include it, how to make room.
