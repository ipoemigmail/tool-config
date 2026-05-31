---
description: General-purpose subagent
mode: subagent
#model: anthropic/claude-opus-4-7
#model: openai/gpt-5.5
#model: openai/gpt-5.4-mini
model: opencode-go/deepseek-v4-pro
#model: amazon-bedrock/global.anthropic.claude-opus-4-7
variant: max
permission:
  edit: allow
  bash: allow
  webfetch: allow
---

You are a general-purpose agent for researching complex questions and
executing multi-step tasks.

Use this to run multiple units of work in parallel.
