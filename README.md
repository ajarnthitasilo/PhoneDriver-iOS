# PhoneDriver-iOS

Fork of [OminousIndustries/PhoneDriver](https://github.com/OminousIndustries/PhoneDriver) adapted for **physical iPhones** via **WebDriverAgent (WDA)** instead of Android ADB.

Same Gradio **Phone Agent Control Panel**: natural-language task → vision model decides taps → device executes.

| Layer | Upstream (Android) | This fork (iPhone) |
|-------|--------------------|--------------------|
| Screen / input | ADB | WebDriverAgent HTTP (`:8100`) |
| Vision / planning | Qwen3-VL | Qwen3-VL (unchanged) |
| UI | Gradio `ui.py` | Gradio `ui.py` (WDA settings) |

## Requirements

- macOS with **Xcode**
- Physical **iPhone**, Developer Mode on, Trusted to this Mac
- **WebDriverAgent** built & running on the device (Apple Developer signing)
- Python **3.10+**
- GPU VRAM enough for Qwen3-VL (4B/8B recommended; 30B needs a large card)
- Optional but recommended: [`pymobiledevice3`](https://github.com/doronz88/pymobiledevice3) for USB tunnel / port forward

## 1. WebDriverAgent on the iPhone

1. Clone / open [WebDriverAgent](https://github.com/appium/WebDriverAgent) (or Appium’s WDA) in Xcode.
2. Set your Team signing for `WebDriverAgentRunner`.
3. Run the test / runner on the connected iPhone (`Product → Test` or Appium docs flow).
4. Keep WDA alive on the device (status endpoint must answer).

### Port forward (USB)

```bash
# iOS 17+ — start tunnel once (needs sudo)
sudo pymobiledevice3 remote start-tunnel

# Forward WDA port to the Mac
pymobiledevice3 usbmux forward 8100 8100
```

Check:

```bash
./scripts/check_wda.sh
# or
curl -s http://127.0.0.1:8100/status | python3 -m json.tool
```

## 2. Install this project

```bash
cd ~/PhoneDriver-iOS
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
# If transformers is too old for Qwen3-VL:
# pip install git+https://github.com/huggingface/transformers
```

Edit `config.json` if needed (`wda_url`, `model_name`, screen size).

## 3. Run

Web UI (recommended):

```bash
source .venv/bin/activate
python ui.py
```

Open [http://localhost:7860](http://localhost:7860) → **Check WDA** → enter a task → **Start Task**.

CLI:

```bash
python phone_agent.py "Open Settings"
```

## Configuration

Key fields in `config.json`:

| Key | Meaning |
|-----|---------|
| `wda_url` | WDA base URL (default `http://127.0.0.1:8100`) |
| `bundle_id` | Optional app to launch when creating a WDA session |
| `screen_width` / `screen_height` | WDA window size in **points** (auto-corrected on connect) |
| `model_name` | Hugging Face id, e.g. `Qwen/Qwen3-VL-8B-Instruct` |
| `step_delay` | Pause after each action (seconds) |

## How it works

1. Screenshot via WDA (`/session/.../screenshot`)
2. Qwen3-VL returns a `mobile_use` tool call (tap / swipe / type / wait / terminate)
3. Coordinates (0–999 space) → normalized → WDA points → `tap` / `dragFromToForDuration` / `keys`
4. Repeat until terminate or max cycles

## Upstream attribution

Original Android PhoneDriver: [OminousIndustries/PhoneDriver](https://github.com/OminousIndustries/PhoneDriver) (Apache-2.0).  
This fork replaces the ADB device layer with `wda_client.py` and updates the UI/docs for iPhone.

## Limitations

- WDA signing / rebuild is required after many iOS updates
- System dialogs and some protected screens may be hard or impossible to automate
- Vision model quality dominates success rate; use a VL model, not text-only OpenELM alone
- Not a drop-in for jailbreak-only agents; this path is **non-jailbreak WDA**
