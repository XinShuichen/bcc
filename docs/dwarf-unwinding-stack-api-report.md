# DWARF unwinding stack API report

This report maps how BCC currently captures and consumes user/kernel stacks.
The purpose is to identify the smallest API surface a DWARF/libgunwinder
backend would need to preserve.

## Current Python API surface

The primary Python-facing stack table is `StackTrace` in
`src/python/bcc/table.py`. It defines build-id stack constants, stops at
`MAX_DEPTH = 127`, handles `BPF_STACK_TRACE_BUILDID` records differently from
plain address arrays, and exposes `walk(stack_id, resolve=None)` as the common
iterator (`bcc/src/python/bcc/table.py:1161-1204`). Existing Python tools
therefore expect stack IDs to remain the key. They do not expect a user-space
unwinder object or a changed iterator contract.

Symbolization is centralized through `BPF.sym()`. If an address object is a
`bpf_stack_build_id`, BCC resolves it through `bcc_buildsymcache_resolve()`;
otherwise it resolves by PID through the symbol cache (`bcc/src/python/bcc/__init__.py:1664-1705`).
This is the compatibility point for build-id stack maps and short-lived-process
symbol resolution.

Perf-buffer delivery is independent but important for any raw-stack capture
path. `open_perf_buffer()` opens one reader per online CPU, accepts an optional
`lost_cb`, and stores callbacks to keep them alive (`bcc/src/python/bcc/table.py:973-1019`).
If no lost callback is supplied, the C perf reader prints
`Possibly lost ... samples` for `PERF_RECORD_LOST`
(`bcc/src/cc/perf_reader.c:194-208`). A DWARF backend that ships raw stack
payloads through perf buffers must either expose lost events or preserve this
default warning path.

`trace_fields()` is a separate legacy trace-pipe interface. It reads
`trace_pipe`, filters kernel "CPU:" lost-event messages, and returns
`(task, pid, cpu, flags, timestamp, msg)` (`bcc/src/python/bcc/__init__.py:1581-1614`).
It is not a stack API and should not be changed for DWARF unwinding.

The helper documentation is also part of the API contract. It states that
`BPF_STACK_TRACE` returns `-ENOMEM` when stack storage is exhausted and that
`-EFAULT` is typically ignorable when a user stack is unavailable
(`bcc/src/cc/export/helpers.h:645-655`). C++ examples follow the same
convention: `examples/cpp/TCPSendStack.cc` captures user and kernel stack IDs
and treats `-EFAULT` as unavailable rather than lost
(`bcc/examples/cpp/TCPSendStack.cc:30-38`,
`bcc/examples/cpp/TCPSendStack.cc:98-123`).

## Tool classes

Current tool stack users fall into four compact categories:

- Profilers and flamegraph-style aggregators: `tools/profile.py`,
  `tools/offcputime.py`, `tools/offwaketime.py`, `tools/wakeuptime.py`,
  `tools/stackcount.py`, `tools/memleak.py`, and `tools/klockstat.py`.
  These mostly aggregate by stack ID in BPF maps, then call
  `stack_traces.walk()` at shutdown or interval boundaries.
- Event tools with optional or diagnostic stack display:
  `tools/capable.py`, `tools/criticalstat.py`, `tools/funcslower.py`,
  `tools/oomkill.py`, `tools/tcpdrop.py`, `tools/compactsnoop.py`, and
  `tools/deadlock.c`. These are less natural first migration targets because
  some emit per-event output and have tighter default-output expectations.
- Dynamic tracing: `tools/trace.py` generates a stack map only when a probe
  requests user or kernel stacks. It chooses `BPF_STACK_TRACE` vs
  `BPF_STACK_TRACE_BUILDID`, adds stack ID fields to the generated event
  struct, calls `.get_stackid()` in generated BPF C, then prints
  `StackTrace.walk()` output (`bcc/tools/trace.py:343-384`,
  `bcc/tools/trace.py:496-506`, `bcc/tools/trace.py:557-568`,
  `bcc/tools/trace.py:618-648`). This is the highest-risk Python tool because
  the BPF program is synthesized from user-provided probe format strings.
- Build-id stack map examples/tests: `examples/tracing/stack_buildid_example.py`
  and `tests/python/test_stackid.py`. The test creates
  `BPF_STACK_TRACE_BUILDID`, calls `.get_stackid(ctx, BPF_F_USER_STACK)`,
  adds libc as a module, and verifies `b.sym(stack.trace[0], -1)` contains
  `getuid` (`bcc/tests/python/test_stackid.py:50-80`).

The stack-using top-level files found in the local tree are:
`bcc/tools/profile.py`, `bcc/tools/offcputime.py`, `bcc/tools/offwaketime.py`,
`bcc/tools/wakeuptime.py`, `bcc/tools/stackcount.py`,
`bcc/tools/memleak.py`, `bcc/tools/klockstat.py`, `bcc/tools/capable.py`,
`bcc/tools/criticalstat.py`, `bcc/tools/funcslower.py`,
`bcc/tools/oomkill.py`, `bcc/tools/tcpdrop.py`, `bcc/tools/compactsnoop.py`,
`bcc/tools/trace.py`, and `bcc/tools/deadlock.c`.

This list intentionally excludes `tools/old/**`, Lua examples, and C++ example
programs. They are still compatibility evidence, but they are not first-wave
Python migration targets.

## Error handling in Python tools

