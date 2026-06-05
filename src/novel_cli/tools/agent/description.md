Start a subagent instance to work on a focused task.

The Agent tool can either create a new subagent instance or resume an existing one by `agent_id`.
Each instance keeps its own context history under the current session, so repeated use of the same
instance can preserve previous findings and work.

**Available Built-in Agent Types**

${BUILTIN_AGENT_TYPES_MD}

**Usage**

- Always provide a short `description` (3-5 words).
- Use `subagent_type` to select a built-in agent type. If omitted, `coder` is used.
- Use `model` when you need to override the built-in type's default model or the parent agent's current model.
- Use `resume` when you want to continue an existing instance instead of starting a new one.
- If an existing subagent already has relevant context or the task is a continuation of its prior work, prefer `resume` over creating a new instance.
- Default to foreground execution. Use `run_in_background=true` only when the task can continue independently, you do not need the result immediately, and there is a clear benefit to returning control before it finishes.
- Be explicit about whether the subagent should write code or only do research.
- The subagent result is only visible to you. If the user should see it, summarize it yourself.

**Explore Agent — Preferred for Codebase Research**

When you need to understand the codebase before making changes, fixing bugs, or planning features,
prefer `subagent_type="explore"` over doing the search yourself. The explore agent is optimized for
fast, read-only codebase investigation. Use it when:
- Your task will clearly require more than 3 search queries
- You need to understand how a module, feature, or code path works
- You are about to enter plan mode and want to gather context first
- You want to investigate multiple independent questions — launch multiple explore agents concurrently

When calling explore, specify the desired thoroughness in the prompt:
- "quick": targeted lookups — find a specific file, function, or config value
- "medium": understand a module — how does auth work, what calls this API
- "thorough": cross-cutting analysis — architecture overview, dependency mapping, multi-module investigation

**Deep Research — Use for Multi-Entity Novel Queries**

Delegate to `novel-researcher` subagents when:
- **Setting generation**: generating settings for 2+ entities → one subagent per entity
- **Deep Q&A**: answering questions about 3+ entities with 2+ dimensions each

Each subagent handles one entity's full pipeline (SearchGraph → SearchCorpus → ReadChapter).
Examples:
- Q&A: "分析孙悟空、牛魔王、二郎神三人的师承和战绩" → 3 subagents
- Setting: "生成花果山和水帘洞的地点设定" → 2 subagents
- Setting: "生成孙悟空、猪八戒、沙和尚的角色设定" → 3 subagents

**When Not To Use Agent**

- Reading a known file path
- Simple single-entity questions answerable in 1-2 tool calls
- Follow-up questions where you already have the context
