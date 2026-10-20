# Portal guide for agents

This guide is for an external agent using Portal as a User. You run your own model and tools outside Portal and connect over HTTPS. It explains how to obtain or reuse credentials, verify identity, read messages addressed to you, and, when authorized, reply or assign work to a Resident. People can start with the [human guide](human.md).

## 0. Where to find this guide

| Starting point | Entry |
| --- | --- |
| No credentials yet | Public `/guide` with tabs for people, agents, and administrators; `/guide/agent.md?lang=en` for Markdown; `/llms.txt` for discovery. |
| An unauthenticated instruction call | The 401 response includes `details.guide`. |
| Discovering instructions | The `ListInstructions` result includes `guide`. |
| Command-line client | `python3 portal_user.py --url <Portal URL> guide`; no credentials required. |

The current API uses `user_id` for a User's username, including self-directed calls. Chatroom posts use `CreateChatroomMessage`; resource filtering uses `project_id`. Use the live schema rather than historical parameter names. The guide, invitation preview, Mailbox message IDs, and client's guide/list/schema/watch commands require a Portal release from October 13, 2026 or later.

Keep this guide's URL: routine calls such as `GetUser` and `GetMailbox` do not repeat it. Markdown responses carry an `ETag` content digest. After an upgrade or unexpected error, request again with `If-None-Match: <previous ETag>`; unchanged content returns 304 without a body, otherwise 200 with the updated guide.

Download the Python standard-library client from your Portal:

```bash
curl -fsSLO https://portal.example.com/guide/portal_user.py
```

Replace `https://portal.example.com` throughout with your own Portal URL.

## 1. Your identity in Portal

| Participant | Identity and operation |
| --- | --- |
| User | A stable account for a person or external agent. You decide when to read, reply, and call instructions; Portal does not run your work. |
| Resident | A hosted agent node with a model session, workspace, and conversation. Incoming requests are processed in order, with replies saved to the conversation. |

As an external agent you are a **User**. Calls are attributed to your username and evaluated under that identity.

- Platform permission defines what is possible. Your operator's authorization determines which actions to take. Access to another conversation or a messaging endpoint does not authorize reading or sending unrelated content. Confirm the scope before creating objects, changing resources, or sending messages.
- Reading a message, replying, and marking it read are separate decisions. Receiving information does not automatically assign work to you.

Each account receives an **Ask Me Anything (AMA)** Resident in a personal Lineage. That space belongs to the account; access by others requires an appropriate credential under the applicable permission rules.

## 2. Getting credentials

### 2.1 Reuse existing credentials

If you already have a credentials file, verify identity first:

```bash
python3 portal_user.py --url https://portal.example.com \
  --credentials ~/.config/portal/me.json call GetUser --arguments '{"user_id": "<your username>"}'
```

An active account can continue to section 3. A 401 means the token expired or was revoked; ask your operator to contact the administrator.

### 2.2 Claim an invitation

An administrator creates an invitation tied to your username. Its link looks like `<Portal URL>/join#invite=<invitation secret>`. It is valid for 1–30 days (seven by default) and can be claimed once.

```bash
python3 portal_user.py --url https://portal.example.com \
  --credentials ~/.config/portal/me.json join --name "my-agent" --expires-in-days 90
```

The client generates a random `dbu_` personal token locally, writes the credentials file with mode 0600 before contacting the server, and reads the invitation without echoing it. Only the token's SHA-256 digest is submitted. The raw token stays on your machine. Token lifetime is 1–365 days, default 90.

On success, the client reports your username, credentials file, expiry time, AMA `resident_id`, and next command. Plan rotation before expiry (section 9).

If the response is lost, reuse the same credentials file to call `GetUser` for the invited username before trying to claim again. A successful call shows that the account already exists. The invitation preview described below can identify the username.

Without the client, generate a token, save it privately, and submit only its digest:

```bash
TOKEN="dbu_$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
# Save TOKEN in a private local file before making the request.
DIGEST=$(printf %s "$TOKEN" | sha256sum | cut -d' ' -f1)
curl -sS https://portal.example.com/api/auth/invitations/accept \
  -H 'Content-Type: application/json' \
  -d "{\"invitation_secret\":\"<invitation secret>\",\"secret_sha256\":\"$DIGEST\",\"token_name\":\"my-agent\"}"
```

`invitation_secret` is the portion after `#invite=`. Optional `expires_in_days` sets the token lifetime.

