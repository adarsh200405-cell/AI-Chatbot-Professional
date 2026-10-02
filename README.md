# AI Assistant — Professional Gemini Chatbot

A Flask + Gemini AI assistant with a modern responsive UI.

## Included features
- Modern responsive UI, sidebar and welcome screen
- Dark / light / system theme with saved preference
- Persistent SQLite chat history
- New Chat, rename, delete, search history
- Markdown rendering and copy-code buttons
- Streaming Gemini responses
- Stop generation and regenerate
- Conversation memory/context
- Browser speech-to-text and text-to-speech support
- PDF upload and text chat
- Settings: theme, language, font size, response style
- API error handling and simple rate limiting
- `.env` secret protection

## Setup
1. Copy `.env.example` to `.env`.
2. Put your Gemini API key in `.env`.
3. Create a virtual environment:
   `python -m venv .venv`
4. Activate it on Windows:
   `.venv\Scripts\activate`
5. Install:
   `pip install -r requirements.txt`
6. Run:
   `python app.py`
7. Open `http://127.0.0.1:5000`

Do NOT upload `.env`, `.venv`, `venv`, or `data/chatbot.db` to GitHub.

## Notes
- Voice input/output uses browser Web Speech APIs; browser support varies.
- PDF text extraction works for text-based PDFs. Scanned PDFs require OCR.
- For production deployment, add authenticated users, server-side persistent storage, HTTPS, stronger rate limiting, and secret management.


## Gemini server-error fix
The backend now retries transient Gemini errors and automatically tries fallback models.
If the API key is invalid/expired/blocked, it reports that separately instead of showing a generic 503 message.

### Important
Do not commit `.env`, API keys, `.venv`, or the SQLite database.
If an API key was exposed publicly, revoke it and create a new key in Google AI Studio.

### Windows
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
python app.py
```
Then open `http://127.0.0.1:5000`.
