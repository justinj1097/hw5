# AI prompts

## Setup — Campus Customs Multi-Agent Operations (assignment overview)

### Prompt

Campus Customs Multi-Agent Operations is the agentic team that runs the shop. Open tickets land on a board - customer orders, rent, unpaid bills, discount requests, whatever the shop has to handle. You will build three pieces that talk to each other: an MCP server, a FASTAPI backend with a multi-agent team, and a React dashboard so a human can watch the agents at work and approve requests.

your agent team has full connectivity - any agent may delegate tasks to any other agent for help. in this assignment you will build these agents:

* Boss - reads each ticket, decides who should work on it, and makes final calls
* Inventory - checks stock by SKU and size, spots shortfalls, and figures out which vendor can restock
* accounting - watches cash and invoices, checks margins, and prepares payments or purchase orders for human approval
* facilities - handles the shop space side (leases, rent, etc.)
* customer service - drafts messages for customers

download data.zip and unzip (i already did this, its in the downloads folder called data 5) so you have data/campus_customs.db. That file is the original shop database. Tables include desk, tickets, inventory, pricing, vendors, leases, cash_accounts, payments, and invoices

your tools will update the database as tickets get resolved. make a copy of the database file called data/campus_customs_new.db and point your MCP server and backend at that working copy. Keep campus_customs.db untouched so you can reset when you want to restart resolving the tickets

Rules of this shop:

1. the field desk.date_today is "today" for the shop. use this date to determine what is overdue
2. vendor lead times come from the vendors table
3. A vendor will not ship new product while they still have an open unpaid invoice
4. Human approval is required for any payments. Points will be deducted if this is not done. If payment is made, make sure to update the relevant table
5. If there is not enough cash, the pay tool must refuse - no negative balances
6. Cash only goes out in this homework - we did not model revenue, so no money comes in
7. before a full run to resolve the tickets, make sure to reset the database to the original values
8. do not email customers or call real vendors. drafts stay on the board

use your PORTKEY_API_KEY for AI calls. for this assignment, use only gpt-6-luna through Portkey for every agent. Multi-agent chats burn tokens fast. Points will be deducted if any other model shows up in your code or runs.

Create AI_prompts.md at the start of the assignment and keep it updated as you work. This file is the log of what you typed to your vibe coder.

Put one section for each problem. Each section must include:

1. the problem number and title
2. at least one prompt you typed, in your own words as much as possible.
3. one-follow up prompt if you needed it (and one sentence on what was lacking after the first)

### Follow-up prompt

None needed.

## Problem 1 — Understanding the database and the open tickets

### Prompt

open data/campus_customs.db and look through every table and its fields. Copy the original file to data/campus_customs_new.db - later problems update that working copy.

study the three open tickets so you see how they link to other tables

start output/harness.md

for each table, list the fields and one short line on why that table matters for the agents. you will keep growing this harness file in later problems

### Follow-up prompt

None needed.

## Problem 2 — MCP server with three ticket tools

### Prompt

Write an MCP server in mcp_server/ using FastMCP. It will talk to data/campus_customs_new.db. You do not need to connect or run it in this problem.

Every agent in this homework uses the tools in this MCP server. You will add more tools to it later; for now write three tools you know you will need for the tickets in the database. Keep the names clear. Never invent any data, only use information in the database.

In output/harness.md, list each of your three MCP tools. For every tool include: which table it reads, which ticket it helps unlock (101, 102, or 103), and one sentence on why that tool is the right one for that ticket. Vague lines like "read inventory" score low - tie the tool to the ticket.

Also add a short mcp_server/README.md file that explains what the MCP server is for, which database file it uses, and the three tools it has

### Follow-up prompt

None needed.

## Problem 3 — Connect the MCP server to the vibe coder and smoke-test it

### Prompt

In your vibe coder (Claude Code) add your MCP server to this project so the vibe coder can all the tools. Save the connection JSON text in .mcp.json at the project root (or however your vibe coder saves local MCP server lists)

With your vibe coder connected, test each of your three MCP tools. Save the evidence in output/mcp_smoke.json