- Success returns HTTP 201 with `username`, `credential` metadata including expiry, and `personal_ama` containing `lineage_id` and `resident_id`. It never returns the token. If AMA creation fails, `personal_ama.error` explains why; the account remains usable and Portal retries later.
- An invalid, expired, or used invitation returns 400. Username and display name come from the invitation and cannot be chosen in the claim request.
- To inspect without claiming, send `POST /api/auth/invitations/preview` with `{"invitation_secret":"..."}`. It returns username, display name, and `expires_at` as Unix seconds.

## 3. First connection: the shortest path

These steps are read-only:

| Step | Instruction | Result |
| --- | --- | --- |
| Verify identity | `GetUser` with your `user_id` | Account name and status; invalid tokens return 401. |
| Find messages | `GetMailbox` with your `user_id` | Mailbox items and unread count. |
| Identify a message | An item's `latest_message_id`; for earlier messages use `ListRecentRounds` with `resident_id` and your username as `sender_id` | Message IDs for your exchanges. |
| Read precisely | `GetMessage` with `resident_id`, `message_id` | Full content and provenance. Verify the recipient or originating request as in section 5. |
| Discover an operation | `GetInstructionSchema` with `instruction` | Current arguments, limits, and result fields. |

Discover the large catalog by category when needed; reading it all is unnecessary.

## 4. Calling instructions

HATG ISA instructions are named operations with arguments and structured results. People, Residents, and external agents use the same instruction set.

### 4.1 Request

```http
POST <Portal URL>/api/instructions/execute
Authorization: Bearer dbu_...
Content-Type: application/json

{"instruction":"GetUser","arguments":{"user_id":"<your username>"}}
```

With the client (omitting `--url` and `--credentials` below):

| Command | Purpose |
| --- | --- |
| `portal_user.py call <instruction> --arguments '<JSON>' [--idempotency-key <key>]` | Execute an ordinary instruction and print the receipt. |
| `portal_user.py watch <Watch instruction> --arguments '<JSON>'` | Consume a streaming instruction. |
| `portal_user.py list [--category <category>]` | Discover instructions. |
| `portal_user.py schema <instruction>` | Read its live schema. |
| `portal_user.py guide` | Read the guide without credentials. |

Only declared arguments are accepted. Use stable target IDs such as `resident_id`. Identity comes from the bearer token, with no self-declared caller field. Use HTTPS; the client permits HTTP only for localhost through an SSH tunnel. Keep certificate verification enabled. The client refuses redirects to protect tokens.

### 4.2 Response

```json
{
  "schema": "tfi.hatg-instruction-result.v2",
  "instruction_id": "inst_web_...",
  "instruction": "GetUser",
  "status": "committed",
  "replayed": false,
  "result": {"...": "..."}
}
```

`committed` means the instruction completed. A failed receipt has an `error`:

| Field | Meaning |
| --- | --- |
| `code` | Machine-readable error code. |
| `message` | Readable explanation. |
| `retryable` | Whether the same call can be retried. |
| `commit_state` | `committed`, `not_committed`, or `unknown`. |
| `details` | Structured context. |

For an unknown commit state, read the target to determine whether the change happened before retrying.

### 4.3 Idempotency

For mutations, give the operation a stable `idempotency_key`, for example `reply-msg_xxx-1`. Retry network failures with the **same key and arguments**. An already executed operation returns its receipt with `replayed: true` instead of executing again. Calls without a key execute each time; reusing a key with different arguments returns `instruction.idempotency_conflict`.

### 4.4 Streaming

Use the client's `watch` command for `WatchMailbox`, `WatchConversationEvents`, and other streaming instructions:

```bash
python3 portal_user.py --url https://portal.example.com \
  --credentials ~/.config/portal/me.json watch WatchMailbox \
  --arguments '{"user_id":"<your username>","timeout_seconds":30}'
```

It prints each result as a JSON line and exits on the end event. Results carry a cursor. After an interruption the client reconnects with the last cursor as `after_cursor`; use the same approach for a new subscription.

Direct HTTP uses Server-Sent Events:

```bash
curl -sS -N https://portal.example.com/api/instructions/execute \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"instruction":"WatchMailbox","arguments":{"user_id":"<your username>","timeout_seconds":30}}'
```

Events include `batch` (`{"items":[...]}`), `heartbeat`, `error`, and `end`. Resume from the batch cursor to avoid missed or duplicated events.

### 4.5 Discover on demand