`tools/profile.py` is the clearest Python contract. It defines
`stack_id_err(stack_id)` as any negative stack ID except `-EFAULT`; the comment
says `-EFAULT` normally means the stack trace is unavailable, such as asking for
a kernel stack in user-space code (`bcc/tools/profile.py:69-72`). It emits
kernel/user stack IDs through `stack_traces.get_stackid(&ctx->regs, 0)` and
`BPF_F_USER_STACK`, or `-1` when the user requested only one side
(`bcc/tools/profile.py:274-287`). At print time it counts missed stacks,
tracks `-EEXIST` collisions, emits `[Missed User Stack]` /
`[Missed Kernel Stack]`, and advises increasing `--stack-storage-size` when
collisions happened (`bcc/tools/profile.py:369-382`,
`bcc/tools/profile.py:398-416`, `bcc/tools/profile.py:430-451`).

`tools/offcputime.py` uses the same negative-stack convention but emphasizes
`-ENOMEM`: it counts negative non-`EFAULT` stack IDs, tracks whether any were
`-ENOMEM`, emits missed-stack placeholders, and advises increasing
`--stack-storage-size` when `-ENOMEM` was seen (`bcc/tools/offcputime.py:324-389`).

These conventions matter because a DWARF backend may fail for reasons that are
not kernel stack-map errors: missing registers, missing ELF, missing CFI,
process exit, truncated stack copy, or out-of-range stack reads. It should not
collapse those into existing stack-map `-ENOMEM` or `-EEXIST`; it should expose
a separate reason while preserving old stack-map IDs and warnings when the old
path is used.

## Build-id stack maps

Build-id stack maps are a first-class compatibility surface. `StackTrace`
iterates `bpf_stack_build_id` records and stops on `BPF_STACK_BUILD_ID_IP` or
`BPF_STACK_BUILD_ID_EMPTY` (`bcc/src/python/bcc/table.py:1161-1199`), while
`BPF.sym()` routes build-id objects through `bcc_buildsymcache_resolve()`
(`bcc/src/python/bcc/__init__.py:1682-1702`). C++ tests cover both plain stack
tables and build-id stack tables: `get_stack_table()` returns addresses and
symbols for kernel stacks, and `get_stackbuildid_table()` resolves user
build-id stacks after adding libc module paths (`bcc/tests/cc/test_bpf_table.cc:175-224`,
`bcc/tests/cc/test_bpf_table.cc:228-283`).

Git history reinforces that build IDs were not a side feature:
`2ddbc077 Add build_id support for BPF stackmap`, `9924e64e support symbol
resolution of short-lived process. (#2144)`, and `aa1b904e trace: Incorrect
symbol offsets when using build_id (#2161) (#2162)`.

## libbpf-tools C stack users

The libbpf-tools path is separate from Python BCC APIs. Local stack-using
libbpf files include `profile`, `offcputime`, `memleak`, `wakeuptime`,
`capable`, `futexctn`, `klockstat`, `biostacks`, and `opensnoop`.

`libbpf-tools/profile.bpf.c` samples kernel and user stack IDs directly with
`bpf_get_stackid(&ctx->regs, &stackmap, 0)` and `BPF_F_USER_STACK`
(`bcc/libbpf-tools/profile.bpf.c:83-92`). User space treats `-EFAULT` as
unavailable, any other negative value as an error, and `-EEXIST` as a stack-map
collision that may require larger storage (`bcc/libbpf-tools/profile.c:30-43`).
It looks up stack IDs with `bpf_map_lookup_elem()`, prints missed-stack
placeholders if lookup fails, and warns if stack traces could not be displayed
(`bcc/libbpf-tools/profile.c:395-431`, `bcc/libbpf-tools/profile.c:493-502`).

`libbpf-tools/offcputime.bpf.c` defines a `BPF_MAP_TYPE_STACK_TRACE` map and
captures previous-task user and kernel stack IDs at `sched_switch`
(`bcc/libbpf-tools/offcputime.bpf.c:33-43`,
`bcc/libbpf-tools/offcputime.bpf.c:93-101`). `libbpf-tools/opensnoop.bpf.c`
uses `bpf_get_stack()` rather than a stack-map ID to copy the first user
callers into event payload fields (`bcc/libbpf-tools/opensnoop.bpf.c:150-154`).
That distinction matters: not all current stack use is stack-ID based.

## Perf-buffer lost handling

Python and C both already have lost-event paths:

- Python `open_perf_buffer()` accepts `lost_cb`; if present, it calls the Python
  callback with the lost count (`bcc/src/python/bcc/table.py:973-1019`).
- The C perf reader prints a default warning when `PERF_RECORD_LOST` arrives
  without a callback (`bcc/src/cc/perf_reader.c:194-208`).
- libbpf-tools commonly wire lost callbacks that print lost counts, for
  example `gethostlatency.c`, `solisten.c`, `exitsnoop.c`, `statsnoop.c`,
  `tcppktlat.c`, and `tcpstates.c` as found by local `rg`.

Any DWARF path that emits larger raw-stack events must treat lost perf-buffer
events as correctness-affecting. It should not report a successful unwound
sample count without preserving lost-event accounting.

## Compatibility summary

The least disruptive BCC API shape is additive:

- Keep `BPF_STACK_TRACE`, `BPF_STACK_TRACE_BUILDID`, stack IDs, and
  `StackTrace.walk()` unchanged.
- Keep `BPF.sym()` and build-id symbol resolution unchanged.
- Add a separate optional raw-stack/DWARF event path for tools that opt in.
- Keep C++ stack-table behavior and helper-level `-EFAULT` / `-ENOMEM`
  documentation consistent with existing examples.
- Preserve negative stack-ID semantics for stack-map paths: `-EFAULT` means
  unavailable, `-ENOMEM` means capacity pressure, and `-EEXIST` means collision.
- Add new DWARF stop reasons rather than overloading those stack-map errors.
