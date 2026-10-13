# Setting up Operon on a new machine

A step-by-step guide for Windows, macOS and Linux. Allow about 10 minutes; most of it is downloads.

## 1. Prerequisites

| Tool | Version | Check with |
|---|---|---|
| Python | 3.10 or newer | `python --version` (macOS/Linux: `python3 --version`) |
| Git | Any recent version | `git --version` |
| GitHub CLI (optional) | Any | `gh --version` |

- **Windows:** Install Python from python.org and tick **"Add python.exe to PATH"** in the installer. Install Git from git-scm.com.
- **macOS:** `brew install python git gh`
- **Linux (Ubuntu/Debian):** `sudo apt install python3 python3-venv python3-pip git`

You'll need about 1 GB of free disk space for Python packages and the Chromium browser.

## 2. Get the code

Repository: [Avnish1909/Operon](https://github.com/Avnish1909/Operon)

If the repository is private, sign in with a GitHub account that has access.

**Option A: GitHub CLI**

```bash
gh auth login
gh repo clone Avnish1909/Operon
cd Operon
```

**Option B: Plain Git**

```bash
git clone https://github.com/Avnish1909/Operon.git
cd Operon
```

If Git asks for a password, use GitHub authentication or a personal access token.

## 3. Create a virtual environment and install

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m playwright install chromium
```

If PowerShell says running scripts is disabled, run this once and try again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m playwright install chromium
```

On Linux, you may need system dependencies:

```bash
python -m playwright install --with-deps chromium
```

Activate the environment every time you open a new terminal. Your prompt should start with `(.venv)`.

## 4. Add an API key

Copy the template:

- **Windows:** `Copy-Item .env.example .env`
- **macOS/Linux:** `cp .env.example .env`

Open `.env` and uncomment the key you want to use by removing the `#` before the variable name.

For Anthropic:

```text
ANTHROPIC_API_KEY=your-anthropic-key-here
```

Or for OpenRouter:

```text
OPENROUTER_API_KEY=your-openrouter-key-here
```

If both are configured, the project may prefer Anthropic, depending on its configuration. The `.env` file should be gitignored so your API key is not committed.

## 5. Check the installation

No API key is needed for the scripted tests:

```bash
pytest
```

Expected result: **56 passed**.

The tests exercise the browser, policy gate, sandbox company, and agent loop with a scripted model.

## 6. Run Operon

List the benchmark scenarios:

```bash
operon scenarios
```

Run a scenario with the browser visible:

```bash
operon scenario refund --headed
```

To limit model calls on OpenRouter's free tier:

```bash
operon scenario refund --headed --max-model-calls 30 --verifier-steps 10
```

Other useful commands:

```bash
operon run "Collections needs the list of customers who are more than 60 days overdue, with totals."

operon scenario onboarding --interactive

operon sandbox serve

operon eval
```

The sandbox is available at `http://127.0.0.1:8765` with the demo credentials `manager / northwind`.

Each run writes a report to `runs/<run-id>/report.html`. Open it in a browser.

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| `operon` is not recognized | Activate the virtual environment, or run `.venv\Scripts\operon.exe` on Windows. |
| `No model credentials found` | Check that `.env` exists and the API key is configured correctly. |
| Chromium executable missing | Run `python -m playwright install chromium`. |
| Port 8765 already in use | Add `OPERON_SANDBOX_PORT=8780` to `.env`. |
| OpenRouter free-model quota exhausted | Wait for the quota reset or use a model with available quota. |
| OpenRouter returns 503 | The model service may be busy; retry later. |
| Chromium fails on Linux | Run `python -m playwright install --with-deps chromium`. |
| Need a fresh fake company | Run `operon sandbox reset`. This resets sandbox data. |

Repository: https://github.com/Avnish1909/Operon