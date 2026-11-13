# Conversation handling – design

**Status: draft for review (2026-11-13).** Part 1 (the "handled" state) is built; everything marked *planned* is a proposal to agree on before it's coded. Once agreed, the decisions go into [PLAN.md](PLAN.md) and the built flows into [CODE_EXPLANATION.md](CODE_EXPLANATION.md).

The question this answers: what happens between a trainee's "yanek …" and the bot's reply, for every kind of conversation that can happen, and what happens when a step fails.

## Why a design

Today one case works well, a report ("yanek waga 80kg"). The others work only as far as the prompt carries them, and the reply flow is spread over three files (`main.reply()`, `conversation.py`, `assistant.py`). The review of 2026-11-13 found reports that were saved twice or lost (`refactor_ideas.md`, 1.1), a weekend's catch-up that can only save one day (1.2), and an answer to the bot's own question that gets no reply unless the trainee starts it with "yanek" (case 5 below).

## Principles

1. **The LLM understands, the code decides.** The model reads the messages and says what's in them: a weight, a training, a correction, a question, something unclear. The code decides what that means for the database: what's saved, what's changed, what keeps waiting. Language is too varied for rules in code ("sorki, zapomniałem, waga 80" is a report, not a correction), and database writes are too important to leave to the model.
2. **"Handled" is stored, not guessed.** Each message from a trainee records when the bot dealt with it (`messages.handled_at`). A reply reads every message that is still waiting. *Built 2026-11-13.*
3. **Every step has a fallback, and no fallback loses a message.** The worst case is an apology with the messages still waiting, so the next "yanek" reads them again.
4. **One LLM call per "yanek"** in the normal case. A second call only for a retry after invalid output, or after another reply took the messages meanwhile.
5. **No state machine.** The state of the conversation is the waiting messages plus the history. The model handles the back-and-forth; the code doesn't model dialogue states like "waiting for the unit".

## The pieces

### Waiting and handled messages (built)

Every message from a trainee starts **waiting** (`handled_at` null). It becomes **handled** when:

- a reply has saved what it reported, in the same database transaction as the entries; or
- a fixed text answered it: the shared phone number, `/start`.

It stays waiting when the LLM call or the save fails (the trainee gets the apology), and, *planned*, when the bot asked a clarifying question and saved nothing (case 4).

### The turn (built)

A **turn** is everything one reply works with (`conversation.Turn`):

| Part | What it is |
|---|---|
| `trigger` | the "yanek …" message that started the reply |
| `trainee` | with their current plan |
| `new` | from the first waiting message to the end of the history, including the bot's messages in between (an apology, the help) |
| `context` | the handled messages before that: what the new ones refer to |
| *planned:* `saved` | the trainee's saved entries of the last 7 days, each with a short ID (`w12`, `t40`), for corrections (case 3) |

The model gets `context`, then the marker "--- NOWE WIADOMOŚCI ---", then `new`, and extracts data only from `new`.

### What the model answers

Today: `reply`, `weight` (one or null) and `training` (one or null). *Planned* (milestone 4):

```json
{
  "reply": "Zapisałem 80 kg na dziś. Jutro masz dzień B.",
  "intent": "report",
  "weights": [{"kg": 80.0, "date": "2026-11-13"}],
  "trainings": [],
  "corrections": [],
  "needs_answer": false
}
```

- **`intent`**: the main thing the trainee wanted: `report`, `correction`, `answer`, `plan_question`, `diet`, `other`. It's used for the logs, for statistics ("how many messages were questions"), and to cross-check the answer (`report` with nothing extracted is suspicious and gets logged). It does not decide what's saved.
- **What's saved follows the content, not the intent.** "yanek waga 80, a co mam jutro?" is a report *and* a question in one message. One intent can't say both, but `weights` and `reply` can. So saving is driven by the filled fields: `weights` and `trainings` are saved, `corrections` applied, and `reply` always sent.
- **`needs_answer`**: true when the model asked a question about the report and saved nothing from it. The messages then keep waiting, and the trainee's answer is read together with them (cases 4 and 5).

## The flow

The handler is one class, `ConversationHandler` (*planned*, `src/wabot/conversation_handler.py`). It takes over what `main.reply()` does today and adds the planned cases. One call per "yanek" message, run in the background after the webhook has answered Telegram or Meta:

