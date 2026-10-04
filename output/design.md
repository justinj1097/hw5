# Dashboard design: Campus Customs "Back Office"

`frontend/` is a React + Vite + TypeScript app that talks to the FastAPI backend at `http://localhost:8000`. Start it with `npm run dev` and open http://localhost:5173.

## The idea: an after-hours desk

I wanted it to feel like sitting at the shop's back-office desk after closing. The room is dark, and a neon sign glows pink in the corner. The things a manager actually handles are on paper: ticket stubs on a spike, cheques in a tray, the register tape. The agents work the floor in the middle.

Everything digital (the agents, the live feed) is neon on black. Everything that's a real-world commitment (tickets, cheques, money, letters) is printed on cream paper. You can tell at a glance what's agent chatter and what's real.

It uses the project's default **black-and-pink** palette:
- **Background:** near-black `#121016`, with a slowly drifting pink glow.
- **Accent:** hot pink `#ff3d9a`, used for anything that needs attention.
- **Paper:** cream `#fbf3ea`, used for any real-world object.
- **Type:** a serif (Iowan Old Style / Palatino) for headings and paper items, so they read like print; a sans-serif for the interface; a monospace font for SKUs, tool calls and the register tape.

## Layout: three columns, read left to right like a shift

| Left: **the ticket spike** | Centre: **the floor** | Right: **the money** |
| --- | --- | --- |
| The three tickets as paper stubs on a glowing spike | The selected ticket, a map of the agents, and three tabs: *Live floor*, *Shift report*, *Board* | **Checking** (the register) above the **Approval tray** (cheques) |

You pick a ticket on the left, watch the team work it in the middle, and sign for anything that costs money on the right. Both side columns stay pinned while the feed scrolls, so the balance and pending cheques are always visible. On smaller screens the money column drops below the stage, and on phones everything stacks into one column.

## How the five agents read differently

Each agent has its own colour, a symbol, and a fixed seat on the floor map. The symbol and name are always shown with the colour, so nothing relies on colour alone.

| Agent | Symbol | Colour | Why |
| --- | --- | --- | --- |
| Boss | ♛ | hot pink | Sits at the top of the map; it's in charge |
| Inventory | ▦ | light pink | A stock shelf |
| Accounting | $ | gold | Money |
| Facilities | ⌂ | lilac | The building |
| Customer Service | ✉ | mint | Letters to customers |

- **Floor map.** The Boss sits at the top and the four specialists along the bottom. All ten possible connections are drawn faintly, because any agent can delegate to any other. When a hand-off happens, that line lights up in the delegating agent's colour and animates while the helper works. After the run you can see the shape of the teamwork: on ticket 101, Boss → Inventory → Accounting and Boss → Customer Service light up, and Facilities stays grey.
- **Each agent's live state.** Idle agents are dimmed and labelled "Off the clock". A working agent's avatar grows, glows, and pulses, with a one-line caption of what it's doing in plain English ("Checked the cash position", "Waiting on Accounting"). Finished agents get a green check.
- **Live floor (the feed).** It reads like a group chat:
  - each line is tagged with the agent's symbol and colour, and indented by delegation depth, with a "via Boss → Inventory" note, so nested hand-offs read like a conversation inside a conversation;
  - what an agent *says* appears as a speech bubble;
  - **tool calls** are small dashed chips with a plain-English verb ("priced a restock") next to the real call, `estimate_restock(vendor_id=1, …)`; clicking a chip shows the raw MCP result;
  - **hand-offs** are their own lines (Boss ⟶ Inventory) with the exact brief that was passed along;
  - each agent's final summary is a highlighted bubble;
  - a typing indicator shows while the run is live.
- **Shift report.** When a run finishes, the board switches to this tab:
  - the **Boss's call** sits at the top on a cream paper card, with a "For you" list of next steps and the facts it relied on;
  - below it is one card per agent that worked, with its summary, the tools it used as pills ("checked stock ×2"), and who it handed off to;
  - a stats strip shows model calls, tool calls, hand-offs and tokens, plus who **stayed on the bench**. That makes it easy to check the team didn't send everyone on every ticket, which was my Problem 5 goal.

## Resolved tickets

- Each ticket is a perforated stub with a big ticket number. Its status chip changes colour: grey *Open*, pink *In progress*, yellow **Waiting on you**, red *Blocked*, green *Resolved*. A pink badge says how many cheques it needs signed ("2 to sign").
- While the team works a ticket, its stub pulses pink.
- When a ticket resolves, a pink **RESOLVED rubber stamp** slams onto the stub and onto the ticket header. It's animated with a little overshoot, like a real stamp hitting paper. A toast confirms it, and the run button changes to "Resolved".
- A ticket only resolves when the work is really done. The Boss resolves it when nothing waits on a person. If the Boss leaves it on *waiting on approval*, it resolves when you sign the last cheque. That keeps the green stamp honest: no ticket shows "resolved" while rent is still unpaid.

## Cash

- **The register.** The checking balance is the biggest number on the page. When a payment clears, it **counts down** to the new value rather than jumping, the panel flashes pink and gives a small shake, and the bar below it fills further.
- **The bar** shows three things at once: money already paid out (pink), money waiting on approval (yellow stripes), and what's left. Below it are three lines: paid out, waiting on approval, and left if everything is approved. If the pending cheques add up to more than checking holds, a warning says you'll have to choose, and the desk refuses to overdraw.
- **Register tape.** Every payment is printed on a cream receipt with a torn, zig-zag bottom edge: amount, date, and **who signed it**. The opening balance sits at the bottom. Cash only goes out in this shop, so the tape only ever gets longer.

## Approvals: cheques you sign

- Each request the agents prepared shows up as a pink **cheque**. It has the cheque number, "Pay to the order of *Bulldog Print Co*", the amount in a box, a memo explaining why, and "Prepared by $ Accounting" in that agent's colour.
- You type your name once in **Signing as**; the app remembers it. Approve buttons stay disabled until there's a name, because every payment records who approved it.
- **Approve & pay** writes your name onto the signature line in handwriting, stamps the cheque **PAID** in green, and then the register counts down. **Reject** stamps it **VOID**.
- The cheque warns you *before* you click:
  - "Checking only has $X. The desk will refuse this."
  - "Vendor still has unpaid invoice 501; approve that first."

  If you try anyway, the backend's refusal appears in red on the cheque. I tested this by approving the $8 purchase order before invoice 501: it was refused under rule 3, and the reason appeared on the cheque.

## Small touches

- **The Board tab** shows customer drafts as **envelopes** with an airmail stripe and a "DRAFT · NOT SENT" mark, so nobody mistakes them for sent mail. Below them are the ticket's money requests and a timeline of every note agents and people pinned.
- The ticket stubs on the spike are tilted a degree or two, so the rail looks like real paper. They straighten when you hover.
- The top bar shows the **shop date** (`desk.date_today`, "Monday, August 31, 2026"). That's the date every agent is told to use, so it matters more than the real clock. There's also a live connection light ("Live · gpt-6-luna").
- **Reset shop** asks you to confirm first, and is disabled while a run is going.
- All the motion stops for people who turn on "reduce motion" in their system settings.

## Why this should make people want to use it

- **It's honest.** Paper means real, neon means agent work, and money only moves when a person signs. The design matches how the system actually works.
- **It shows what happened, not just the result.** The live map and feed let you watch the team think, and the shift report summarises it in 30 seconds. That's how you come to trust agents.
- **It makes the important moment feel weighty.** Signing a cheque, seeing the stamp, and watching the balance count down make approving a payment feel like a real decision, not a casual click.
