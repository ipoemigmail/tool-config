---
description: General-purpose subagent
mode: subagent
#model: anthropic/claude-opus-4-7
#model: openai/gpt-5.5
#model: openai/gpt-5.4-mini
#model: opencode-go/deepseek-v4-flash
#model: opencode-go/deepseek-v4-pro
#model: opencode-go/glm-5.1
#model: opencode-go/kimi-k2.5
#model: opencode-go/kimi-k2.6
#model: opencode-go/minimax-m2.5
#model: opencode-go/minimax-m2.7
#model: opencode-go/qwen3.5-plus
#model: opencode-go/qwen3.6-plus
model: kakao-ai-platform/glm-5-fp8
#variant: max
permission:
  edit: allow
  bash: allow
  webfetch: allow
---

You are a general-purpose agent for researching complex questions and
executing multi-step tasks.

Use this to run multiple units of work in parallel.
