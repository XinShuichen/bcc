# DWARF unwinding review style report

This report summarizes the BCC review constraints that matter for an
upstream-facing DWARF/libgunwinder proposal. It is intentionally biased toward
what maintainers are likely to block, ask to split, or ask to prove.

## Review baseline

BCC treats tools as production troubleshooting programs that run as root. The
script contribution guide says `/tools` changes must be useful, tested,
low-overhead, documented with caveats, and easy to use; it also says tool
submissions normally include the tool, a man page, an example file, and a
README entry (`bcc/CONTRIBUTING-SCRIPTS.md:11-12`). The same checklist asks for
known workloads, cross-checking with other observability tools, overhead
measurement, stress testing, concise output, default output under 80 columns,
PEP8-style checking, examples, man-page overhead/caveats, README updates, and
smoke tests (`bcc/CONTRIBUTING-SCRIPTS.md:26-45`).

The current PR template now encodes the same maintainer taste: every PR should
state the problem and "Why this approach"; commit prefixes should match the
changed area and commit bodies should explain why; new tools require production
use cases, an `OVERHEAD` man-page section, examples, README entries, and smoke
tests (`bcc/.github/PULL_REQUEST_TEMPLATE.md:1-22`). Copilot review guidance is
not maintainer law, but it captures project expectations: evaluate scope fit,
existing coverage, production value, and maintenance cost before detailed
review (`bcc/.github/copilot-instructions.md:7-16`).

## Code taste that affects DWARF

The project strongly prefers small, local, measurable changes over broad
feature drops. Relevant global review rules are:

- Prefer CO-RE, BTF-enabled patterns, and fentry/fexit with fallback where
  broad kernel support is needed (`bcc/.github/copilot-instructions.md:18-28`).
- Preserve default behavior and document minimum kernel requirements for
  kernel-dependent features (`bcc/.github/copilot-instructions.md:25-28`).
- Reduce kernel-to-user traffic with BPF filtering and map aggregation where
  possible, and be explicit about helper overhead (`bcc/.github/copilot-instructions.md:30-35`).
- Keep BPF map lookups, allocations, array accesses, verifier stack use, and
  default output width reviewable (`bcc/.github/copilot-instructions.md:54-60`).

For core library changes, reviewers will be stricter. `src/cc/**` instructions
say public C++ APIs must not break without deprecation, Python ctypes bindings
must track C++ signatures, error handling must follow the surrounding API
convention, and architecture-specific code must be guarded (`bcc/.github/instructions/core.instructions.md:13-18`).
They also require RAII/resource cleanup, thread-safety consideration, optional
dependencies behind `find_package`/`HAVE_*`, Debian packaging updates, and
reference guide updates for public APIs (`bcc/.github/instructions/core.instructions.md:20-45`).

Ownership is broad but explicit. Documentation, tools, `src/cc`, Python API,
and tests all route to the same maintainers, and substantial/API-breaking
changes require codeowner review (`bcc/CODEOWNERS:1-24`). A libgunwinder
integration therefore crosses at least documentation, build, core API, Python
API, tools, and tests even if the first user-visible change is only a tool
option.

## Existing stack-maintenance history

Recent history shows stack changes are accepted, but usually as focused fixes
or narrowly scoped tool additions:

- `b14c463e libbpf-tools: add CO-RE profile (#3782)` added the libbpf profile
  tool rather than retrofitting all stack tools at once.
- `b2ef7a02 tools: Update default stack storage size to reduce frequent
  warnings` adjusted stack-map sizing behavior as a bounded operational fix.
- `72965b6f tools/profile: Add additional information to backtrace (#5109)`
  changed a single tool's stack output.
- `477ed040 libbpf-tools/memleak: Add options to adjust stack map size (#5237)`
  and `c58d7b43 libbpf-tools/memleak: Fix output error for invalid stackid
  when using combined-only mode (#5238)` handled stack-map capacity and invalid
  stack IDs locally.
- `f449d05b libbpf-tools/klockstat: Search for correct stack offset in
  userspace (#5203)`, `fef9003e libbpf-tools/trace_helpers: Fix incorrect DSO
  information in stacktrace`, and `a5eb4cb9 libbpf-tools/offcputime, futexctn:
  Fix incorrect DSO information in stacktrace (#4902)` show reviewers care
  about symbol correctness, not just capturing addresses.
