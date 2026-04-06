import re
import json
import os
import traceback
from aqt import mw, gui_hooks
from aqt.utils import showWarning, tooltip
from aqt.operations import CollectionOp

from .config_dialog import (
    ConfigDialog, CONFIG_API_KEY, CONFIG_DEFAULT_MODEL,
    CONFIG_REVIEWER_ACTIONS, CONFIG_MODE,
)
from .openrouter_client import OpenRouterClient
from .backend_client import BackendClient, AuthError, CreditsExhaustedError, NetworkError
from .auth_dialog import AuthDialog
from .markdown_converter import markdown_to_html

ADDON_PACKAGE = __name__.split(".")[0]

# Track if a generation is in progress to prevent double-clicks
_generating = False

# ---------------------------------------------------------------------------
# CSS for injected buttons and content
# ---------------------------------------------------------------------------
INJECTED_CSS = """
#ai-reviewer-buttons, .reviewai-auto-buttons {
    text-align: center; margin: 15px 0; width: 100%;
}
.reviewai-action-block { margin: 10px 0; }
.reviewai-action-bar {
    display: flex; gap: 8px; justify-content: center; flex-wrap: wrap;
}
.reviewai-btn {
    background: rgba(100,126,234,0.85); border: 1px solid rgba(100,126,234,0.6);
    color: white; padding: 8px 18px; border-radius: 18px; font-size: 13px;
    font-weight: 500; cursor: pointer; transition: all 0.2s ease;
}
.reviewai-btn:hover {
    background: rgba(100,126,234,1); transform: translateY(-1px);
    box-shadow: 0 3px 8px rgba(100,126,234,0.4);
}
.reviewai-btn:disabled {
    opacity: 0.5; cursor: wait; transform: none;
}
.reviewai-btn-clear {
    background: rgba(200,80,80,0.7); border: 1px solid rgba(200,80,80,0.5);
    color: white; padding: 8px 18px; border-radius: 18px; font-size: 13px;
    font-weight: 500; cursor: pointer; transition: all 0.2s ease;
}
.reviewai-btn-clear:hover {
    background: rgba(200,80,80,0.9); transform: translateY(-1px);
}
.reviewai-content {
    background: rgba(0,0,0,0.06); border-radius: 10px; padding: 14px 18px;
    margin-top: 8px; text-align: left; line-height: 1.5;
    border: 1px solid rgba(0,0,0,0.08);
    font-size: 14px !important; max-width: 600px; margin-left: auto; margin-right: auto;
}
.reviewai-content strong { color: #4a6fa5; }
.reviewai-content em { color: #6a7a4a; }
.reviewai-content h1, .reviewai-content h2, .reviewai-content h3 {
    margin: 0.3em 0;
}
.reviewai-content ul { padding-left: 1.3em; margin: 0.3em 0; }
.reviewai-content p { margin: 0.3em 0; }
.reviewai-login-prompt {
    text-align: center; margin: 15px 0; padding: 12px;
    background: rgba(0,0,0,0.04); border-radius: 10px;
    font-size: 13px; color: #666;
}
.reviewai-login-prompt a { color: #4a6fa5; cursor: pointer; }
.night_mode .reviewai-content {
    background: rgba(255,255,255,0.06); border-color: rgba(255,255,255,0.08);
    color: #d0d0d0;
}
.night_mode .reviewai-content strong { color: #7eb8da; }
.night_mode .reviewai-content em { color: #a8c97a; }
.night_mode .reviewai-btn {
    background: rgba(80,110,200,0.7); border-color: rgba(80,110,200,0.5);
}
.night_mode .reviewai-login-prompt {
    background: rgba(255,255,255,0.05); color: #999;
}
"""


def get_config():
    try:
        return mw.addonManager.getConfig(ADDON_PACKAGE)
    except Exception:
        return None


def construct_prompt(template, note_data):
    def replacer(m):
        return note_data.get(m.group(1), f"{{Field '{m.group(1)}' not found}}")
    return re.sub(r"\{\{([\w\s-]+?)\}\}", replacer, template)


def is_authenticated(config):
    auth = config.get("auth", {})
    return bool(auth.get("access_token") and auth.get("refresh_token"))