For every tool include:

1. the prompt you asked the vibe coder
2. the tool name
3. the tool output (must match values in data/campus_customs_new.db)

### Follow-up prompt

Re-sent the same prompt (fixed typo "can all" → "can call").

What was lacking: the first run saved .mcp.json, but the session hadn't loaded the server yet, so I re-sent the prompt after reopening the session in HW5.

## Problem 4 — The multi-agent team

### Prompt

Build the agentic team for Campus Customs using PydanticAI: Boss, Inventory, Accounting, Facilities, and Customer Service. Include prompts, models, and agent loops so they can delegate work to each other with full connectivity.

Put the agent prompts in backend/prompts/ - one file per agent. Put data types in backend/models.py, and the agent files under backend/ (your layout can vary as long as the five roles are clear). Use your PORTKEY_API_KEY and only gpt-6-luna for every agent

write each prompt in your own words with the shop rules that agent needs. More detailed prompts that cover all aspects of an agent's work and scope score higher than a short generic prompt

add any tools the agents need to your MCP server so they can work on the open tickets. Shop facts come from the MCP server over data/campus_customs_new.db - do not invent a second shop-tools layer that bypasses MCP.

Wire the agents so they append to output/audit_trail.json as they run: for each agent-loop step, record enough to audit later. Append to this file - do not wipe it each run

in output/harness.md, list each agent and each MCP tool (including ones you add in this problem) along with which table the tool uses. Also add a short safety section: the guardrails a real business would want when agents touch real customers and real money, plus limits that keep token use in check.

update mcp_server/README.md so the tool list matches what you have now

### Follow-up prompt

None needed.

## Problem 5 — Expected plan for each desk ticket

### Prompt

before you wire the backend, write down what you expect the team to do on each open ticket. build output/desk_tickets.html - a page you can double-click with one tab per ticket (101, 102, and 103). Also add empty Cash and Reflection tabs for later problems (leave them black or with a short "coming later" note for now)

on each ticket tab, fill an Expected section only (leave room for an Actual section you will fill after you run the agents)

For each ticket's expected section, write in your own words:

1. who the boss should call first, and why
2. All the agent delegations you expect - not "Boss calls everyone"
3. which mcp tools you expect that run to use

plans that send every specialist on every ticket score low. you will compare this plan to what actually happens when your agents resolve the three tickets

### Follow-up prompt

None needed.

## Problem 6 — FastAPI backend routes for the dashboard

### Prompt

your React frontend dashboard (The next problem) needs a backend it can call. In backend/main.py, use FastAPI and add routes that do the following:

1. Return the three tickets and whether each is open or resolved
2. take a ticket id and run your agent team on that ticket
3. return recent agent events - what each agent said and which tools they used - so the board can refresh
4. approve a payment or purchase after a human clicks approve (agents only prepare the pay; this route is what actually changes cash)
5. return the current checking balance from cash_accounts
6. reset the database to the original values when you want to try a fresh run

from the backend/ folder, start the server with:

uvicorn main:app --reload --port 8000

this turns on your backend at http://localhost:8000 so the frontend dashboard can call those routes. in output/harness.md, list each route in one line (what URL / what it does)

### Follow-up prompt

None needed.

## Problem 7 — React dashboard

### Prompt

build the frontend dashboard in frontend/ with React + Vite + TypeScript. The page should call the routes you built in Problem 7. At minimum the board should:

1. List all three tickets
2. Let you pick one ticket and start the agent team on it
3. Show each agent and what they are saying / doing while the ticket runs
4. Mark a ticket resolved when the run finishes
5. Show a short summary of what each agent did on that ticket
6. Let a human approve a pay or purchase when asked
7. Show the checking balance (it should drop after an approved pay)

make the dashboard look good. be creative with the layout and feel - this is a real desk people would want to sit at, so put thought into how it looks

Tell the front end to talk to your backend at http://localhost:8000

