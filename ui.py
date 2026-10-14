"""Gradio control panel for PhoneDriver-iOS (WebDriverAgent)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from threading import Thread

import gradio as gr
import requests

from phone_agent import PhoneAgent

try:
    from wda_client import WDAClient
except ImportError:  # pragma: no cover
    WDAClient = None  # type: ignore


class UILogHandler(logging.Handler):
    """Custom logging handler that stores logs for UI display."""

    def __init__(self) -> None:
        super().__init__()
        self.logs: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        log_entry = self.format(record)
        self.logs.append(log_entry)
        if len(self.logs) > 200:
            self.logs = self.logs[-200:]


current_screenshot = None
log_handler: UILogHandler | None = None
is_running = False
agent: PhoneAgent | None = None
current_config = None


def load_config(config_path: str = "config.json"):
    if not os.path.exists(config_path):
        return get_default_config()
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        default = get_default_config()
        for key, value in default.items():
            if key not in config:
                config[key] = value
        return config
    except json.JSONDecodeError:
        return get_default_config()


def get_default_config():
    return {
        "wda_url": "http://127.0.0.1:8100",
        "bundle_id": None,
        "screen_width": 1170,
        "screen_height": 2532,
        "screenshot_dir": "./screenshots",
        "max_retries": 3,
        "model_name": "Qwen/Qwen3-VL-8B-Instruct",
        "use_flash_attention": False,
        "temperature": 0.1,
        "max_tokens": 512,
        "step_delay": 1.5,
        "enable_visual_debug": False,
        "wda_timeout": 30.0,
    }


def save_config(config, config_path: str = "config.json") -> bool:
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as exc:  # noqa: BLE001
        logging.error("Failed to save config: %s", exc)
        return False


def setup_logging() -> None:
    global log_handler
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    log_handler = UILogHandler()
    log_handler.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    log_handler.setFormatter(formatter)
    root_logger.addHandler(log_handler)

    file_handler = logging.FileHandler("phone_agent_ui.log")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)


def detect_device_resolution(wda_url: str | None = None):
    """Detect screen size via WDA window/size."""
    url = (wda_url or (current_config or {}).get("wda_url") or "http://127.0.0.1:8100").rstrip(
        "/"
    )
    try:
        status = requests.get(f"{url}/status", timeout=5)
        if status.status_code != 200:
            return None, None, f"⚠️ WDA not ready at {url} (HTTP {status.status_code})"

        client = WDAClient(base_url=url) if WDAClient else None
        if client is None:
            return None, None, "⚠️ wda_client unavailable"
        client.ensure_ready()
        width, height = client.window_size()
        return width, height, f"✓ WDA {url}: {width} x {height}"
    except Exception as exc:  # noqa: BLE001
        return None, None, f"✗ WDA error: {exc}"


def check_wda_status(config_json: str):
    try:
        cfg = json.loads(config_json)
    except json.JSONDecodeError:
        cfg = current_config or get_default_config()
    _, _, message = detect_device_resolution(cfg.get("wda_url"))
    return message


def execute_task_thread(task, max_cycles, config):
    global current_screenshot, is_running, agent

    if log_handler:
        log_handler.logs.clear()

    is_running = True

    try:
        logging.info("Starting task: '%s'", task)

        if agent is None:
            logging.info("Initializing Phone Agent (first time)...")
            agent = PhoneAgent(config)
        else:
            logging.info("Reusing existing agent...")
            agent.context["previous_actions"] = []
            agent.context["task_request"] = task
            agent.context["session_id"] = datetime.now().strftime("%Y%m%d_%H%M%S")
            agent.context["screenshots"] = []
            agent._stop_requested = False  # noqa: SLF001

        original_capture = agent.capture_screenshot

        def capture_with_tracking():
            path = original_capture()
            global current_screenshot
            current_screenshot = path
            return path

        agent.capture_screenshot = capture_with_tracking  # type: ignore[method-assign]
        result = agent.execute_task(task, max_cycles=max_cycles)

        if result["success"]:
            logging.info("✓ Task completed in %s cycles", result["cycles"])
        else:
            logging.info("⚠️ Task incomplete after %s cycles", result["cycles"])

    except KeyboardInterrupt:
        logging.info("Task interrupted by user")
    except Exception as exc:  # noqa: BLE001
        logging.error("Task execution error: %s", exc, exc_info=True)
    finally:
        is_running = False


def start_task(task, max_cycles, config_json):
    global is_running, current_config

    if is_running:
        return ("⚠️ A task is already running", None, gr.update(active=False))

    if not task.strip():
        return ("✗ Please enter a task", None, gr.update(active=False))

    try:
        config = json.loads(config_json)
        current_config = config
    except json.JSONDecodeError as exc:
        return (f"✗ Invalid config JSON: {exc}", None, gr.update(active=False))

    try:
        max_cycles = int(max_cycles)
        if max_cycles < 1:
            max_cycles = 15
    except ValueError:
        max_cycles = 15

    thread = Thread(target=execute_task_thread, args=(task, max_cycles, config))
    thread.daemon = True
    thread.start()

    return ("✓ Task started...", None, gr.update(active=True))


def update_ui():
    global current_screenshot, log_handler, is_running

    screenshot = None
    if current_screenshot and os.path.exists(current_screenshot):
        screenshot = current_screenshot

    logs = "\n".join(log_handler.logs) if log_handler else ""
    return (screenshot, logs, gr.update(active=is_running))


def stop_task():
    global is_running, agent
    if is_running and agent is not None:
        agent.request_stop()
        return "⚠️ Stopping after current cycle..."
    if is_running:
        is_running = False
        return "⚠️ Stopping task..."
    return "No task running"


def apply_settings(
    wda_url,
    screen_width,
    screen_height,
    temp,
    max_tok,
    step_delay,
    use_fa2,
    visual_debug,
):
    global current_config

    try:
        config = current_config or load_config()
        config["wda_url"] = str(wda_url).strip() or "http://127.0.0.1:8100"
        config["screen_width"] = int(screen_width)
        config["screen_height"] = int(screen_height)
        config["temperature"] = float(temp)
        config["max_tokens"] = int(max_tok)
        config["step_delay"] = float(step_delay)
        config["use_flash_attention"] = use_fa2
        config["enable_visual_debug"] = visual_debug

        if save_config(config):
            current_config = config
            return "✓ Settings saved", json.dumps(config, indent=2)
        return "✗ Failed to save settings", json.dumps(config, indent=2)
    except ValueError as exc:
        return f"✗ Invalid value: {exc}", json.dumps(current_config or {}, indent=2)


def auto_detect_resolution():
    width, height, message = detect_device_resolution()
    if width and height:
        return width, height, message
    cfg = current_config or get_default_config()
    return cfg["screen_width"], cfg["screen_height"], message


def clear_logs_fn():
    if log_handler:
        log_handler.logs.clear()
    return ""


def create_ui():
    global current_config
    current_config = load_config()
    Path(current_config["screenshot_dir"]).mkdir(parents=True, exist_ok=True)

    with gr.Blocks(title="Phone Agent Control Panel", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# 📱 Phone Agent Control Panel")
        gr.Markdown(
            "*PhoneDriver-iOS — Qwen3-VL + WebDriverAgent for physical iPhone GUI automation*"
        )

        with gr.Tabs():
            with gr.Tab("🎯 Task Control"):
                with gr.Row():
                    with gr.Column(scale=2):
                        task_input = gr.Textbox(
                            label="Task Description",
                            placeholder="e.g., 'Open Phone, dial a number, then hang up'",
                            lines=3,
                        )

                        with gr.Row():
                            max_cycles = gr.Number(
                                label="Max Cycles",
                                value=15,
                                minimum=1,
                                maximum=50,
                            )
                            start_btn = gr.Button(
                                "▶️ Start Task", variant="primary", scale=2
                            )
                            stop_btn = gr.Button("⏹️ Stop", variant="stop", scale=1)

                        status_text = gr.Textbox(
                            label="Status", lines=2, interactive=False
                        )
                        wda_check_btn = gr.Button("🔌 Check WDA")
                        wda_status = gr.Textbox(
                            label="WDA Status", lines=1, interactive=False
                        )

                    with gr.Column(scale=3):
                        image_output = gr.Image(
                            label="Current Screen",
                            type="filepath",
                            height=600,
                        )

                log_output = gr.Textbox(
                    label="📋 Execution Log",
                    lines=15,
                    max_lines=20,
                    interactive=False,
                    show_copy_button=True,
                )

                with gr.Row():
                    refresh_btn = gr.Button("🔄 Refresh Display")
                    clear_logs_btn = gr.Button("🗑️ Clear Logs")

            with gr.Tab("⚙️ Settings"):
                gr.Markdown("### WebDriverAgent")
                wda_url = gr.Textbox(
                    label="WDA URL",
                    value=current_config.get("wda_url", "http://127.0.0.1:8100"),
                )

                gr.Markdown("### Device Configuration")
                with gr.Row():
                    with gr.Column():
                        detect_btn = gr.Button("🔍 Auto-Detect Screen Size (WDA)")
                        detect_status = gr.Textbox(
                            label="Detection Status", interactive=False
                        )
                    with gr.Column():
                        screen_width = gr.Number(
                            label="Screen Width (points)",
                            value=current_config["screen_width"],
                        )
                        screen_height = gr.Number(
                            label="Screen Height (points)",
                            value=current_config["screen_height"],
                        )

                gr.Markdown("### Model Parameters")
                with gr.Row():
                    temperature = gr.Slider(
                        label="Temperature",
                        minimum=0.0,
                        maximum=1.0,
                        value=current_config["temperature"],
                        step=0.05,
                    )
                    max_tokens = gr.Number(
                        label="Max Tokens",
                        value=current_config["max_tokens"],
                        minimum=128,
                        maximum=2048,
                    )

                with gr.Row():
                    step_delay = gr.Slider(
                        label="Step Delay (seconds)",
                        minimum=0.5,
                        maximum=5.0,
                        value=current_config["step_delay"],
                        step=0.1,
                    )

                gr.Markdown("### Advanced Options")
                with gr.Row():
                    use_flash_attn = gr.Checkbox(
                        label="Use Flash Attention 2",
                        value=current_config.get("use_flash_attention", False),
                    )
                    visual_debug = gr.Checkbox(
                        label="Enable Visual Debug",
                        value=current_config.get("enable_visual_debug", False),
                    )

                apply_btn = gr.Button("💾 Save Settings", variant="primary")
                settings_status = gr.Textbox(
                    label="Settings Status", interactive=False
                )

                gr.Markdown("### Configuration JSON")
                config_editor = gr.Code(
                    label="Current Configuration",
                    language="json",
                    value=json.dumps(current_config, indent=2),
                    lines=18,
                )

            with gr.Tab("❓ Help"):
                gr.Markdown(
                    """
