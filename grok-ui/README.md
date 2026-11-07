# Vector AI — Grok-style UI

`index.html` is a single self-contained file. No build step, no frameworks, no CDN.

## What is real and what is demo

Everything is real except the answers. Sending a message streams a canned reply so you can
see the interaction, the markdown rendering, the code blocks and the copy button.

To connect it to your actual AI, replace one function: `send()`. The demo reply lives in
`pickReply()` and `REPLIES` — delete both once the backend is wired.

## Wiring it to FastAPI

Replace the body of `send()` after `const body = addAI();` with a streamed fetch:

```js
const body = addAI();
let acc = '';

const res = await fetch('/api/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    messages: history,          // [{role:'user'|'assistant', content:'...'}]
    mode: mode                  // chat | code | debug | review | diagram | image
  })
});

const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = '';

while (true) {
  const { done, value } = await reader.read();
  if (done) break;

  buffer += decoder.decode(value, { stream: true });
  const lines = buffer.split('\n');
  buffer = lines.pop();                        // keep the incomplete line

  for (const line of lines) {
    if (!line.startsWith('data: ')) continue;
    const data = line.slice(6);
    if (data === '[DONE]') { done_flag = true; continue; }

    acc += data;
    body.innerHTML = '<span class="stream"></span><span class="caret"></span>';
    body.querySelector('.stream').textContent = acc;
    scrollBottom();
  }
}

body.innerHTML = render(acc);                  // markdown pass at the end
busy = false;
autogrow();
```

Server side, that expects SSE in this shape — one `data:` line per token:

```python
yield f"data: {token}\n\n"
yield "data: [DONE]\n\n"
```

## Things worth knowing

- **The markdown pass runs once, at the end.** Streaming raw text keeps partial code
  fences from rendering as broken blocks mid-stream.
- **`render()` handles** fenced code, `**bold**`, `` `inline` ``, and `- lists`. It escapes
  HTML first, so model output cannot inject markup.
- **`mode`** is set by the sidebar buttons and drives the greeting, the placeholder, and
  which system prompt the server should use.
- **History** is not persisted in this file — it is a UI. Keep the array in memory and
  send the last N turns, same as your current `HISTORY_MESSAGES` setting.

## Design tokens

All at the top of the file in `:root`. Background `#000`, composer `#101010`, borders
`#222`, text `#f7f7f7`, muted `#a6a6a6`. Change four values and the whole thing retunes.

## Note on fidelity

This matches Grok's layout language — black canvas, oversized greeting, minimal chrome,
wide rounded composer with a white send circle. It is an original build, not a copy of
their assets, fonts or code.

## Splitting it up

When this goes into the real app, the inline `<style>` becomes `src/styles/`, the inline
`<script>` becomes `src/main.js` + `src/api/client.js` + `src/ui/`, and `index.html` drops
to a shell of roughly 50 lines. That is the Milestone 1 goal in the rebuild brief.
