# Contributing to gemini-live-translate-demos

Thanks for your interest in contributing! 🎉  
Whether you're fixing a bug, improving an existing demo, or adding a brand-new one, this guide has everything you need to get started.

---

## Prerequisites

Before you dive in, make sure you have the following installed and configured:

| Requirement | Notes |
|---|---|
| **Python 3.11+** | [python.org/downloads](https://www.python.org/downloads/) |
| **ffmpeg** | Install via your system package manager: `brew install ffmpeg`, `apt install ffmpeg`, etc. |
| **GEMINI_API_KEY** | Obtain a key from [Google AI Studio](https://aistudio.google.com/) and export it: `export GEMINI_API_KEY="your-key-here"` |

---

## Running Demos Locally

1. **Clone the repo**

   ```bash
   git clone https://github.com/unrealandychan/gemini-live-translate-demos.git
   cd gemini-live-translate-demos
   ```

2. **Create and activate a virtual environment**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   ```

3. **Install demo dependencies**

   Each demo folder may have its own `requirements.txt`. Install dependencies for the demo you want to run:

   ```bash
   pip install -r demos/<demo-name>/requirements.txt
   ```

4. **Set your API key**

   ```bash
   export GEMINI_API_KEY="your-key-here"
   ```

5. **Run the demo**

   ```bash
   python demos/<demo-name>/main.py
   ```

   Refer to the individual `demos/<demo-name>/README.md` for any demo-specific flags or configuration.

---

## Adding a New Demo

We love new demo ideas! Follow these conventions so everything stays consistent:

### Folder naming

Place your demo under `demos/` using **lowercase words separated by hyphens**:

```
demos/
└── my-awesome-demo/
    ├── README.md          # required
    ├── main.py            # required (at least one .py file)
    ├── requirements.txt   # optional but recommended
    └── ...
```

### Requirements

| Item | Required? |
|---|---|
| `README.md` in the demo folder | ✅ Yes |
| At least one `.py` file | ✅ Yes |
| Short description of what the demo does | ✅ Yes (in the README) |
| `requirements.txt` if third-party packages are needed | Recommended |

### README.md template

```markdown
# <Demo Name>

A one-line description of what this demo does.

## What it demonstrates
- Feature / concept 1
- Feature / concept 2

## Prerequisites
List any extra system dependencies beyond the global ones.

## Usage
\`\`\`bash
python main.py [--flags]
\`\`\`

## How it works
Brief explanation of the key implementation details.
```

---

## Code Style

This project uses [**ruff**](https://docs.astral.sh/ruff/) for linting. CI runs the following check on every PR:

```bash
ruff check demos/ --select E,W,F --ignore E501
```

To run it locally before pushing:

```bash
pip install ruff
ruff check demos/ --select E,W,F --ignore E501
```

Beyond what ruff enforces, please follow these conventions:

- **Type hints** — annotate all function parameters and return types.

  ```python
  def translate(text: str, target_lang: str) -> str:
      ...
  ```

- **Docstrings** — every public function and class should have a docstring (Google style preferred).

  ```python
  def translate(text: str, target_lang: str) -> str:
      """Translate text into the target language using Gemini Live.

      Args:
          text: The source text to translate.
          target_lang: BCP-47 language code, e.g. "fr" or "zh-TW".

      Returns:
          The translated string.
      """
  ```

- **No bare `print()` inside functions** — use `logging` for diagnostic output inside functions. Top-level script output (e.g. displaying results to the user) is fine.

---

## PR Checklist

Before opening a pull request, please confirm:

- [ ] My code passes `ruff check demos/ --select E,W,F --ignore E501` with no errors.
- [ ] All functions and classes have type hints and docstrings.
- [ ] I have not left bare `print()` calls inside functions (use `logging` instead).
- [ ] A `README.md` exists inside my demo folder (if adding a new demo).
- [ ] My demo folder follows the `lowercase-hyphen-separated` naming convention.
- [ ] I have tested the demo end-to-end with a real `GEMINI_API_KEY`.
- [ ] I have updated the top-level `README.md` to list the new demo (if applicable).
- [ ] My PR description explains *what* the change does and *why*.

---

Questions? Open a [GitHub Discussion](../../discussions) or file an [issue](../../issues). Happy hacking! 🚀
