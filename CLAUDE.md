# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Paisabot — a Python bot application (likely with Flask backend and SQLite persistence). The project is in early setup; no source code exists yet.

## Intended Stack

- **Language**: Python
- **Web framework**: Flask
- **Database**: SQLite
- **Frontend**: Possibly Node.js/npm assets
- **Testing**: pytest

## Note on `.gitignore`

The gitignore file is misnamed `.gitiginore` (extra "i") and is not active. Rename it to `.gitignore` before committing any files.

## Expected Commands (once project is set up)

```bash
# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the app
python app.py  # or flask run

# Run tests
pytest

# Run a single test
pytest tests/test_foo.py::test_bar
```
