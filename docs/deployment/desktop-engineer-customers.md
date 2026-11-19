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
| `AURA_ENGINEER_AGENT_ROLES` | must not contain `User` | That grant would give every account the Engineer, plan or not. |

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
  - 5 wrong pairing codes an hour per person;
  - at most 10 paired desktops per person (revoke one in Settings to add more);
  - the 10 newest app sign-ins per person (older ones are signed out).

  These limits are per worker process.
- **Downgrade.** Within about 15 minutes the desktop session stops refreshing, and within about a
  minute the paired daemon disconnects. The Engineer is also gone from every new turn. Paired
  devices stay listed, and upgrading again works without re-pairing.

## Desktop sign-in with Apple or email (WP12)

The desktop app has two ways to sign in:
- **Google**, through the app's own loopback flow. This works as before.
- **Continue in your browser**, which offers Google, Apple and email through the web app's
  Firebase sign-in. The design is in ADR-011's 2026-11-18 amendment.

The second needs:

| What | Where | Why |
|---|---|---|
| The web app served at `APP_BASE_URL` | hosting, for example `https://app.aureon-ai.tech` | The browser opens `{APP_BASE_URL}/desktop-sign-in`, and the web build must serve `index.html` for that path, as it does for `/invite`. |
| `--dart-define=APP_BASE_URL=…` in the **desktop** builds | `desktop-package.yml`, or a local build | The desktop app shows the button only when it knows where the web app is. |
| Firebase sign-in providers on, and the web app's domain in Firebase's authorized domains | Firebase console | The same setup as customer web sign-in (`docs/deployment/customer-sign-in.md`). |
| `AURA_REDIS_URL` | Railway | The one-time code may be issued by one worker and redeemed by another. Without Redis it lives in one worker's memory, and a sign-in that lands on another worker fails with "Sign-in code is invalid, expired, or already used". |

An operator still needs a **Google** identity to be recognised as an operator. An operator's
address used with Apple or email/password signs in as a customer, or is refused (SEC-08).

## Not covered

- Server-side Engineer workspaces for customers (phase 1b).
