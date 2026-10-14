"""iPhone automation agent: Qwen3-VL (vision) + WebDriverAgent (control)."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from qwen_vl_agent import QwenVLAgent
from wda_client import WDAClient, WDAError


class PhoneAgent:
    """
    Phone automation agent for physical iPhones.

    - Captures screenshots via WebDriverAgent
    - Uses Qwen3-VL to choose the next GUI action
    - Executes tap / swipe / type / wait through WDA
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        default_config: Dict[str, Any] = {
            "wda_url": "http://127.0.0.1:8100",
            "bundle_id": None,  # None = default WDA session (SpringBoard-capable)
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

        self.config = default_config
        if config:
            self.config.update(config)

        self.context: Dict[str, Any] = {
            "previous_actions": [],
            "current_app": "SpringBoard",
            "task_request": "",
            "session_id": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "screenshots": [],
        }

        self._stop_requested = False
        self.wda: Optional[WDAClient] = None

        self._setup_logging()
        self._setup_directories()
        self._connect_wda()

        logging.info("Initializing Qwen3-VL agent...")
        model_kwargs: Dict[str, Any] = {
            "use_flash_attention": self.config.get("use_flash_attention", False),
            "temperature": self.config["temperature"],
            "max_tokens": self.config["max_tokens"],
        }
        if self.config.get("model_name"):
            model_kwargs["model_name"] = self.config["model_name"]

        self.vl_agent = QwenVLAgent(**model_kwargs)
        logging.info("iPhone agent ready (WDA)")

    def request_stop(self) -> None:
        """Signal the running task loop to exit after the current cycle."""
        self._stop_requested = True
        logging.warning("Stop requested")

    def _setup_logging(self) -> None:
        log_file = f"phone_agent_{self.context['session_id']}.log"
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
            force=True,
        )
        logging.info("Session started: %s", self.context["session_id"])

    def _setup_directories(self) -> None:
        Path(self.config["screenshot_dir"]).mkdir(parents=True, exist_ok=True)
        logging.info("Screenshots directory: %s", self.config["screenshot_dir"])

    def _connect_wda(self) -> None:
        self.wda = WDAClient(
            base_url=self.config["wda_url"],
            bundle_id=self.config.get("bundle_id"),
            timeout=float(self.config.get("wda_timeout", 30.0)),
        )
        try:
            self.wda.ensure_ready()
            self._verify_screen_resolution()
            logging.info("✓ WDA connection verified at %s", self.config["wda_url"])
        except WDAError as exc:
            logging.error("WDA error: %s", exc)
            raise RuntimeError(
                "Failed to connect to WebDriverAgent. "
                "Confirm WDA is running on the iPhone and port 8100 is forwarded "
                f"(wda_url={self.config['wda_url']})."
            ) from exc

    def _verify_screen_resolution(self) -> None:
        assert self.wda is not None
        try:
            width, height = self.wda.window_size()
            cfg_w = int(self.config["screen_width"])
            cfg_h = int(self.config["screen_height"])
            if width != cfg_w or height != cfg_h:
                logging.warning("=" * 60)
                logging.warning("RESOLUTION MISMATCH DETECTED!")
                logging.warning("Device (WDA):  %s x %s", width, height)
                logging.warning("Config:        %s x %s", cfg_w, cfg_h)
                logging.warning("Auto-correcting to WDA window size")
                logging.warning("=" * 60)
                self.config["screen_width"] = width
                self.config["screen_height"] = height
            else:
                logging.info("✓ Screen size confirmed: %s x %s", width, height)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Could not verify screen size via WDA: %s", exc)

    def capture_screenshot(self) -> str:
        assert self.wda is not None
        timestamp = int(time.time())
        screenshot_path = os.path.join(
            self.config["screenshot_dir"],
            f"screen_{self.context['session_id']}_{timestamp}.png",
        )
        png = self.wda.screenshot_png()
        with open(screenshot_path, "wb") as fh:
            fh.write(png)
        logging.info("Screenshot captured: %s", screenshot_path)
        self.context["screenshots"].append(screenshot_path)
        return screenshot_path

    def execute_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        try:
            action_type = action["action"]
            logging.info("Executing: %s", action_type)

            if action_type == "terminate":
                status = action.get("status", "success")
                message = action.get("message", "Task complete")
                logging.info("✓ Task %s: %s", status, message)
                return {"success": True, "action": action, "task_complete": True}

            if action_type == "tap":
                self._execute_tap(action)
            elif action_type == "swipe":
                self._execute_swipe(action)
            elif action_type == "type":
                self._execute_type(action)
            elif action_type == "wait":
                self._execute_wait(action)
            elif action_type == "home":
                assert self.wda is not None
                self.wda.home()
            else:
                raise ValueError(f"Unknown action type: {action_type}")

            self.context["previous_actions"].append(
                {
                    "action": action_type,
                    "timestamp": time.time(),
                    "elementName": action.get("observation", "")[:50],
                }
            )
            time.sleep(self.config["step_delay"])
            return {"success": True, "action": action, "task_complete": False}

        except Exception as exc:  # noqa: BLE001
            logging.error("Action execution failed: %s", exc)
            return {
                "success": False,
                "error": str(exc),
                "action": action,
                "task_complete": False,
            }

    def _norm_to_px(self, norm_x: float, norm_y: float) -> tuple[int, int]:
        width = int(self.config["screen_width"])
        height = int(self.config["screen_height"])
        x = int(norm_x * width)
        y = int(norm_y * height)
        x = max(0, min(x, width - 1))
        y = max(0, min(y, height - 1))
        return x, y

    def _execute_tap(self, action: Dict[str, Any]) -> None:
        assert self.wda is not None
        if "coordinates" not in action:
            raise ValueError("Tap action missing coordinates")
        norm_x, norm_y = action["coordinates"]
        x, y = self._norm_to_px(norm_x, norm_y)
        logging.info(
            "Tapping at (%s, %s) [normalized: (%.3f, %.3f)]", x, y, norm_x, norm_y
        )
        self.wda.tap(x, y)

    def _execute_swipe(self, action: Dict[str, Any]) -> None:
        assert self.wda is not None
        # Prefer explicit start/end from the model when present
        if "coordinates" in action and "coordinate2" in action:
            x1, y1 = self._norm_to_px(*action["coordinates"])
            x2, y2 = self._norm_to_px(*action["coordinate2"])
            logging.info("Swiping (%s,%s) -> (%s,%s)", x1, y1, x2, y2)
            self.wda.swipe(x1, y1, x2, y2, duration=0.3)
            return

        direction = action.get("direction", "up")
        width = int(self.config["screen_width"])
        height = int(self.config["screen_height"])
        cx, cy = width // 2, height // 2
        dist = 0.35

        if direction == "up":
            x1, y1, x2, y2 = cx, int(cy + height * dist), cx, int(cy - height * dist)
        elif direction == "down":
            x1, y1, x2, y2 = cx, int(cy - height * dist), cx, int(cy + height * dist)
        elif direction == "left":
            x1, y1, x2, y2 = int(cx + width * dist), cy, int(cx - width * dist), cy
        elif direction == "right":
            x1, y1, x2, y2 = int(cx - width * dist), cy, int(cx + width * dist), cy
        else:
            raise ValueError(f"Invalid swipe direction: {direction}")

        logging.info("Swiping %s: (%s,%s) -> (%s,%s)", direction, x1, y1, x2, y2)
        self.wda.swipe(x1, y1, x2, y2, duration=0.3)

    def _execute_type(self, action: Dict[str, Any]) -> None:
        assert self.wda is not None
        if "text" not in action:
            raise ValueError("Type action missing text")
        text = action["text"]
        recent = self.context["previous_actions"][-3:]
        if not any(a.get("action") == "tap" for a in recent):
            logging.warning("Type action without recent tap — focus may be wrong")
        logging.info("Typing: %s", text)
        self.wda.type_text(text)

    def _execute_wait(self, action: Dict[str, Any]) -> None:
        wait_time = action.get("waitTime", 1000) / 1000.0
        logging.info("Waiting %.1fs...", wait_time)
        time.sleep(wait_time)

    def execute_cycle(self, user_request: str) -> Dict[str, Any]:
        screenshot_path = self.capture_screenshot()
        action = self.vl_agent.analyze_screenshot(
            screenshot_path, user_request, self.context
        )
        if not action:
            raise RuntimeError("Failed to get action from model")

        if "observation" in action:
            logging.info("Model observation: %s", action["observation"])
        if "reasoning" in action:
            logging.info("Model reasoning: %s", action["reasoning"])

        return self.execute_action(action)

    def execute_task(self, user_request: str, max_cycles: int = 15) -> Dict[str, Any]:
        self._stop_requested = False
        self.context["task_request"] = user_request
        logging.info("=" * 60)
        logging.info("STARTING TASK: %s", user_request)
        logging.info("=" * 60)

        cycles = 0
        task_complete = False
        last_error: Optional[str] = None

        while cycles < max_cycles and not task_complete and not self._stop_requested:
            cycles += 1
            logging.info("\n--- Cycle %s/%s ---", cycles, max_cycles)
            try:
                result = self.execute_cycle(user_request)
                if result.get("task_complete"):
                    task_complete = True
                    logging.info("✓ Task marked complete by agent")
                    break
                if not result["success"]:
                    last_error = result.get("error", "Unknown error")
                    logging.warning("Action failed: %s", last_error)
                    if cycles >= self.config["max_retries"]:
                        logging.error("Max retries exceeded")
                        break
            except KeyboardInterrupt:
                logging.info("Task interrupted by user")
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logging.error("Cycle error: %s", exc)
                if cycles >= self.config["max_retries"]:
                    break
                time.sleep(2)

        if self._stop_requested:
            logging.info("Task stopped by user after %s cycles", cycles)

        if cycles >= max_cycles and not task_complete and not self._stop_requested:
            logging.info("Max cycles reached, checking completion...")
            screenshot_path = self.capture_screenshot()
            completion_check = self.vl_agent.check_task_completion(
                screenshot_path, user_request, self.context
            )
            if completion_check.get("complete"):
                task_complete = True
                logging.info("✓ Task verified complete: %s", completion_check.get("reason"))

        logging.info("\n" + "=" * 60)
        if task_complete:
            logging.info("✓ TASK COMPLETED in %s cycles", cycles)
            success = True
        else:
            logging.info("✗ TASK INCOMPLETE after %s cycles", cycles)
            if last_error:
                logging.info("Last error: %s", last_error)
            success = False
        logging.info("=" * 60)

        return {
            "success": success,
            "cycles": cycles,
            "task_complete": task_complete,
            "stopped": self._stop_requested,
            "context": self.context,
            "screenshots": self.context["screenshots"],
        }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python phone_agent.py 'your task here'")
        sys.exit(1)

    task = " ".join(sys.argv[1:])
    config_path = "config.json"
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = {}

    agent = PhoneAgent(cfg)
    result = agent.execute_task(task)
    if result["success"]:
        print(f"\n✓ Task completed in {result['cycles']} cycles")
    else:
        print(f"\n✗ Task failed after {result['cycles']} cycles")