On the backend, allow the Vite page origin (usually http://localhost:5173) so the browser is allowed to call those routes. Start the board with:

npm run dev

This opens the React desk in your browser so you can pick tickets and watch agents

write output/design.md with what you chose for the dashboard look (layout, how agents read differently, how resolved tickets and cash show up) and why - including the creative choices that make it feel special and something humans would actually enjoy using

### Follow-up prompt

None needed.

## Problem 8 — Full run over the three tickets, cash itemization, and final harness

### Prompt

Before testing the agents on a full run over the three tickets, reset the working database data/campus_customs_new.db again so you start clean. Note the starting checking balance. Then run all three tickets on the board (101, 102, 103) until each is resolved.

Open output/desk_tickets.html from Problem 6. On each ticket tab, fill the Actual section from this run: which agents worked, what they delegated, and which tools they used. Keep your Expected section so you can compare the two

On the Cash tab of the same output/desk_tickets.html, itemize the money:

1. Starting checking balance (after the reset)
2. For each ticket: how cash changed when that ticket resolved, and why (which pay / purchase, dollar amount)
3. Ending checking balance - it must match cash_accounts in the working database

Wrong cash math loses points even if the board shows every ticket resolved. Also save:

1. output/resolved_tickets.json - for each ticket: id, final status, short outcome, what each agent contributed, and any human approvals
2. output/resolved_board.html - a page you can double-click with a screenshot of your React board for each resolved ticket (101, 102, and 103)

Append real runs to output/audit_trail.json. Finish output/harness.md so it covers tables, MCP tools, the five agents, API routes, the dashboard, and safety rules

### Follow-up prompt

None needed.

## Problem 9 — Reflection

### Prompt

Open output/desk_tickets.html and fill the Reflection tab with something I will write

write this in the Reflection tab without the quotation marks at the beginning and end:

"i think the agent for ticket 101 did a really great job because it identified that there was no more size S so it quickly figured out who the vendor was and that there was an unpaid invoice blocking it so it quickly paid it and was able to order a restock for the customer. For Ticket 102, I think it did an okay job because it did what it was supposed to do but I had to reread it multiple times to understand what it was saying. For example, I wasn't sure where the $152 came from and had to do some mental math to figure it out. Also, the way some of the stuff was written out like referring to the checking account as just "Checking" was kind of confusing to me. For ticket 103, I think it started off strong, realizing there were 12 hoodies missing but it should have quickly placed an order to fill those 12 hoodies and then fulfilling the discount for the customer so that we get the full sale. It just selling the 8 on hand was also a pretty good move but I think the agent for ticket 103 could have done better. I think for ticket 101, the actual vs expected plan didnt differ too much, other than the fact that the actual plan required more read calls and sending the customer service at the same time as inventory and not after. For Ticket 102, facilities read the ticket twice and accounting pinned the board note and not facilities. Otherwise, I think the actual vs expected plan was pretty similar. Finally for ticket 103, the actual vs expected plan differed a lot since the actual plan required a managers note (a human decision) and a second run to override and remove the blocker. I think having one agent with tools might be simpler in some ways but it can also get messy very quickly because you have one agent performing all the tasks so it's very easy for it to get things confused and mixed up especially if those things have similar names. This is why even bosses have multiple managers and underlings because its not practical or feasible for the boss to do everything for a business; there's just too much to handle and keep track of. Three new problems Campus Customs might face that this agent team could solve with the tools I built are understanding the customers need very quickly, quickly checking whether we have the item or condition that the customer is looking, and putting blockers in place if things are missing. Three new problems Campus Customs might face that this agent team could not solve with the tools I built are running calls multiple times instead of just once, falsely blocking something that doesn't need to be blocked, and misreading or mislabeling certain things that could be confusing"

### Follow-up prompt

None needed.

## Problem 10 — Publish to GitHub

### Prompt

push your code to a public GitHub repository so graders can clone it. Submit the repo URL to Canvas, and also put it in output/github_url.txt

do not push your real .env to the GitHub repo. Do include both database files under data/ (the original and your working copy) so graders can run your app easily

README should explain: copy original DB to the working copy when you need a clean run, start MCP server, start FastAPI backend, start the React board, reset the DB before a full three-ticket run

### Follow-up prompt

None needed.
