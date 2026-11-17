# Customer email: setup (SaaS P1-F1)

AURA sends its own customer email over SMTP. Sign-in verification and password-reset email stay
Firebase's.

| Email | When | To |
|---|---|---|
| Team invite | An Owner or Admin invites someone | The invitee, with a one-time link `{AURA_APP_BASE_URL}/invite?token=…` |
| Receipt | A Fawry payment is applied | The workspace's billing contact |
| Renewal reminder | The hourly cycle issues a reference-code renewal (a few days before the period ends) | The billing contact, with the Fawry reference code |
| Past due | A Fawry subscription first falls behind | The billing contact, with the date access ends |

The **billing contact** is the Owner's verified address. It is stored when they check out or start
an in-app purchase. App Store and Google Play customers get their receipts and reminders from the
store.

Email is **off** until `AURA_EMAIL_ENABLED=true`. With it off, nothing is sent and nothing fails:
an invite's token is still returned by the API, as before.

## Choose a provider

Any transactional provider with SMTP works: Resend, Amazon SES, Mailgun, Brevo, Postmark. The
spec leaves the choice to the owner (§8 #4). On the sending domain (for example `aureon-ai.tech`):

1. Add the provider's **SPF** and **DKIM** records.
2. Add a **DMARC** record (start with `p=none` and a reporting address).
3. Verify the sender address in the provider.

## Railway variables (service `AURA-AI`)

| Variable | Value |
|---|---|
| `AURA_EMAIL_ENABLED` | `true` |
| `AURA_EMAIL_FROM` | for example `AURA <billing@aureon-ai.tech>` |
| `AURA_SMTP_HOST` / `AURA_SMTP_PORT` | the provider's SMTP host, port `587` |
| `AURA_SMTP_USERNAME` | the provider's SMTP user |
| `AURA_SMTP_PASSWORD` | the provider's SMTP password or API key (a **secret**; never logged) |
| `AURA_SMTP_STARTTLS` | `true` (default) |
| `AURA_APP_BASE_URL` | where the customer app is served, for invite links |

Startup refuses `AURA_EMAIL_ENABLED` without a host, a from-address and the app URL.

## Checking it

Invite a test address to a team. The invitee should get the link, and the API still returns the
token. A failed send appears in the audit log as `email.failed`, with the kind of email and the
error type, never its content. An invite email carries a single-use token.

## Not built yet

- Arabic versions of the email (they come with the app's localisation work).
- Receipts as Egyptian e-invoices (ETA). That is an owner/accountant question (§8 #5).
