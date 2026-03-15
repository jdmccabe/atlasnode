---
description: Initialize the current chat with the live Meridian runtime state.
mode: agent
tools: ["meridian-brain/*"]
---

Initialize this chat session with the live Meridian runtime.

Steps:

1. Call `get_brain_state`.
2. If needed, set the mode to `technical`.
3. Call `build_system_prompt`.
4. Summarize the active mode, sliders, focus, and memory count.
5. Confirm that future responses in this session will use the retrieved Meridian state.
