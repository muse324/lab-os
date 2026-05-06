# AGENTS.md

## Project

This project is a low-maintenance Laboratory Operating System for university lab management.

Core stack:

- Flask
- SQLite
- Jinja2
- Vanilla JavaScript
- Low dependency architecture

Primary goals:

- Extremely maintainable
- Easy local operation
- Human-readable structure
- Diff-based synchronization
- Task continuity support
- Daily dashboard operation 

This system is NOT an enterprise workflow engine.
Avoid overengineering.

---

# Architecture Principles

## Prioritize simplicity

Prefer:

- explicit code
- small functions
- readable SQL
- local state

Avoid:

- excessive abstraction
- large frameworks
- hidden magic
- unnecessary async processing
- microservice-like decomposition

---

# Task System Philosophy

Tasks are NOT simple todos.

The task system should support:

- continuity
- recurring creative work
- long-running research activity
- incremental progress
- cognitive load reduction

The system should reduce psychological friction.

---

# Important Concept:

# Completion-triggered task generation

Some tasks automatically generate the next task when completed.

Example:

Current:

- 第4回研究のおと（note.com）を執筆する

When completed:
Automatically create:

- 第5回研究のおと（note.com）を執筆する

This is NOT date-based recurrence.

This is:

- chain-based recurrence
- continuity-driven task generation

Use event-driven generation.

---

# Task Status Design

Task statuses:

- inbox
- today
- future
- anytime
- waiting
- done
- archived

Avoid complicated workflow states.

---

# Series Task Rules

Series tasks should support:

- series_id
- series_index
- next_task_generation
- metadata inheritance

Example:

{
"series_id": "research_note",
"series_index": 4
}

When completed:

- increment series_index
- generate next task
- inherit tags/project metadata

---

# UI Principles

The UI should feel:

- calm
- lightweight
- cognitively safe
- dashboard-oriented

Avoid:

- noisy UI
- enterprise feeling
- excessive modal dialogs
- dense information walls

---

# Coding Rules

## Python

Prefer:

- dataclasses
- simple ORM usage
- explicit queries
- type hints where useful

Avoid:

- excessive decorators
- meta-programming
- hidden side effects

## Frontend

Prefer:

- minimal JS
- server-rendered UI
- progressive enhancement

Avoid:

- SPA complexity
- unnecessary build systems

---

# Database Design

SQLite first.

Schema should:

- remain human readable
- support future migration
- allow manual debugging

Avoid tightly coupled schema logic.

---

# Expected Codex Behavior

When implementing features:

1. First explain the design briefly.
2. Then implement incrementally.
3. Prefer small patches.
4. Avoid giant rewrites.
5. Preserve existing structure.
6. Keep debugging easy.

For ambiguous requirements:

- choose simpler implementation
- avoid speculative abstraction

---

# Preferred Development Style

The project evolves experimentally.

Design for:

- modification
- iteration
- reversibility
- rapid prototyping

Avoid premature optimization.