```
ConversationHandler.handle(message_id)
  1. covered?    the message is handled already, or the trainee wrote "yanek" again since
                 (that reply reads this one too) → stop, no LLM call                      built
  2. load_turn() context + new (+ planned: saved entries of the last days)               built
  3. understand() the LLM → the answer above, checked by validate_output()
       invalid → one retry with the errors                                                built
       still invalid, or the LLM is down → the fast path (case 1)                         planned
       nothing works → no answer
  4. decide      from the answer: entries to save, corrections to apply, the reply text,
                 whether the messages keep waiting (needs_answer)                         planned
  5. commit      ONE transaction: mark the waiting messages handled (unless they keep
                 waiting) + save + apply corrections                                      built
       another reply took some messages meanwhile → back to 1, once                       built
       no answer, or the save fails → nothing saved, the APOLOGY, messages keep waiting   built
  6. send        back on the channel the message came from; a failure is logged only
                 (what was saved stays saved and handled)                                 built
```

Each step is a method, so a test can replace one (a fake LLM, a fake sender) without patching modules. The class gets its collaborators when it's created, `ConversationHandler(settings, sessions, llm, sender)`, which is also what Cloud Tasks will need later: a task carries the message ID, and the handler rebuilds everything from the database.

## The cases

Each case has a test with a fixed model answer, and the real-model ones a scenario in `scenarios/*.yaml`. "Today" is the code as of 2026-11-13.

### 1. A simple report

> **Kasia:** yanek waga 80,4
> **Yanek:** Zapisałem 80,4 kg na dziś.

- **Model:** `intent: report`, `weights: [80.4, today]`.
- **Code:** saves the weight, marks the message handled, sends the reply.
- **Fallback (planned): the fast path.** When the LLM is down or its answer is invalid twice, a regular expression recognises the simplest reports ("yanek waga 80,4", "yanek 80.4 kg"): a weight between 30 and 300, today's date. It's saved, and the trainee gets a fixed reply ("Zapisałem 80,4 kg na dziś."). Anything else gets the apology and keeps waiting.
- **Today:** works; no fast path, so an OpenAI outage means an apology for every message.

### 2. A report spread over several messages

> **Kasia:** waga 80,4
> **Kasia:** przysiady 4x8 60kg
> **Kasia:** yanek zapisz
> **Yanek:** Zapisałem 80,4 kg i przysiady 4×8 z 60 kg.

- **Code:** the first two messages wait until "yanek zapisz"; all three are new in its turn and handled together.
- **Today:** works (since 2026-11-13; before, a `/start` in between lost the first one).

### 3. A correction

> **Kasia:** yanek waga 80
> **Yanek:** Zapisałem 80 kg na dziś.
> **Kasia:** yanek sorki, 81

or a correction of something saved days ago:

> **Kasia:** yanek dzień A zrobiony, przysiad 60 kg
> **Yanek:** Zapisałem dzień A…
> *(two days later)*
> **Kasia:** yanek we wtorek przysiad był na 65, nie 60