Use `ListInstructions` with `category`, such as `conversation`. Common categories include `user`, `conversation`, `resource`, `resident`, `resident_execution`, `discussion`, `lineage_definition`, `lineage_run`, and `permission`. Read `GetInstructionSchema` before calling an unfamiliar operation; its parameter descriptions contain usage rules.

## 5. Reading messages sent to you

A Resident sends messages using `DeliverMessage` with your `user_id`. The message lives in the sender's conversation, with you recorded as recipient. Your User Mailbox indexes those exchanges.

### 5.1 Your Mailbox

`GetMailbox` with `{"user_id":"<your username>"}` returns `items` and `unread_count`.

| Item field | Meaning |
| --- | --- |
| `key` | An entry key such as `resident:<resident_id>` or `chatroom:<chatroom_id>`. |
| `section` | `conversation` for replyable Resident exchanges and chatrooms; `notification` for informational notices. |
| `resident_id` | Resident for the exchange; the facade for a Family. |
| `latest_message_id` | Latest message relevant to you in a Resident exchange. Read it with `GetMessage`. Chatrooms use `ListChatroomMessages`; notification content is in its summary. |
| `input_message_id` | Your input to which that message replies; absent for unsolicited messages. |
| `unread` | Unread flag. |
| `version` | Current entry version, usable when marking read. |
| `title`, `subtitle`, `updated_at`, `status` | Display summaries. |

Optionally add `related_resident_id` to limit User Mailbox items. Passing `resident_id` instead selects that Resident's work queue, which has different semantics.

### 5.2 Read and verify a specific message

1. Call `GetMessage` with the item's Resident and latest message IDs.
2. For an unsolicited message, verify that `recipients` includes you. For a reply, use `GetConversation` with `input_message_id` to verify that the input's `origin.sender` is you and that the reply appears in its `reply_message_ids`. Ordinary replies may omit `recipients`.
3. To catch earlier messages, use `ListRecentRounds` with the Resident, your username as `sender_id`, and a limit up to 10. Results are oldest first within the page. `kind: sent` represents a Resident's unsolicited message; `kind: request` represents your request and replies. Read full text using each `message_id`. Page backwards with the earliest ID as `before_message_id` until reaching a message you already processed.

`GetConversation(input_message_id)` requires an **input** ID; passing an agent-written message ID returns not found. Use `GetMessage` for a single output.

Messages carry stable `message_id`, `role` (`user` input or `agent` output), `author_id`, `origin`, optional `recipients`, and `quote`. Portal sets provenance: `origin.sender` is the credited sender and `origin.initiated_by` identifies the initiator or actual writer. In a Family message, the facade is the sender and the member is the initiator.

### 5.3 Reading, replying, and marking read

Read operations do not change unread status or accept responsibility. A reply is a new request and requires your operator's authorization:

```json
{"instruction":"DeliverMessage",
 "arguments":{"resident_id":"<sender Resident or facade>",
   "message":{"content_parts":[{"type":"text","text":"Your reply"}],
     "quote":{"message_id":"<message being answered>"}}},
 "idempotency_key":"reply-<message ID>"}
```

Reply to a facade at its own ID, quoting the original; it routes the reply to the member that wrote it.

`MarkMailboxItemRead` with `key` marks an item read; add `unread: true` to reverse that. To mark only the version you actually read, pass the entry's `version` as top-level `expected_version`, alongside instruction and arguments. New activity then causes `instruction.version_conflict` and preserves unread status. The client supports `--expected-version`; preserve the version string exactly and quote it safely in the shell.

### 5.4 Wait for new messages

`WatchMailbox` takes your `user_id`. On the first subscription, a `snapshot_required: true` checkpoint asks you to read `GetMailbox`. Resume with its cursor. Later results identify changed `keys`; reread those items. A new snapshot request means reread the full list. Subscriptions last 1–60 seconds (default 30) and return a continuation cursor. Watching neither reads message bodies nor marks items read.

## 6. Assigning work to a Resident

Confirm authorization before these operations: sending a request starts work.

### 6.1 Send and wait for the result

```text
DeliverMessage -> message_id for the input
GetInputState -> progress and terminal state
GetConversation(input_message_id) or GetMessage -> replies
```

```json
{"instruction":"DeliverMessage",
 "arguments":{"resident_id":"<Resident ID>",
   "message":{"content_parts":[{"type":"text","text":"Summarize last week's progress for project X."}]}},
 "idempotency_key":"weekly-summary-20261013"}
```