def _re_enable_buttons():
    """Re-enable buttons and reset generating flag."""
    global _generating
    _generating = False
    try:
        if mw.reviewer and mw.reviewer.web:
            mw.reviewer.web.eval(
                "document.querySelectorAll('.reviewai-btn').forEach(b => b.disabled = false);"
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Build buttons HTML for matching actions
# ---------------------------------------------------------------------------
def _build_buttons_html(actions, note, card_template=""):
    """Build HTML for action buttons and their content areas.
    If the target field is already in the card template, only show the button (no content area).
    """
    blocks = []
    for action in actions:
        aid = action.get("id", "")
        label = action.get("button_label", "AI Action")
        target_field = action.get("target_field_name", "")

        if not aid or not target_field:
            continue

        # Check if the field is already rendered by the card template
        field_in_template = f"{{{{{target_field}}}}}" in card_template

        # Read existing field content
        field_content = ""
        try:
            if target_field in note.keys():
                field_content = note[target_field] or ""
        except Exception:
            pass

        has_content = bool(field_content.strip())
        clear_display = "inline-block" if has_content else "none"

        # Only show content area below button if field is NOT in the template
        if field_in_template:
            content_html = ""
        else:
            content_display = "block" if has_content else "none"
            safe_content = field_content if has_content else ""
            content_html = (
                f'  <div class="reviewai-content" id="reviewai-content-{aid}" '
                f'    style="display:{content_display}">{safe_content}</div>'
            )

        blocks.append(
            f'<div class="reviewai-action-block">'
            f'  <div class="reviewai-action-bar">'
            f'    <button class="reviewai-btn" onclick="pycmd(\'reviewai_action:{aid}\')">'
            f'{label}</button>'
            f'    <button class="reviewai-btn-clear" style="display:{clear_display}" '
            f'      onclick="pycmd(\'reviewai_clear:{target_field}\')">Clear</button>'
            f'  </div>'
            f'{content_html}'
            f'</div>'
        )

    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# Card display hook — inject buttons
# ---------------------------------------------------------------------------
def on_card_will_show(text, card, kind):
    """Inject AI action buttons into cards on the answer side."""
    try:
        return _on_card_will_show_inner(text, card, kind)
    except Exception:
        # Never crash card display — log and return original text
        print(f"RevAI: Error in card_will_show: {traceback.format_exc()}")
        return text


def _on_card_will_show_inner(text, card, kind):
    # Only inject on answer side
    if kind not in ("reviewAnswer",):
        return text

    config = get_config()
    if not config:
        return text

    note = card.note()
    note_type_name = note.note_type()["name"]
    actions = [
        a for a in config.get(CONFIG_REVIEWER_ACTIONS, [])
        if a.get("note_type_name") == note_type_name
    ]

    if not actions:
        return text

    # Check if user is authenticated (for backend mode)
    mode = config.get(CONFIG_MODE, "backend")
    if mode == "backend" and not is_authenticated(config):
        style_block = f"<style>{INJECTED_CSS}</style>"
        login_html = (
            '<div class="reviewai-login-prompt">'
            "RevAI: <a onclick=\"pycmd('reviewai_login')\">Sign in</a> "
            "to enable AI buttons"
            "</div>"
        )
        placeholder = '<div id="ai-reviewer-buttons"></div>'
        if placeholder in text:
            return text.replace(placeholder, style_block + login_html)
        else:
            return text + style_block + login_html

    # Get the card template source to check if fields are already referenced
    card_template = card.template().get("afmt", "")

    # Build buttons HTML
    style_block = f"<style>{INJECTED_CSS}</style>"
    buttons_html = _build_buttons_html(actions, note, card_template)

    placeholder = '<div id="ai-reviewer-buttons"></div>'
    if placeholder in text:
        return text.replace(placeholder, style_block + buttons_html)
    else:
        return text + style_block + '<div class="reviewai-auto-buttons">' + buttons_html + '</div>'


gui_hooks.card_will_show.append(on_card_will_show)


# ---------------------------------------------------------------------------
# pycmd handler — AI generation, clear, login
# ---------------------------------------------------------------------------
def on_webview_message(handled_tuple, message, context):
    if not isinstance(message, str):
        return handled_tuple

    if message.startswith("reviewai_action:"):
        action_id = message.split(":", 1)[1]
        _handle_ai_action(action_id)
        return (True, None)

    if message.startswith("reviewai_clear:"):
        field_name = message.split(":", 1)[1]
        _handle_clear_field(field_name)
        return (True, None)

    if message == "reviewai_login":
        _handle_login()
        return (True, None)

    return handled_tuple


def _handle_login():
    """Show the auth dialog from the reviewer."""
    try:
        dialog = AuthDialog(mw)
        if dialog.exec():
            tooltip("Signed in! Click an AI button to get started.", period=3000)
            if mw.reviewer:
                mw.reviewer.refresh_if_needed()
    except Exception as e:
        print(f"RevAI: Login error: {e}")
        showWarning(f"Login failed: {e}", title="RevAI")


def _handle_ai_action(action_id):
    global _generating

    # Prevent double-click
    if _generating:
        tooltip("Already generating... please wait.", period=2000)
        return
    _generating = True

    config = get_config()
    if not config:
        _generating = False
        showWarning("RevAI configuration not found.", title="RevAI")
        return

    mode = config.get(CONFIG_MODE, "backend")

    # Validate setup based on mode
    if mode == "byok":
        api_key = config.get(CONFIG_API_KEY)
        model = config.get(CONFIG_DEFAULT_MODEL)
        if not api_key:
            _generating = False
            showWarning("OpenRouter API Key is not configured.\n\n"
                        "Go to Tools > RevAI Config > API Config.", title="RevAI")
            return
        if not model:
            _generating = False
            showWarning("No model selected.\n\n"
                        "Go to Tools > RevAI Config > Model Config.", title="RevAI")
            return
    else:
        if not is_authenticated(config):
            _generating = False
            _handle_login()
            return

    reviewer = mw.reviewer
    if not reviewer or not reviewer.card:
        _generating = False
        showWarning("No card is currently being reviewed.", title="RevAI")
        return

    card = reviewer.card
    note = card.note()
    note_type_name = note.note_type()["name"]

    # Find matching action
    action_config = None
    for a in config.get(CONFIG_REVIEWER_ACTIONS, []):
        if a.get("id") == action_id:
            if a.get("note_type_name") == note_type_name:
                action_config = a
            else:
                _generating = False
                tooltip(
                    f"Action '{a.get('button_label')}' is for note type "
                    f"'{a.get('note_type_name')}', not '{note_type_name}'.",
                    period=3000,
                )
                return

    if not action_config:
        _generating = False
        showWarning(f"Action '{action_id}' not found in configuration.", title="RevAI")
        return

    label = action_config.get("button_label", "")
    tooltip(f"Generating '{label}'...", period=2000)

    # Disable the buttons visually
    try:
        mw.reviewer.web.eval(
            "document.querySelectorAll('.reviewai-btn').forEach(b => b.disabled = true);"
        )
    except Exception:
        pass

    note_id = note.id

    def background_op(col):
        n = col.get_note(note_id)
        if not n:
            raise Exception(f"Note {note_id} not found.")

        target = action_config.get("target_field_name")

        note_model = n.note_type()
        field_names = col.models.field_names(note_model)
        if target not in field_names:
            raise Exception(
                f"Field '{target}' not found on note type '{note_model['name']}'.\n"
                f"Create it in Anki via Manage Note Types first."
            )

        # Build prompt
        note_data = {key: n[key] for key in n.keys()}
        prompt = construct_prompt(action_config.get("prompt_template", ""), note_data)

        if not prompt.strip():
            raise Exception("Prompt is empty after field substitution. Check your prompt template.")

        # Call AI based on mode
        if mode == "byok":
            client = OpenRouterClient(config.get(CONFIG_API_KEY))
            raw_response = client.generate(config.get(CONFIG_DEFAULT_MODEL), prompt)
        else:
            auth = config.get("auth", {})
            client = BackendClient(auth.get("access_token"), auth.get("refresh_token"))
            model = config.get(CONFIG_DEFAULT_MODEL)
            raw_response, meta = client.generate(prompt, model=model if model else None)

            # Save refreshed tokens if they changed
            if client.access_token != auth.get("access_token"):
                config["auth"]["access_token"] = client.access_token
                config["auth"]["refresh_token"] = client.refresh_token
                mw.addonManager.writeConfig(ADDON_PACKAGE, config)

        # Convert markdown to HTML and store
        html_content = markdown_to_html(raw_response)
        n[target] = html_content
        return col.update_note(n)

    def on_success(op_changes):
        global _generating
        _generating = False
        target = action_config.get("target_field_name", "")
        tooltip(f"'{target}' updated.", period=3000)
        if mw.reviewer:
            mw.reviewer.refresh_if_needed()

    def on_failure(exc):
        _re_enable_buttons()

        # Unwrap Anki's exception wrapper if needed
        actual_exc = exc
        if hasattr(exc, '__cause__') and exc.__cause__:
            actual_exc = exc.__cause__

        if isinstance(actual_exc, CreditsExhaustedError):
            showWarning(
                "You've used all your free credits!\n\n"
                "Options:\n"
                "- Redeem a coupon code in RevAI Config\n"
                "- Switch to 'Own API Key' mode with an OpenRouter key\n"
                "- Wait for monthly credit reset (1st of each month)",
                title="RevAI - No Credits",
            )
        elif isinstance(actual_exc, AuthError):
            showWarning(
                f"Authentication error: {actual_exc}\n\n"
                "Please sign in again via Tools > RevAI Config.",
                title="RevAI",
            )
        elif isinstance(actual_exc, NetworkError):
            showWarning(
                f"Connection problem:\n{actual_exc}\n\n"
                "Check your internet connection and try again.",
                title="RevAI - Network Error",
            )
        else:
            msg = str(actual_exc)
            # Truncate very long error messages
            if len(msg) > 500:
                msg = msg[:500] + "..."
            showWarning(f"AI generation failed:\n{msg}", title="RevAI")
            print(f"RevAI: Generation error: {traceback.format_exc()}")

    op = CollectionOp(parent=mw, op=background_op)
    op.success(on_success)
    op.failure(on_failure)
    op.run_in_background()


def _handle_clear_field(field_name):
    """Clear the specified field on the current note."""
    reviewer = mw.reviewer
    if not reviewer or not reviewer.card:
        return

    note = reviewer.card.note()
    if field_name not in note.keys():
        return

    note_id = note.id

    def background_op(col):
        n = col.get_note(note_id)
        if not n:
            raise Exception(f"Note {note_id} not found.")
        n[field_name] = ""
        return col.update_note(n)

    def on_success(op_changes):
        tooltip(f"'{field_name}' cleared.", period=2000)
        if mw.reviewer:
            mw.reviewer.refresh_if_needed()

    def on_failure(exc):
        showWarning(f"Failed to clear field:\n{exc}", title="RevAI")

    op = CollectionOp(parent=mw, op=background_op)
    op.success(on_success)
    op.failure(on_failure)
    op.run_in_background()


gui_hooks.webview_did_receive_js_message.append(on_webview_message)


# ---------------------------------------------------------------------------
# Config menu entry
# ---------------------------------------------------------------------------
def show_config_dialog():
    try:
        ConfigDialog(mw).exec()
    except Exception as e:
        print(f"RevAI: Config dialog error: {traceback.format_exc()}")
        showWarning(f"Failed to open RevAI Config:\n{e}", title="RevAI")


action = mw.form.menuTools.addAction("RevAI Config...")
action.triggered.connect(show_config_dialog)
mw.addonManager.setConfigAction(ADDON_PACKAGE, show_config_dialog)


# ---------------------------------------------------------------------------
# Default config on profile load
# ---------------------------------------------------------------------------
def on_profile_loaded():
    try:
        current = mw.addonManager.getConfig(ADDON_PACKAGE)
        if current is None:
            try:
                config_path = os.path.join(os.path.dirname(__file__), "config.json")
                with open(config_path, "r", encoding="utf-8") as f:
                    defaults = json.load(f)
                mw.addonManager.writeConfig(ADDON_PACKAGE, defaults)
            except Exception:
                mw.addonManager.writeConfig(ADDON_PACKAGE, {
                    CONFIG_MODE: "backend",
                    CONFIG_API_KEY: "",
                    CONFIG_DEFAULT_MODEL: "",
                    CONFIG_REVIEWER_ACTIONS: [],
                    "auth": {"access_token": "", "refresh_token": "", "email": ""},
                })
    except Exception:
        print(f"RevAI: Profile load error: {traceback.format_exc()}")


gui_hooks.profile_did_open.append(on_profile_loaded)
