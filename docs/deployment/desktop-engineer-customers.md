# Desktop Engineer for customers: setup (SaaS P1-E)

With this switched on, a customer on **Pro or Team** can run the Engineer agent on their own
computer: sign in to the desktop app, pair `aura_hostd`, and ask the Engineer to work in folders
they granted it. Free workspaces cannot. The design and threat model are in ADR-011, section
"Customers".

## Before turning it on

Startup refuses `AURA_ENGINEER_FOR_CUSTOMERS=true` together with any of these:

| Setting | Must be | Why |
|---|---|---|
| `AURA_SANDBOX_ENABLED` | `false` | Server sandbox workspaces are not open to customers yet (phase 1b). |
| `AURA_GITHUB_ENABLED` without `AURA_GITHUB_OAUTH_ENABLED` | not set | One shared operator token would be every customer's. With OAuth, each person connects their own GitHub. |
| `AURA_MCP_ENABLED` | `false` | MCP servers are the operator's. |
| `AURA_TOOLS_REQUIRE_CONFIRMATION` | `true` (default) | Per-call confirmation stays mandatory. |

The Railway target already meets all four, unless one was changed by hand.

## Turn it on

1. Set `AURA_ENGINEER_FOR_CUSTOMERS=true` on the Railway service `AURA-AI` and redeploy.
2. Check that a Pro test account can:
   - sign in to the desktop app;
   - pair `aura_hostd` (`aura_hostd pair`, then approve the code in the app);
   - run an Engineer turn that lists a granted folder.
3. Check that a Free test account gets "The desktop Engineer needs a Pro or Team plan" at
   desktop sign-in and when approving a pairing code.

## What customers get, and what limits them

- Their desktop session is always a plain `User` session. It can never open the console.
- **Rate limits:**
  - 10 desktop sign-ins and 10 pairing starts a minute per address;
  - 5 pairing approvals an hour per person;
  - at most 10 live desktop devices per person (revoke one in Settings to add more).
- **Downgrade.** Within about 15 minutes the desktop session stops refreshing, and within about a
  minute the paired daemon disconnects. The Engineer is also gone from every new turn. Paired
  devices stay listed, and upgrading again works without re-pairing.

## Not covered

- The desktop app's sign-in is Google (loopback OAuth). Apple and email sign-in on desktop come
  with the client work in Plan F.
- Server-side Engineer workspaces for customers (phase 1b).
