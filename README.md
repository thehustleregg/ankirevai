# RevAI - AI Buttons for Anki Review

Add custom AI buttons directly to your Anki review screen. One click reads your card fields, runs your prompt, and writes the AI response back into your card - permanently.

![RevAI Demo](https://ankirevai.netlify.app/demo.gif)

**Website:** [ankirevai.netlify.app](https://ankirevai.netlify.app)  
**AnkiWeb:** [Addon #1059061770](https://ankiweb.net/shared/info/1059061770)

## Features

- **AI buttons during review** - appear automatically on the answer side
- **Custom prompts with {{FieldName}} variables** - reference any field on your card
- **Writes back permanently** - AI response saved to a target field
- **Any AI model** - hundreds of models via OpenRouter (GPT-4o, Claude, Gemini, DeepSeek, Llama, Mistral...)
- **Per-note-type actions** - different note types get different buttons
- **Smart placement** - buttons auto-appear at bottom, or place them where you want
- **300 free AI generations per month** - no credit card needed

## Quick Start

1. Install from AnkiWeb: code `1059061770`
2. Restart Anki
3. Tools > RevAI Config > Create account
4. Add a target field to your note type
5. Create an AI action with your prompt
6. Start reviewing!

Full setup guide with screenshots: [ankirevai.netlify.app](https://ankirevai.netlify.app)

## Project Structure

```
addon/
  __init__.py           # Main: hooks, button injection, pycmd handling
  backend_client.py     # Supabase auth + API client
  auth_dialog.py        # Login/register PyQt dialog
  config_dialog.py      # Config UI (actions, API mode, model selection)
  openrouter_client.py  # Direct OpenRouter client (BYOK mode)
  markdown_converter.py # Markdown to HTML with sanitization
  config.json           # Default config
  manifest.json         # Addon metadata
  lib/                  # Bundled markdown library
```

## How It Works

1. User clicks an AI button during review
2. Addon sends card field data + prompt to the RevAI backend (or directly to OpenRouter in BYOK mode)
3. AI response is converted from markdown to sanitized HTML
4. HTML is written into the target field on the card
5. Card refreshes to show the new content

## Two Modes

- **RevAI Account** - create a free account, get 300 AI generations per month. No API key needed.
- **Bring Your Own Key (BYOK)** - use your own OpenRouter API key for unlimited generations.

## Supported Anki Versions

Anki 24.06+ (macOS, Windows, Linux). AI-generated content syncs to mobile via AnkiWeb.

## Contact

Questions, feedback, or coupon requests? Visit [ankirevai.netlify.app](https://ankirevai.netlify.app) and use the contact form.

## License

MIT
