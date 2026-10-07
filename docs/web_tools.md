# Web tools

A **web tool** is a function that runs in the visitor's browser — open a page, fill a field, read the cart — chosen by the voice agent
during a call (Hamsa: Tools → Web Tool). Two halves:

| | Who | Where |
|---|---|---|
| **Definition**: name, description, parameters, timeout, async, messages | you, in the console | Tools → Add New Tool → **Web Tool**; give the agent the tool like any other (its collection / a flow's tool node) |
| **Implementation**: the function | your website | `VoiceAgent.registerTools({ name: fn })` on the page that embeds the widget |

Definitions only come from the console: a page can supply implementations of tools you defined, it can't add tools to the agent.
(MCP servers and API-request tools are unchanged — a web tool is one more kind next to them.)

## On your website

```html
<script src="https://YOUR_HOST/embed.js" data-token="YOUR_SHARE_TOKEN" async></script>
<script>
  VoiceAgent.registerTools({
    navigate_to_page: async function (args) {        // args = { path: "/pricing" } — named like the tool's parameters
      location.href = args.path;
      return "Navigated to " + args.path;            // a string or a JSON object goes back to the agent
    }
  });
</script>
```

You can also set `window.VoiceAgentTools = { name: fn }` before or after the widget loads. A complete example: `docs/examples/web-tools-demo.html`.

## What happens in a call

1. The visitor starts a call from the widget (or the public link opened inside your site). The widget tells the call which tools your page registered.
2. The agent is offered a web tool **only** if the page registered it. On a phone call there is no page, so it is never offered; a flow's tool node
   for it takes its failure edge.
3. When the agent calls it, the server sends the request to the page, the widget runs your function, and the answer comes back as the tool result
   (a flow's tool node maps JSON results with `outputs`, like any tool).
4. If the function is missing, throws, answers with more than 20,000 characters, isn't plain data, or takes longer than the tool's timeout (default
   10 s), the agent gets a failure result ("the page did not answer within 10 s", the error message …).

## Safety

* Your function receives what the model decided. **Validate the arguments** like any input (see `navigate_to_page` in the demo).
* Only the widget iframe may trigger your functions: the widget checks the sending window and origin, and the page checks that requests come from its parent.
* Results are data for the model, not instructions — don't return secrets.
* Not a replacement for server-side checks: anything a visitor can do in their browser, they can do with the console open.

## Protocol (for your own client)

Public call socket `/ws/public/{token}`: the `start` message carries `"web_tools": ["navigate_to_page"]`; the server sends
`{"event": "web_tool", "id", "name", "args"}`; answer with `{"event": "web_tool_result", "id", "result"}` or `{"event": "web_tool_result", "id", "error"}`.
Between the page and the widget: `postMessage` types `hmg-web-tools?`, `hmg-web-tools`, `hmg-web-tool`, `hmg-web-tool-result`.