## Quick Start (iPhone + WDA)

1. **Mac + Xcode**: build & run `WebDriverAgentRunner` on a trusted iPhone (Developer Mode on)
2. **Forward port** (iOS 17+ often needs a tunnel first):

```bash
# iOS 17+
sudo pymobiledevice3 remote start-tunnel
# then, in another terminal:
pymobiledevice3 usbmux forward 8100 8100
```

3. **Check**: open Settings → Check WDA, or `curl http://127.0.0.1:8100/status`
4. **Run UI**: `python ui.py` → http://localhost:7860

## Task Examples
- Open Settings and turn on Airplane Mode
- Open Safari and go to apple.com
- Open Phone and dial a number (then hang up)

## Troubleshooting
- **Wrong taps**: Auto-detect screen size in Settings (WDA points, not Android px)
- **No WDA**: rebuild WebDriverAgentRunner in Xcode after iOS updates
- **Model OOM**: use `Qwen/Qwen3-VL-4B-Instruct` or `8B` in `config.json` `model_name`
                    """
                )

        timer = gr.Timer(value=3, active=False)

        start_btn.click(
            fn=start_task,
            inputs=[task_input, max_cycles, config_editor],
            outputs=[status_text, image_output, timer],
        )
        stop_btn.click(fn=stop_task, outputs=status_text)
        timer.tick(fn=update_ui, outputs=[image_output, log_output, timer])
        refresh_btn.click(fn=update_ui, outputs=[image_output, log_output, timer])
        clear_logs_btn.click(fn=clear_logs_fn, outputs=log_output)
        wda_check_btn.click(
            fn=check_wda_status, inputs=[config_editor], outputs=wda_status
        )
        detect_btn.click(
            fn=auto_detect_resolution,
            outputs=[screen_width, screen_height, detect_status],
        )
        apply_btn.click(
            fn=apply_settings,
            inputs=[
                wda_url,
                screen_width,
                screen_height,
                temperature,
                max_tokens,
                step_delay,
                use_flash_attn,
                visual_debug,
            ],
            outputs=[settings_status, config_editor],
        )

    return demo


def main():
    print("PhoneDriver-iOS UI Starting...")
    setup_logging()
    demo = create_ui()
    print("Starting server on http://localhost:7860")
    demo.queue()
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        show_error=True,
    )


if __name__ == "__main__":
    main()
