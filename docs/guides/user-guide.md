# User Guide

What AURA can do for you, and how to get good answers out of it.

---

## What it is

AURA is a personal AI assistant that works from **your** documents, remembers what you tell it,
and can use tools on your behalf. Three things make it different from a general chatbot:

- **It cites its sources.** When an answer comes from your documents, you see which document and
  which section.
- **It says when it does not know.** If your documents do not cover something, AURA says so
  instead of inventing a plausible answer.
- **It remembers deliberately.** Facts you state are stored with a record of where they came
  from, and you can see and delete them.

## Asking questions

Just ask. AURA searches your documents automatically.

> **You:** How many days of annual leave do I get?
>
> **AURA:** 21 days per year for permanent staff. Requests need at least 14 days' notice
> through the HR portal.
> *Source: Employee Handbook (Leave > Annual Leave)*

The citation is the important part. If an answer has no citation, it did not come from your
documents — treat it as general knowledge and verify anything that matters.

### When AURA says it does not know

> **You:** What is our policy on sabbaticals?
>
> **AURA:** I don't have anything on sabbaticals in your documents. The handbook covers annual
> leave and sick leave only.

This is working correctly. An assistant that produces a confident answer with nothing behind it
is worse than one that admits the gap — and it tells you exactly what to upload next.

### Getting better answers

- **Include distinctive terms.** Names, ticket numbers, product names, and error codes are
  matched exactly, so `AURA-4419` finds that ticket rather than something merely similar.
- **Ask one thing at a time.** Multi-part questions get multi-part answers, and the parts get
  shorter.
- **Say what you want back.** "in a table", "one paragraph", "with the exact figures" all work.
- **Correct it.** If an answer is wrong, say so. Corrections are the highest-value feedback the
  system receives.

## Documents

Upload anything text-based: policies, notes, contracts, meeting minutes, documentation.

**Versioning.** Re-uploading a changed document creates a new version and retires the old one,
so you never get an answer mixing the current policy with last year's. Re-uploading an
*unchanged* document does nothing.

**Privacy.** Your documents are visible only to you. AURA never mixes one person's documents
into another's answers.

**If a document is rejected:** AURA refuses to index documents containing text that tries to
give it instructions — a known way to manipulate AI assistants. You will see a note that the
document was quarantined. If it is a legitimate document, tell your administrator.

## Memory

Tell AURA something about yourself and it remembers:

> **You:** I'm based in the Cairo office and I prefer short, bulleted answers.
>
> **AURA:** Noted.

Later, in a new conversation, it applies both without being reminded.

**What gets remembered:** things you state about yourself that will still be true next month —
preferences, your role, ongoing projects. Not passing context ("I'm tired today"), not
questions, and nothing it merely inferred.

**Seeing and removing memories.** Everything AURA remembers is visible, with where it came from,
and any of it can be deleted. Nothing is stored that you cannot inspect.

**Corrections.** If you say something that contradicts an earlier fact, the newer one takes
precedence. The old one is marked superseded rather than erased, so a mistaken correction can
be undone.

## Tools

AURA can take actions — calculations, checking the date, searching your documents, and whatever
else your administrator has enabled.

**It asks before acting.** Anything that changes something outside AURA — sending a message,
creating a record, exporting a file — needs your confirmation first. You will see exactly what
it proposes to do.

**Calculations are computed, not guessed.** Ask for arithmetic and AURA runs it rather than
predicting the answer.

## Conversations

AURA remembers the current conversation, so follow-ups work naturally:

> **You:** How much leave do I get?
> **AURA:** 21 days per year. *(Employee Handbook > Leave > Annual Leave)*
> **You:** And if I don't use it?
> **AURA:** Unused days expire on 31 December. *(same source)*

Long conversations are summarised as they grow — the details stay, the small talk does not.
Start a new conversation when you change topic; it keeps answers focused.

## Feedback

Rating an answer takes a second and genuinely matters:

- **Good answers** confirm what is working.
- **Bad answers**, especially with a correction, are the strongest signal available. Corrections
  are weighted above ratings when the system prepares improvements.

Nothing is changed automatically based on feedback: a person reviews and approves any change to
how AURA behaves.

## Privacy

- Your documents, memories, and conversations are yours. AURA does not mix them with anyone
  else's.
- Answers can be kept entirely on your organisation's own hardware — ask your administrator
  whether local-only mode is enabled.
- Passwords, API keys, and similar credentials are blocked from appearing in answers.
- Personal details like emails and phone numbers are removed from answers where they were not
  part of your own question.
- Diagnostic logs record *that* a question was asked, never *what* was asked.

## Limits

Worth knowing:

- **It can be wrong.** Citations tell you where an answer came from; verify anything
  consequential against the source.
- **It only knows what it has been given.** No document, no answer.
- **It is not a professional adviser.** Finance, health, and legal questions get careful,
  bounded answers and a recommendation to consult someone qualified — deliberately.
- **Long documents are chunked.** A question needing information scattered across fifty pages
  may only find part of it. Narrowing the question helps.

## Getting help

If AURA is not answering well:

1. Check whether the answer had a **citation**. No citation means it was not from your documents.
2. Try adding a distinctive term — a name, a code, a number.
3. Check the document is uploaded, and that its latest version is.
4. Rate the answer and add a correction. That is what improves it.
