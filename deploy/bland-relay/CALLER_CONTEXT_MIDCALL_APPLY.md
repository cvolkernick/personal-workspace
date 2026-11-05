# Apply plan: mid-call caller context on the 6221 line

This is a Bland inbound **config change**. It is not applied by this PR.

Do not merge this PR from the apply step. Do not deploy to prism from this step. Do not call the Bland API or edit the Bland dashboard. Do not place a real call or a test call. The verification call needs Chris's OK after merge and deploy.

The relay support in this change is `POST /bland/caller-context`. `GET /bland/caller-context` stays for compatibility. Live inbound config keeps the pre-answer `dynamic_data` GET until the steps below are run on purpose.

## Line

Panamerica inbound line ending **6221**, E.164 `+19062406221`.

Today the inbound prompt runs a pre-answer `dynamic_data` GET:

`https://prism-gateway.tailb1085a.ts.net:8443/bland/caller-context?pn={{phone_number}}`

Timeout 1500 ms. Funnel latency is about 1.3–1.5 s, so the lookup times out and the greeting waits. Evidence call `19a574a1`.

## Order

1. Merge, then deploy bland-relay to prism (a separate ops step, not this document).
2. Confirm loopback `POST /bland/caller-context` returns 401 with no token and 200 with `CALLER_CONTEXT_TOKEN`.
3. Only then change the Bland inbound config, from a fresh GET of the current inbound object.

## 1. Read the live inbound object first

`GET https://api.bland.ai/v1/inbound/+19062406221` and save the full JSON.

The update must keep every field you are not changing: voice, webhook, transfer, pathway, `first_sentence`, and every tool already on the line (including Check Meeting). Paste only the differences below.

Do not POST `"tools": null`. That clears every tool.

Do not POST `"dynamic_data": null` until you have checked the array. Remove only the caller-context entry.

## 2. Remove the greeting lookup

Delete the `dynamic_data` entry whose URL contains `/bland/caller-context` (the GET with `query.pn` = `{{phone_number}}`, timeout 1500, `response_data` name `caller_context`).

If that was the only entry, set `dynamic_data` to `null`. If other entries remain, leave them.

`first_sentence` stays the greeting. It must not reference the tool and it must not wait on caller history.

The relay GET route stays up. It is no longer on the greeting path.

## 3. Add the tool

Append the object in `caller_context_tool.json` to the existing `tools` array. Do not replace the array.

Before pasting, set the Authorization header to the live secret:

`Authorization: Bearer <value of CALLER_CONTEXT_TOKEN>`

`CALLER_CONTEXT_TOKEN` is the existing prism env key in `~/.config/bland-relay/env`. It is the same secret `GET /bland/caller-context` already uses (`Authorization: Bearer` or `X-Caller-Context-Token`). It is not the Bland API key. Do not write the value into git, the prompt, or this file.

The placeholder in the JSON is the literal string `<CALLER_CONTEXT_TOKEN>`. Replace it at apply time.

The tool URL is:

`https://prism-gateway.tailb1085a.ts.net:8443/bland/caller-context`

Method POST. The body sends `call_id` and `phone_number` from the call (`{{call_id}}`, `{{phone_number}}`). The model does not supply the number. `input_schema` has no fields. Timeout is 5000 ms so a ~1.5 s lookup can finish. The greeting does not wait on this request. `speech` plays while the tool runs, which is after the greeting.

Response paths the prompt can read after the tool returns: `{{caller_context}}`, `{{known}}`, `{{last_contact_at}}`, `{{open_followups}}`, `{{caller_type}}`.

## 4. Prompt line

In the inbound prompt, after the `### PANAMERICA KB vN ###` block, replace the CALLER HISTORY sentence that treats `{{caller_context}}` as already filled before the greeting. Put this line in its place:

```text
CALLER HISTORY: Greet the caller first. After the greeting, and not before, call the caller_context tool once. If {{caller_context}} shows earlier contact, treat them as a returning caller and pick up the open item (for example, 'I see we tried to reach you this morning'). Never deny that we called. A matching number is not proof of identity: discuss booking specifics only after the caller gives a name and trip number that match. If there is no history, or the tool is slow or fails, continue normally.
```

Alexandra greets, then calls the tool. She does not call it before the greeting.

## Rollback

Restore the saved inbound JSON: put the `dynamic_data` GET back, remove the `caller_context` tool, and restore the previous CALLER HISTORY sentence. The relay GET still answers, so the old greeting lookup works again after that restore. No relay rollback is required for the config rollback.

## Verification later

No call is part of this PR. After merge, prism deploy, and this config change, one inbound call to 6221 needs Chris's OK. On that call the greeting should start without the caller-context wait, the tool should run after the greeting, and a known caller should hear history only after the greeting.
