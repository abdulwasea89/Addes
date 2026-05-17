# Agno — Practical Guide (Gemini + Groq)

> Everything you need to use [Agno](https://docs.agno.com) for building agents
> that talk to **Google Gemini** and **Groq (Llama)**, with the patterns we use
> in this project. Read top-to-bottom once; after that this is reference.

Agno is a Python framework for building, running, and managing multi-agent AI
systems. Where the OpenAI/Gemini SDKs give you a single `generate_content` call,
Agno gives you:

- **Agents** — an LLM + instructions + tools + structured output, all in one
  object.
- **Teams** — multiple agents coordinated by an orchestrator agent.
- **Workflows** — deterministic step pipelines (Step A → Step B → Step C) where
  any step can be an agent or a team.
- **Memory / Knowledge / DB** — built-in session memory and RAG over vector
  stores, with optional Postgres/SQLite persistence.
- **Structured output** — return a Pydantic model directly from `agent.run()`.
- **Tools** — Python functions decorated with `@tool` that the model can call.
- **Streaming** — sync + async streaming for every primitive.

---

## 1. Install

```bash
uv add agno
# Model SDKs (install only what you use)
uv add google-genai groq
# Optional extras
uv add ddgs sqlalchemy pgvector pypdf      # knowledge bases
uv add psycopg                              # Postgres persistence
```

Set keys (you already have these in `.env`):

```bash
GEMINI_API_KEY=...
GROQ_API_KEY=...
```

Agno's model wrappers read these env vars automatically.

---

## 2. Hello, Agent

The smallest useful program:

```python
from agno.agent import Agent
from agno.models.google import Gemini

agent = Agent(
    model=Gemini(id="gemini-2.5-flash"),
    instructions="You are a concise technical writer. Reply in two sentences max.",
)

agent.print_response("Explain Postgres MVCC to a junior engineer.")
```

Swap providers without changing anything else:

```python
from agno.models.groq import Groq

agent = Agent(model=Groq(id="llama-3.3-70b-versatile"), ...)
```

That swap is the whole point of Agno's model layer — same `Agent`, different
`model=`.

---

## 3. Running an Agent

Three call styles. Pick by what you need:

| Method | Returns | Use when |
|---|---|---|
| `agent.print_response(prompt)` | prints to stdout | quick scripts, debugging |
| `response = agent.run(prompt)` | `RunOutput` object | you want the data |
| `async response = await agent.arun(prompt)` | `RunOutput` (async) | inside FastAPI, asyncio |

`RunOutput` fields you'll actually use:

```python
response = agent.run("What is 2+2?")
response.content          # the text (or your Pydantic model if output_schema)
response.messages         # full conversation history
response.metrics.duration # seconds
response.metrics.total_tokens
response.is_paused        # True if a tool needs human confirmation
```

### Streaming

```python
# Sync
for chunk in agent.run("...", stream=True):
    print(chunk.content, end="", flush=True)

# Async
async for chunk in await agent.arun("...", stream=True):
    print(chunk.content, end="", flush=True)
```

---

## 4. Structured Output (the killer feature)

Define a Pydantic model. Pass it as `output_schema=`. The agent returns an
instance of that model — no JSON parsing, no schema-validation glue.

```python
from pydantic import BaseModel, Field
from agno.agent import Agent
from agno.models.google import Gemini

class BrandProfile(BaseModel):
    brand_name: str | None = None
    tagline: str | None = None
    key_benefits: list[str] = Field(default_factory=list)
    industry: str | None = None
    colors: list[str] = Field(default_factory=list)

cleaner = Agent(
    model=Gemini(id="gemini-2.5-flash"),
    instructions=(
        "Extract a structured brand profile from raw scraped HTML/markdown. "
        "Use null/[] for unknown fields. Never fabricate."
    ),
    output_schema=BrandProfile,
)

response = cleaner.run(scraped_markdown)
profile: BrandProfile = response.content
print(profile.brand_name, profile.tagline)
```

Under the hood Agno tells Gemini `response_mime_type="application/json"` +
`response_json_schema=<schema>`. With Groq it falls back to JSON mode +
client-side Pydantic validation. Same `output_schema=` argument, both work.

**Strict vs guided mode** (provider-dependent — Groq, Anthropic):

```python
Agent(model=Groq(id="..."), output_schema=BrandProfile, strict_output=False)  # guided
```

Strict mode rejects extra fields and wrong types at the SDK boundary; guided
mode validates after the fact. Default is strict where supported.

---

## 5. Tools

Any Python function can become a tool the model can call. Two ways:

### 5.1 Decorator

```python
from agno.tools import tool

@tool
def get_weather(city: str, unit: str = "celsius") -> str:
    """Get current weather for a city.

    Args:
        city: City name, e.g. "Tokyo".
        unit: "celsius" or "fahrenheit".
    """
    return f"22°{unit} in {city}, sunny"

agent = Agent(model=Gemini(id="gemini-2.5-flash"), tools=[get_weather])
agent.print_response("What's the weather in Tokyo?")
```

The docstring + type hints become the tool schema the model sees. **Write good
docstrings.** They're prompts.

### 5.2 Manual `Function` for strict tool use

```python
from agno.tools import Function

weather_tool = Function(
    name="get_weather",
    description="Get current weather for a city.",
    parameters={
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
        },
        "required": ["city"],
        "additionalProperties": False,
    },
    strict=True,
    entrypoint=get_weather,
)
```

Use this when the auto-generated schema isn't tight enough.

### 5.3 Human-in-the-loop

```python
@tool(requires_confirmation=True)
def deploy_to_production(app_name: str, version: str) -> str:
    ...

response = await agent.arun("Deploy v2.1")
if response.is_paused:
    for req in response.requirements:
        if req.needs_confirmation:
            print(f"Tool: {req.tool_execution.tool_name}")
            req.confirm()  # or req.reject()
    response = await agent.acontinue_run(response)
```

### 5.4 Built-in tool packages

```python
from agno.tools.hackernews import HackerNewsTools
from agno.tools.yfinance import YFinanceTools
from agno.tools.duckduckgo import DuckDuckGoTools
# ...dozens more — see docs.agno.com/tools

Agent(model=..., tools=[HackerNewsTools(), YFinanceTools()])
```

Each `*Tools()` class exposes a curated set of methods. Restrict with
`include_tools=`:

```python
YFinanceTools(include_tools=["get_current_stock_price", "get_company_news"])
```

---

## 6. Teams (multi-agent orchestration)

A `Team` is an agent that delegates to other agents.

```python
from agno.team import Team
from agno.agent import Agent
from agno.models.google import Gemini
from agno.tools.duckduckgo import DuckDuckGoTools

researcher = Agent(
    name="Researcher",
    role="Find recent facts on a topic with citations.",
    model=Gemini(id="gemini-2.5-flash"),
    tools=[DuckDuckGoTools()],
)

writer = Agent(
    name="Writer",
    role="Turn research notes into a 200-word brief.",
    model=Gemini(id="gemini-2.5-flash"),
)

team = Team(
    name="Brief Team",
    model=Gemini(id="gemini-2.5-flash"),   # the orchestrator
    members=[researcher, writer],
    instructions="Hand off research findings to the Writer for the final brief.",
    show_members_responses=True,            # print each member's output
    markdown=True,
)

team.print_response("Write a brief on the state of Llama 3.3 vs Gemini 2.5.")
```

Teams can stream:

```python
async for chunk in await team.arun("...", stream=True):
    print(chunk.content, end="")
```

Teams also support `output_schema=` for structured final output.

---

## 7. Workflows (deterministic pipelines)

When you don't want the LLM to decide the order, use `Workflow`:

```python
from agno.workflow import Step, Workflow
from agno.db.sqlite import SqliteDb

workflow = Workflow(
    name="Ad pipeline",
    db=SqliteDb(db_file="tmp/workflow.db"),   # persists session state
    steps=[
        Step(name="Scrape",  agent=scrape_agent),
        Step(name="Clean",   agent=clean_agent),
        Step(name="Outline", agent=outline_agent),
        Step(name="Prompt",  agent=prompt_agent),
    ],
)

response = workflow.run(input="https://linear.app")
print(response.content)

# Per-step metrics:
for name, step in response.metrics.steps.items():
    print(f"{name}: {step.metrics.duration:.2f}s / {step.metrics.total_tokens} tok")
```

This is the right tool when you have a fixed pipeline (like our
scrape → clean → outline → prompt flow).

---

## 8. Memory & Knowledge

### 8.1 Session history (chat memory)

```python
from agno.db.postgres import PostgresDb

db = PostgresDb(db_url="postgresql+psycopg://ai:ai@localhost:5532/ai")

agent = Agent(
    model=Gemini(id="gemini-2.5-flash"),
    db=db,
    add_history_to_context=True,   # the agent will see prior turns
)
```

Use SQLite for local, Postgres for prod.

### 8.2 Knowledge (RAG)

```python
from agno.knowledge.knowledge import Knowledge
from agno.vectordb.pgvector import PgVector

knowledge = Knowledge(
    vector_db=PgVector(table_name="recipes", db_url=db_url),
)
knowledge.insert(url="https://example.com/recipes.pdf")

agent = Agent(
    model=Groq(id="llama-3.3-70b-versatile"),
    knowledge=knowledge,
)
agent.print_response("How to make Thai curry?", markdown=True)
```

Other vector DBs Agno ships with: LanceDB, Pinecone, Qdrant, Weaviate, Chroma,
Mongo Atlas, etc. Same API, different `vector_db=`.

---

## 9. Model Wrappers — Gemini & Groq Cheat Sheet

### Gemini (`agno.models.google.Gemini`)

```python
from agno.models.google import Gemini

Gemini(
    id="gemini-2.5-flash",          # or "gemini-2.5-pro", "gemini-2.0-flash-001"
    api_key=None,                    # default: env GEMINI_API_KEY / GOOGLE_API_KEY
    temperature=0.4,
    top_p=None,
    top_k=None,
    max_output_tokens=None,
    response_mime_type=None,         # Agent sets this for output_schema
    response_schema=None,
    system_instruction=None,         # usually set via Agent(instructions=)
    safety_settings=None,
    generation_config=None,
)
```

Models worth knowing:
- `gemini-2.5-flash` — default; fast, cheap, structured output ✓
- `gemini-2.5-pro` — slower, smarter
- `gemini-2.0-flash-001` — stable older release

### Groq (`agno.models.groq.Groq`)

```python
from agno.models.groq import Groq

Groq(
    id="llama-3.3-70b-versatile",   # default
    api_key=None,                    # default: env GROQ_API_KEY
    temperature=0.4,
    max_tokens=None,
    top_p=None,
    response_format=None,            # Agent sets {"type": "json_object"} for schemas
    seed=None,
)
```

Models worth knowing:
- `llama-3.3-70b-versatile` — best quality, our default
- `llama-3.1-8b-instant` — small, very fast
- `deepseek-r1-distill-llama-70b-specdec` — reasoning model (`/think`-style)
- `mixtral-8x7b-32768` — wide context, older

### One-line provider swap

```python
def make_agent(provider: str):
    if provider == "gemini":
        return Agent(model=Gemini(id="gemini-2.5-flash"), output_schema=BrandProfile)
    if provider == "groq":
        return Agent(model=Groq(id="llama-3.3-70b-versatile"), output_schema=BrandProfile)
    raise ValueError(provider)
```

---

## 10. Project Patterns We Use

### 10.1 The "service" pattern (matches `services/gemini.py`)

If you don't want the full `Agent` machinery, you can call the model directly:

```python
from agno.models.google import Gemini

model = Gemini(id="gemini-2.5-flash", api_key=settings.gemini_api_key.get_secret_value())
text = await model.aresponse([{"role": "user", "content": "..."}])
```

But for anything beyond a single shot, use `Agent`.

### 10.2 Async inside FastAPI

```python
@router.post("/clean")
async def clean(body: CleanRequest):
    response = await cleaner_agent.arun(json.dumps(body.raw_content))
    return response.content    # already a BrandProfile if output_schema is set
```

### 10.3 Catch errors cleanly

```python
try:
    response = await agent.arun(payload)
except Exception as exc:
    # Agno wraps provider errors. Inspect exc.__cause__ for the underlying SDK error.
    raise HTTPException(502, detail=f"Agent failed: {exc}") from exc
```

---

## 11. Debugging Tips

- `agent.print_response(..., stream=True, show_full_reasoning=True)` — prints
  every tool call + thought.
- `RunOutput.messages` — full message list, useful for replaying.
- `RunOutput.metrics` — duration, token counts per step.
- Set `Agent(debug_mode=True)` for verbose internal logs.
- For workflows: `Workflow(monitoring=True)` to push traces to AgentOS.

---

## 12. When to use what

| Need | Use |
|---|---|
| One LLM call, structured output | `Agent` + `output_schema` |
| Multi-step, model decides order | `Team` |
| Multi-step, fixed order | `Workflow` |
| Persistent chat memory | `Agent(db=PostgresDb(...))` |
| RAG over documents | `Agent(knowledge=Knowledge(...))` |
| Tool calls with confirmation | `@tool(requires_confirmation=True)` |
| Multiple providers as fallback | hand-roll a try/except chain over two `Agent`s |

---

## 13. Sources

- Official docs: <https://docs.agno.com>
- GitHub: <https://github.com/agno-agi/agno>
- Models index: <https://docs.agno.com/models/providers>
- Cookbook: <https://docs.agno.com/examples>