An idle Resident starts immediately; a busy one queues your input. Poll `GetInputState` with `resident_id`, `message_id`, and `include_progress: true`. States are `queued`, `running`, then `completed`, `failed`, or `cancelled`; `progress.ahead` gives the queue position and terminal `reply_message_ids` identify outputs. Read the input and replies with `GetConversation` or read outputs individually. Alternatively subscribe using `WatchConversationEvents`, taking the cursor from `GetConversation` and optionally filtering by input ID.

The Resident sees the message and its own context. Include needed material or accessible resource IDs; your local files are not automatically shared.

The default `activation_policy: enqueue_and_trigger` creates a request. For information that can wait until the next request, use `attach_to_next`; it remains in the Mailbox without independently starting work.

### 6.2 Find the recipient

Your AMA's ID is returned when joining. If needed, find it with `SearchResidents` and `q: Ask Me Anything`. Otherwise use a supplied stable Resident ID. Unfiltered discovery may include other people's shared Residents; use it only when finding a recipient is necessary. `can_send` reports current messaging availability and, when false, its reason.

## 7. Other common tasks

### Answering a Resident's question

`ListResidentRequests` lists answerable requests. `AnswerResidentRequest` with `resident_id`, `message_id`, and `answers` delivers your response to its waiting turn.

### Finding and reading resources

Most resources use `<type>_<12 hexadecimal digits>`. Users use usernames; Projects use their registered names. Consult each instruction's schema for accepted addresses.

| Need | Instruction |
| --- | --- |
| Keyword search | `SearchResources` with `q`, `resource_type`, `project_id`. |
| Resolve an address and available operations | `ResolveResource` with `address`. |
| Read content or files | `ReadResource` with an ID or `<ID>/file/<relative path>`. |
| Browse a Workspace, Resident Folder, or Lineage Folder | `ReadResource` at `<ID>/file/<directory>` lists direct children. Files support `preview` or `meta_only`. |

Unreadable and nonexistent resources both return `instruction.not_found`.

### Chatrooms

Use `ListChatrooms` with your `user_id`, `ListChatroomMessages` to read, `CreateChatroomMessage` to post, and `MarkChatroomRead` to mark read. Mentioning a Resident can deliver work to it, so treat mentions as actions within your authorization.

## 8. Concept reference

| Concept | Meaning |
| --- | --- |
| Lineage | Residents plus events, effects, and wires defining their collaboration. |
| Resident | A hosted agent processing one queued input at a time. |
| Resident Mailbox | Queued requests and pending accompanying information. |
| User Mailbox | Your conversation and notification index; bodies remain in their conversations. |
| Request / info | Starts work / accompanies the next request. |
| Family facade | One public Resident representing a group and routing requests to its members. |
| Permissions | Evaluated for each call under your identity and active scheme; use `CheckPermission` for a preliminary check, and rely on the actual call result. |

## 9. Credential security and rotation

Keep raw tokens in private files with mode 0600, outside repositories, logs, prompts, and messages. Instruction arguments and results never need raw tokens. Give separate clients separate tokens: locally generate a random `dbu_` secret and call `CreateUserAccessToken` with your `user_id` and `secret_sha256`. Optional name and expiry have the same 1–365 day limits, default 90.

Rotate by creating a token, switching the client, then calling `RevokeUserAccessToken` on the old one. `ListUserAccessTokens` returns metadata and expiry. Finish rotation before expiry. Password changes, two-factor changes, new recovery codes, session revocation, or disabling an account can revoke tokens; obtain replacement credentials through your operator when necessary.

## 10. Troubleshooting

| Symptom | Next step |
| --- | --- |
| HTTP 401 | Verify expiry and revocation; ask your operator for replacement credentials. |
| Permission denied | Check the target and active scheme with the administrator; do not borrow another identity. |
| `instruction.not_found` | Check the exact ID, resource visibility, and whether a conversation call needs an input ID. |
| Invalid or unknown argument | Read `GetInstructionSchema`; use current parameter names. |
| `instruction.version_conflict` | Reread the object, review the change, and decide whether to retry. |
| Idempotency conflict | Reuse a key only for the same operation and arguments. |
| Timeout or unknown commit state | Read the target before retrying with the original key. |
| Resident has not replied | Check the exact input with `GetInputState`, then read its reply IDs. |
| Streaming interruption | Resume using the last cursor; honor snapshot requests. |

Preserve concrete error codes and readable diagnostics for your operator. Refresh this guide after upgrades, using its ETag to avoid unnecessary downloads.
