---
name: feature
description: Implement a new feature in this project. Automatically handles Supabase schema changes (new tables, columns, indexes) if the feature requires them.
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Edit, Write, Bash(python *), Bash(curl *), Bash(cat *), Bash(echo *)
---

# Feature Implementation Skill

You are implementing a new feature for the **Multi-Agent Debate System**.

## Feature Request
$ARGUMENTS

---

## Step 1 — Understand the existing schema

The project uses Supabase with these known tables:
- `user_credits` — columns: `user_id`, `balance_paise`
- `query_analytics` — columns: `user_id`, `prompt`, `actual_cost_inr`, `profit_inr`, `total_input_tokens`, `total_output_tokens`
- `conversations` — columns: `user_id`, (conversation history)

Auth is Supabase Auth (JWT). The admin client is initialized in `server.py` via `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` env vars.

Read `server.py` to understand exactly how the Supabase client is used before making any changes.

---

## Step 2 — Plan the feature

Think through:
1. What does this feature need to do?
2. Does it require any new Supabase tables or columns?
3. Which existing files need to change?
4. What new files (if any) are needed?

Show the plan clearly before writing any code.

---

## Step 3 — Apply Supabase schema changes (if needed)

If the feature requires schema changes, apply them via the **Supabase Management REST API** using the service key from the environment.

### How to run SQL against Supabase

Use this pattern to execute SQL (read `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` from the `.env` file):

```python
import os, requests
from dotenv import load_dotenv
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SERVICE_KEY  = os.getenv("SUPABASE_SERVICE_KEY")
PROJECT_REF  = SUPABASE_URL.split("//")[1].split(".")[0]  # extract project ref

sql = """
-- Your SQL here
CREATE TABLE IF NOT EXISTS example (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    created_at timestamptz DEFAULT now()
);
"""

resp = requests.post(
    f"https://api.supabase.com/v1/projects/{PROJECT_REF}/database/query",
    headers={
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Content-Type": "application/json",
    },
    json={"query": sql},
)
print(resp.status_code, resp.text)
```

Save this as a temp script, run it with `python`, verify the response is 200, then delete the script.

### SQL guidelines for this project
- Always use `IF NOT EXISTS` for CREATE TABLE / CREATE INDEX
- Use `uuid` PKs with `gen_random_uuid()`
- Always add `created_at timestamptz DEFAULT now()`
- Link to users via `user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE`
- Enable RLS: `ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;`
- Add a service-role policy so the backend can access rows:
  ```sql
  CREATE POLICY "service_role_all" ON <table>
    FOR ALL TO service_role USING (true) WITH CHECK (true);
  ```
- Never drop columns or tables — only ADD

### Before applying
- Print the exact SQL you will run and confirm it looks correct
- Run in a single transaction where possible

---

## Step 4 — Implement the feature code

- Follow the patterns in `server.py` for Supabase access (use `get_sb_admin()`)
- Add new API endpoints using FastAPI
- Keep existing error handling style (`logger.error(...)`)
- Do not break existing endpoints

---

## Step 5 — Verify

After implementing:
1. If schema was changed, query the table to confirm it exists:
   ```python
   # Quick verify
   resp = requests.post(...)  # SELECT 1 FROM <new_table> LIMIT 1
   ```
2. Check that the Python code imports are correct
3. Summarize what was changed: files modified, SQL applied, endpoints added