- `2ddbc077 Add build_id support for BPF stackmap`, `9924e64e support symbol
  resolution of short-lived process. (#2144)`, and `aa1b904e trace: Incorrect
  symbol offsets when using build_id (#2161) (#2162)` are especially relevant:
  BCC already accepted build-id stack-map support, then accepted follow-up
  fixes for short-lived processes and offsets.

The lesson is not "large unwinding changes are impossible"; it is that each
accepted stack change had a narrow behavioral claim and a way to verify it.

## Likely objections to DWARF/libgunwinder

Reviewers are likely to object if the proposal is framed as "replace stack
maps" rather than "add an optional user-space unwind backend for selected user
stacks." Current BCC tools mostly use kernel stack helpers and stack maps; a
DWARF backend adds raw user-stack copying, register capture, ELF/CFI parsing,
cache lifecycle, and more failure modes. That is a maintenance-cost question
under the global value assessment (`bcc/.github/copilot-instructions.md:7-16`).

Specific likely objections:

- Dependency risk: libgunwinder uses `libelf`/`libdw`/`libbfd` style ELF and
  CFI processing in its core files (`public-libgunwinder/src/gu_stacktrace.c:18-43`).
  BCC core rules require optional dependencies to be guarded and added to both
  CMake and Debian packaging (`bcc/.github/instructions/core.instructions.md:37-40`).
- License clarity: public libgunwinder headers and sources are
  LGPL-3.0-or-later (`public-libgunwinder/include/gunwinder/unwinder.h:1`,
  `public-libgunwinder/src/gu_stacktrace.c:1-16`), while BCC is Apache-2.0 and
  contains LGPL/BSD libbpf-tools components. Upstream will need an explicit
  build/linking story, not just copied code.
- Behavior compatibility: BCC stack users already distinguish valid stacks,
  unavailable stacks, collisions, and capacity errors. For example,
  `tools/profile.py` treats negative stack IDs except `-EFAULT` as display
  failures and tracks `-EEXIST` collisions (`bcc/tools/profile.py:69-72`,
  `bcc/tools/profile.py:375-382`), while libbpf `profile.c` treats `-EFAULT` as
  unavailable and `-EEXIST` as stack-map collision (`bcc/libbpf-tools/profile.c:30-43`).
- Overhead: CPA-style DWARF capture copies raw stack pages and register state
  through perf events (`public-continue-profiling-agent/bpf/src/stack_capture/stack_capture.bpf.c:273-301`).
  BCC review guidance asks for low overhead, map aggregation, and overhead
  disclosure (`bcc/CONTRIBUTING-SCRIPTS.md:31-41`,
  `bcc/.github/copilot-instructions.md:30-35`).
- Scope: CPA is a continuous profiler runtime with queues, drop policy,
  process-exit invalidation, and a worker model (`public-continue-profiling-agent/src/cpa_monitor/cpa_unwinder.c:35-43`,
  `public-continue-profiling-agent/src/cpa_monitor/cpa_unwinder.c:886-994`).
  Importing that runtime shape directly into BCC would not match BCC's existing
  small script/API style.

## Commit split guidance

Do not submit a monolithic "DWARF unwinding" PR. A reviewer-friendly split is:

1. `docs:` design/rationale and compatibility matrix.
2. `build:` optional libgunwinder discovery only, default off, with Debian
   package updates if dependencies are enabled.
3. `src/cc:` internal C++ wrapper for a BCC-owned unwind interface, with no
   Python-visible behavior change.
4. `src/python:` ctypes bindings and an opt-in Python API, preserving existing
   `StackTrace.walk()` behavior.
5. `tests/cc:` and `tests/python:` unit/integration coverage for fallback,
   error mapping, and symbol formatting.
6. `tools/profile:` one opt-in migration target, because `profile.py` already
   has stack options, storage sizing, missed-stack warnings, and a clear
   workload model (`bcc/tools/profile.py:98-123`, `bcc/tools/profile.py:302-389`).
7. Additional tool PRs only after the first tool's overhead and compatibility
   are accepted.

Each commit body should explain why the step exists, matching the PR template
and commit-message guidance (`bcc/.github/PULL_REQUEST_TEMPLATE.md:11-14`,
`bcc/.github/copilot-instructions.md:37-47`).
