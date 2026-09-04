# Order Supervisor

Order Supervisor is a proof of concept for supervising a long-running e-commerce order
with an AI agent. Each order gets one Temporal workflow that stays active until the order
is delivered, manually terminated, or reaches its configured maximum age.

The workflow receives order events, decides whether the agent should wake, records
simulated business actions, maintains a compact memory, and produces a final report. The
web console lets an operator configure supervisors, start runs, inspect their history,
send events and instructions, and pause, resume, or terminate a run.

## Stack

- Next.js App Router, React, Tailwind CSS
- Python 3.12 and FastAPI
- Temporal Python SDK
- PostgreSQL
- Groq or Google Gemini for model calls
- Docker Compose for local infrastructure

## Requirements

- Docker with Compose
- Make
- [uv](https://docs.astral.sh/uv/)
- Node.js 22
- npm 10

## Setup

Clone the repository and prepare the local services:

```sh
git clone https://github.com/Deep99739/Order-Supervisor.git
cd Order-Supervisor
make setup
```

Edit `backend/.env` and configure a model provider. For Groq:

```env
AGENT_MODE=live
MODEL_PROVIDER=groq
MODEL_NAME=openai/gpt-oss-120b
MODEL_API_KEY=your_key_here
```

For Gemini, use `MODEL_PROVIDER=google`, a supported Gemini model name, and the
corresponding API key. Keep credentials only in `backend/.env`; the file is ignored by
Git.

Start the application:

```sh
make start
```

Open [http://localhost:3000](http://localhost:3000). FastAPI documentation is available
at [http://127.0.0.1:8010/docs](http://127.0.0.1:8010/docs), and the Temporal UI is at
[http://localhost:8233](http://localhost:8233).

## Architecture

See [ARCHITECTURE_NOTE.md](ARCHITECTURE_NOTE.md) for the design: components, the
supervision lifecycle, what owns which decision, how memory and Temporal history are
bounded separately, and the trade-offs.

Press Ctrl-C to stop the application processes. Run `make stop` when you also want to
stop PostgreSQL and Temporal.

## Tests

```sh
cd backend && uv run pytest
cd frontend && npm run check:contracts && npm run check:display && npm run lint && npm run typecheck
```

Tests that need PostgreSQL or Temporal skip with a message rather than fail, so run
`make setup` first if you want the whole suite to execute. No test calls a model
provider; the workflow tests substitute the decision activity, so a green suite is not
evidence that a provider works.