- **Today:** the first works: the model returns 81 and the newest row of the day counts (80 stays as history). For a training, the model returns the whole day again, copied from the history, with the change. That only works while the earlier report is in the history (24 h / 20 messages). Two days later it isn't, so the "corrected" day has just the squat.
- **Planned:** the turn includes the saved entries of the last 7 days with their IDs. The model answers `intent: correction`, `corrections: [{entry: "t40", exercise: "back_squat", weight_kg: 65}]`, and the code changes that entry: a new row with the change, the old one kept, as for any report.
- **Fallback:** an ID that isn't one of this trainee's entries (or isn't in the list) → nothing is changed, and the bot asks which report it was.

### 4. An unclear report: the bot asks

> **Kasia:** yanek trening zrobiony
> **Yanek:** Super! Który to był dzień – A czy B?

- **Model:** `needs_answer: true`, nothing extracted.
- **Planned:** nothing is saved and the message keeps waiting, so the answer (case 5) is read together with it.
- **Today:** the message is marked handled; the answer can still work, but only because the model reads the question from the context.

### 5. The answer to the bot's question

> **Yanek:** Który to był dzień – A czy B?
> **Kasia:** A

- **The problem today:** "A" doesn't mention "yanek", so the bot doesn't answer at all. The trainee has to know to write "yanek A".
- **Planned: an open question.** When a reply has `needs_answer`, the bot's message is stored as a question (`messages.expects_answer`). The next message from the trainee, within 2 hours, is answered even without "yanek". Its turn has "trening zrobiony" (still waiting) and "A" as new, and the model saves day A.
- **Fallback:** the model still isn't sure → it asks once more; after the second question it saves only what's certain (e.g. the training without a day) and says so.

### 6. Several days at once

> **Kasia:** yanek w sobotę zrobiłam A, w niedzielę B, a dziś waga 79,8
> **Yanek:** Zapisałem niedzielę (B) i wagę 79,8 kg. — *(today only one training can be returned, so Saturday is lost)*

- **Planned:** `weights` and `trainings` are lists; each item is checked and saved as now.
- **Today:** one weight and one training per reply; the model drops one day or (worse) says it saved both.

### 7. A question about the plan

> **Kasia:** yanek co mam jutro?
> **Yanek:** Jutro (środa) masz dzień B: …

- **Model:** `intent: plan_question`, nothing extracted. **Code:** nothing to save; the message is handled.
- **Today:** works.

### 8. A reply to a reminder

> **Yanek (reminder, 21:00):** Jak dziś z dietą?
> **Kasia:** dobrze, bez odstępstw

- The reminder is context, and the model sees what's being answered. *Planned* with milestone 5 (reminders): a reminder is stored as a question (`expects_answer`), so the answer needs no "yanek" (as in case 5).

### 9. A diet report

> **Kasia:** yanek dziś wieczorem pizza 😬
> **Yanek:** Dzięki, zanotowane – trener to zobaczy.

- **Today:** acknowledged, nothing saved (`intent: diet`). *Milestone 4:* `diet_checkins`.

### 10. Off-topic, or a request for advice

> **Kasia:** yanek mogę brać kreatynę?
> **Yanek:** To pytanie do Twojego trenera – nie doradzam w sprawie suplementów.

- **Model:** `intent: other`, nothing extracted. A refusal from the model gets the fixed `REFUSAL` text. **Today:** works.

### 11. A photo or a voice message

> **Kasia:** *(photo of the scale)* yanek 80,4

- **Built:** a caption is the message's text, so this is case 1.
- **Planned:** a voice message from a trainee, or a photo without a caption that follows a "yanek" in the last few minutes, gets a fixed reply ("Na razie czytam tylko tekst – napisz proszę raport.") and is handled. Voice messages get it even without "yanek", since they can't contain it as text.

### 12. A retry after a failure

> **Kasia:** yanek waga 80
> **Yanek:** Przepraszam, mam chwilowy problem. Twoje wiadomości mam – napisz „yanek” za kilka minut, a wtedy je zapiszę.
> **Kasia:** yanek spróbuj jeszcze raz
> **Yanek:** Zapisałem 80 kg na dziś.

- **Built:** the failed reply left "yanek waga 80" waiting, so the second turn reads it again.

### 13. Two "yanek" messages in quick succession

> **Kasia:** yanek 80
> **Kasia:** yanek sorki, 81 *(before the first reply is out)*
> **Yanek:** Zapisałem 81 kg.

- **Built:** the first message is covered by the second one's reply: one reply, one entry (81), and no LLM call for the first.

## What we don't build

- **A dialogue state machine.** Waiting messages, the history and `needs_answer` cover what states would.
- **A table of conversations or sessions.** Messages with `handled_at` (and, planned, `expects_answer`) are enough.
- **Rules in code that guess the case** from words ("sorki", "?"), except the fast path (case 1), which is only a fallback.

## Order of work

| Step | What | Cases | When |
|---|---|---|---|
| 1 | `handled_at`, the turn, the apology keeps messages waiting | 2, 12, 13 | ✅ 2026-11-13 |
| 2 | `ConversationHandler` class: today's flow moved into it, no change in behaviour | – | before milestone 4 |
| 3 | `needs_answer` + `expects_answer`: questions keep the report waiting, answers need no "yanek" | 4, 5, 8 | milestone 4 |
| 4 | Lists of weights and trainings | 6 | milestone 4 |
| 5 | Saved entries in the turn, corrections by ID | 3 | milestone 4 |
| 6 | `intent` in the answer, logged | all | with 3 |
| 7 | The fast path | 1 | any time |
| 8 | Fixed reply for voice and captionless photos | 11 | any time |
| 9 | Diet entries | 9 | milestone 4 |

## Decisions to make

1. **`intent`:** do we want it (logs, statistics, cross-checks), or only the content fields?
2. **Answers without "yanek" (case 5):** after a bot's question, the next message within **2 hours** is answered without "yanek"? Another window?
3. **The fast path (case 1):** only as a fallback (as proposed), or also in front of the LLM for the simplest reports (cheaper, but the reply is always the fixed text)?
4. **Corrections (case 3):** how far back: the last **7 days**?
5. **Voice messages (case 11):** the fixed reply, or transcription later (one more OpenAI call per voice message)?
