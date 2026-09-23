# Marufi Digital guide site

This project contains a static website for digital guides organized by country and state. Product checkout happens on Stan. The public site files are in `dist/`.

## Add real guides

Edit `dist/guides.json`. Set `stanUrl` to your Stan storefront URL and add each published product to `guides`:

```json
{
  "storeName": "Marufi Digital",
  "stanUrl": "https://stan.store/your-store",
  "guides": [
    {
      "country": "Example country",
      "state": "Example state",
      "title": "Example guide title",
      "description": "A short description of what the buyer gets.",
      "stanUrl": "https://stan.store/your-store/p/example-product"
    }
  ]
}
```

Replace the example values with real products; the example is not included in the live catalog. The site only displays guides with a title, description, country, state, and HTTPS `stan.store` product link. Open the site through an HTTP server when previewing locally; opening `index.html` as a file prevents the catalog from loading. For example, run `python3 -m http.server 8765 -d dist` and visit `http://localhost:8765/`.

## Let Codex and Claude work together

In a normal Terminal tab, run:

```bash
cd /Users/alimarufi/Documents/marufidigital-web
./collab.sh "Describe the project goal you want completed"
```

Codex creates a plan of up to five tasks. For each task, Codex implements, Claude reviews and fixes, and Codex verifies. When a task is complete, the runner starts the next one. It stops when all tasks are done, an agent needs your input, a command fails, or an individual agent call takes longer than one hour. The wrapper keeps a Mac awake while it runs.

Use `./collab.sh --max-tasks 8 "..."` for a larger objective, or `./collab.sh --resume --note "Here is the missing information"` after addressing a stopped run. Each run's plan, handoffs, logs, and status are saved in `.ai-handoff/`. The runner permits only one active run in this project at a time.

The runner launches new non-interactive Codex and Claude sessions. It does not type into the two interactive chats you already have open. Avoid editing the same project in those chats while the runner is active.

The agents can edit this project, but the runner does not give Codex user-configured browser/desktop tools or network access, and Claude has file tools only. Changes from each completed task are committed locally; nothing is pushed or deployed. A new run requires a clean Git working tree. Review the result before publishing. The Claude review phase has a $10 per-call budget cap; reaching it stops the run. This automation uses your existing CLI sign-ins and their usage limits. Run one real objective while you are present before relying on the unattended watcher.

### Keep a queue running while you are away

Leave this command running in a normal Terminal tab on the Mac Mini:

```bash
./collab.sh --watch
```

Add a new project objective from another normal Terminal tab:

```bash
./collab.sh --queue "Add the real Stan guide listings and check the catalog"
```

The watcher picks up queued objectives in order. Each objective gets its own Codex plan and the same Codex → Claude → Codex phases. Finished tasks move to `automation/completed/`; tasks that need attention move to `automation/paused/`. If an objective pauses or fails, the watcher stops rather than starting the next one on a half-edited project. Logs and detailed handoffs remain in `.ai-handoff/`. Press Ctrl+C in the watcher tab to stop it. The watcher does not invent new objectives after the queued work is finished; it waits for another task.

Use `./collab.sh --status` to see whether the watcher is running, or `./collab.sh --stop-watch` to ask it to stop after its current objective. Keep the Terminal tab open while you are away. macOS does not allow a login service to read this project from Documents without extra privacy access, so the watcher is intentionally run from Terminal.
