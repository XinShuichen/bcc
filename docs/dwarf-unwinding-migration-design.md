# DWARF unwinding migration design

This design proposes an upstream-oriented way to integrate libgunwinder into
BCC while minimizing Python script changes and preserving the existing
stack-map contract.

## Goals

- Add optional DWARF-based user-stack unwinding for selected BCC tools.
- Preserve existing Python `BPF_STACK_TRACE` / `StackTrace.walk()` behavior.
- Preserve current stack-map error semantics and perf-buffer lost handling.
- Keep libgunwinder usable by Continue Profiling Agent (CPA).
- Make the initial upstream PR set reviewable under BCC's scope,
  compatibility, overhead, and documentation expectations.

## Non-goals

- Do not replace kernel stack helpers.
- Do not rewrite all Python tools.
- Do not import CPA's runtime model into BCC.
- Do not make libgunwinder a required BCC dependency.
- Do not change `trace_fields()` or trace-pipe behavior.

## Existing libgunwinder interface

The public libgunwinder API is already close to what BCC needs for a user-space
backend:

- `gu_init()` / `gu_cleanup()` create and destroy an unwinder context
  (`public-libgunwinder/include/gunwinder/unwinder.h:55-66`).
- `gu_unwind()` consumes a caller-owned `struct gu_stack_info` snapshot and
  invokes a frame callback; it returns the number of frames or a negative error,
  with the final reason encoded in `info->flags`
  (`public-libgunwinder/include/gunwinder/unwinder.h:68-84`).
- `struct gu_stack_info` carries PID, stack bytes, process unique ID, register
  block, flags, optional frame-pointer stack, and raw stack data
  (`public-libgunwinder/include/gunwinder/unwinder_types.h:65-94`).
- `struct gu_frame_record` carries normalized PC, absolute PC, symbol, offset,
  ELF metadata, and frame flags (`public-libgunwinder/include/gunwinder/unwinder_types.h:33-49`).
- Stop reasons include missing regs/ELF/CFI, CFI failures, process exit,
  truncation, no executable PC, stack read out of range, and unknown
  (`public-libgunwinder/include/gunwinder/unwinder_types.h:146-204`).
- `gu_preload_pid_debug_info()`, PID-private metadata, process-exit/module
  events, and kernel symbol lookup are already public
  (`public-libgunwinder/include/gunwinder/unwinder.h:87-155`).

The implementation keeps PID and ELF caches, a retired-ELF timer, kernel
symbols, and statistics in `struct gu_context`
(`public-libgunwinder/src/gu_stacktrace.c:63-87`). PID lifecycle events are
available through a registered callback list
(`public-libgunwinder/include/gunwinder/unwinder.h:19-52`,
`public-libgunwinder/src/gu_pid_ctx_event.c:13-75`).

## Required libgunwinder changes

For BCC, libgunwinder should grow a small compatibility wrapper rather than
forcing BCC to understand CPA details:

1. Add a stable C result object for one unwound sample:
   frame array, frame count, stop reason, flags, and optional diagnostic string.
   BCC should not have to build stack-map text inside a callback.
2. Add an explicit symbolization mode:
   raw address, symbol only, symbol plus module, symbol plus offset, and
   build-id suffix. BCC tools already vary these choices; CPA currently formats
   build IDs and full paths in its callback (`public-continue-profiling-agent/src/cpa_monitor/cpa_unwinder.c:592-712`).
3. Add a public stack-snapshot helper struct with fixed ABI fields for BCC's
   perf event payload. BCC should be able to convert event bytes to
   `gu_stack_info` without depending on CPA's `struct stack_event`.
4. Add a no-background-thread mode or document the existing retire-thread
   lifecycle. BCC command-line tools are short-lived; reviewers will ask how
   the context cleans up on all paths.
5. Add architecture capability queries: supported register layout, required
   `pt_regs` size, and whether frame-pointer hints are useful. BCC core rules
   require architecture-specific code to be guarded
   (`bcc/.github/instructions/core.instructions.md:13-18`).
6. Keep CPA source compatibility. Existing CPA calls `gu_init()`,
   `gu_preload_pid_debug_info()`, `gu_get_statistics()`, `gu_unwind()`,
   `gu_event_occur()`, and `gu_search_kernel_symbol()`
   (`public-continue-profiling-agent/src/cpa_monitor/cpa_unwinder.c:341-414`,
   `public-continue-profiling-agent/src/cpa_monitor/cpa_unwinder.c:786-795`,
   `public-continue-profiling-agent/src/cpa_monitor/cpa_unwinder.c:926-987`).
   These functions should remain ABI-stable.

## BCC-side API shape

Add an optional internal C++ service in `src/cc`:

```c++
class DwarfUnwinder {
 public:
  static bool supported();
  StatusTuple init(const DwarfUnwindOptions &opts);
  StatusTuple preload_pid(pid_t pid);
  StatusTuple notify_pid_exit(pid_t pid);
  StatusTuple unwind(const DwarfStackSample &sample,
                     std::vector<DwarfFrame> *frames,
                     DwarfUnwindResult *result);
};
```

Expose a narrow Python API through ctypes only after the C++ wrapper is tested:

```python
bcc_unwinder = b.get_dwarf_unwinder(options)
frames, result = bcc_unwinder.unwind_event(event)
```

Do not change `StackTrace.walk()`. Tools that use stack maps should keep using
the old iterator. Tools that opt into DWARF should add a separate event payload
and call the new unwinder only when `--dwarf` or an equivalent option is set.

The generated BPF helper for Python tools should be a new template, not a
replacement for `BPF_STACK_TRACE`. It should capture:

- PID/TGID and comm.
- Process unique ID when runtime field detection can read it; otherwise pass
  zero and let libgunwinder fall back to PID-only cache behavior.
- Raw user stack bytes, bounded by a user option.
- Register snapshot.
- Optional frame-pointer stack from `bpf_get_stack(..., BPF_F_USER_STACK)`.
- Kernel stack ID or kernel stack addresses through existing helper paths.
- Perf-event timestamp and CPU for lost-event/ordering accounting.

CPA's BPF capture path is a useful reference. It copies bounded user stack
pages with `bpf_probe_read_user()` (`public-continue-profiling-agent/bpf/src/stack_capture/stack_capture.bpf.c:74-109`),
captures kernel stack addresses with `bpf_get_stack()`
(`public-continue-profiling-agent/bpf/src/stack_capture/stack_capture.bpf.c:111-119`),
captures frame-pointer stack conditionally
(`public-continue-profiling-agent/bpf/src/stack_capture/stack_capture.bpf.c:258-271`),
copies registers from task stack tail, reads user stack bytes, and submits a
single perf event containing metadata plus stack bytes
(`public-continue-profiling-agent/bpf/src/stack_capture/stack_capture.bpf.c:273-301`).
BCC should borrow the data model, not the entire worker runtime.

For `profile.py`, the first migration target, the DWARF path must be a separate
collection mode. The existing default path aggregates by stack ID in BPF maps
and prints after sleeping; a DWARF path needs raw user-stack bytes and register
state for each sample, so it cannot reuse the same `counts` key as if nothing
changed. A reviewable design is:

- Default mode remains the existing BPF stack-map aggregation.
- `--dwarf` opens a perf buffer for bounded raw-stack samples, registers a
  `lost_cb`, unwinds each event in Python/C++ user space, then aggregates by
  the formatted frame tuple plus comm/PID before printing the same output
  shape where possible.
- The DWARF mode must account for both perf-buffer lost events and per-sample
  unwind stop reasons in the final warning. It should report those separately
  from stack-map `-ENOMEM`/`-EEXIST` warnings because the failure domains are
  different.
- The mode needs conservative defaults for stack-copy size and perf-buffer
  pages, plus documentation that high-frequency sampling can drop events or
  increase CPU time due to raw stack copying and ELF/CFI work.
- If folded output is supported in the first PR, it should be generated from
  the same user-space aggregate. If exact parity with every `profile.py` output
  option is not ready, the first PR should reject unsupported option
  combinations with a clear error instead of producing subtly different data.

## Preserving CPA

CPA is not just a libgunwinder caller. It has BPF and perf capture backends,
drop-pressure policy, pause/restart lifecycle, PID-exit queues, metadata
formatting, and kernel-only fast paths:

- BPF capture builds `gu_stack_info` from event payload, including raw stack
  data, regs, frame-pointer stack, PID, and unique ID
  (`public-continue-profiling-agent/src/cpa_monitor/cpa_bpf_capture.c:243-291`).
- The event processor validates payload size, records BPF execution time,
  handles kernel-only fast path, drop pressure, and queues samples
  (`public-continue-profiling-agent/src/cpa_monitor/cpa_bpf_capture.c:347-407`).
- Perf capture builds similar samples without BPF CO-RE stack-copy code
  (`public-continue-profiling-agent/src/cpa_monitor/cpa_perf_capture.c:34-103`).
- The unwinder worker initializes libgunwinder, preloads PID debug info, owns
  queues, and tears down the context on pause/destroy
  (`public-continue-profiling-agent/src/cpa_monitor/cpa_unwinder.c:341-441`,
  `public-continue-profiling-agent/src/cpa_monitor/cpa_unwinder.c:468-515`).
- The final unwind flow chooses DWARF vs frame-pointer, records benchmark
  timing, appends stop reasons, resolves kernel symbols, and emits completed
  stackmap entries (`public-continue-profiling-agent/src/cpa_monitor/cpa_unwinder.c:886-994`).

Therefore, libgunwinder changes for BCC must be additive. Do not remove
callbacks, flags, private PID metadata, process-exit events, kernel symbol
lookup, or statistics.

## Migration categories

Start with one tool category:

1. `tools/profile.py`: best first target. It already has user/kernel stack
   selection, sampling frequency/period, stack storage sizing, folded output,
   address output, missed-stack warnings, and perf-event attachment
   (`bcc/tools/profile.py:98-135`, `bcc/tools/profile.py:274-337`,
   `bcc/tools/profile.py:368-389`).
2. `tools/offcputime.py` / `tools/offwaketime.py`: second wave. They are
   latency/duration aggregators and already handle missed stacks, but off-CPU
   register/stack timing is harder to explain and test.
3. `tools/trace.py`: defer. It dynamically generates BPF C and event structs
   from user probe specs (`bcc/tools/trace.py:343-539`), so adding raw-stack
   payloads there creates verifier, output, and option-combination risk.
4. libbpf-tools: defer or handle separately. They do not use Python BCC APIs;
   `profile.bpf.c` and `profile.c` call libbpf/BPF helpers directly
   (`bcc/libbpf-tools/profile.bpf.c:83-92`,
   `bcc/libbpf-tools/profile.c:395-502`).
5. Event tools: migrate only when there is a specific production problem that
   frame-pointer/build-id stack maps cannot solve.

## Compatibility and fallback

The default path remains current stack maps. DWARF is opt-in and disabled when:

- libgunwinder is not found at build time.
- The running architecture is unsupported.
- Required helpers are unavailable.
- Raw-stack perf events would exceed configured limits.
- The tool requests kernel-only stacks.

Fallback behavior:

- If DWARF setup fails before attach, print a clear warning and use existing
  stack-map unwinding unless the user explicitly requested `--dwarf=required`.
- If individual samples fail, emit a DWARF stop reason and continue.
- If raw-stack perf-buffer lost counts are non-zero, the final summary must
  say that sampled events were dropped and therefore the aggregate is
  incomplete.
- Preserve stack-map `-EFAULT`, `-ENOMEM`, and `-EEXIST` reporting on the
  stack-map path. Do not map DWARF `NO_CFI` or `TRUNCATED` to those values.
- Preserve perf-buffer lost accounting. For raw-stack events, pass `lost_cb`
  and include lost counts in final warnings; otherwise BCC's C reader will only
  print the default warning (`bcc/src/python/bcc/table.py:973-1019`,
  `bcc/src/cc/perf_reader.c:194-208`).

## Tests

Minimum upstream gate:

- `tests/cc`: wrapper initialization, unsupported dependency path, sample
  conversion validation, PID-exit notification, and stop-reason mapping.
- `tests/python`: retain existing `test_stackid.py` behavior for plain and
  build-id stack maps (`bcc/tests/python/test_stackid.py:12-80`); add opt-in
  DWARF tests guarded by architecture/helper/dependency checks.
- Tool smoke: add a `profile.py --dwarf` smoke test only if libgunwinder is
  enabled, and keep the existing non-DWARF smoke path.
- Workload tests: a small C program compiled with frame pointers disabled and
  DWARF CFI available, plus a stripped/separate-debug build-id variant.
- Lost-event tests: force small perf-buffer page count or oversized samples and
  verify lost accounting is surfaced.
- Regression tests for `-EFAULT`, `-ENOMEM`, and `-EEXIST` warnings on the
  old stack-map path.

## Commit plan

1. `docs:` add design, API report, and review-style report.
2. `build:` optional libgunwinder detection, default disabled, with packaging
   notes and no behavior change.
3. `src/cc:` add internal wrapper and tests; no Python API yet.
4. `src/python:` expose a small opt-in unwinder object and conversion helpers.
5. `tests/python:` add guarded DWARF API tests and preserve stack-map tests.
6. `tools/profile:` add `--dwarf` / `--dwarf=required` and documentation,
   keeping default output unchanged.
7. `man/man8` and examples: update profile overhead/caveats and example output.
8. Follow-up PRs: off-CPU tools, dynamic `trace.py`, and libbpf-tools only
   after the first migration is accepted.

## Review gate checklist

- [ ] Default BCC behavior unchanged when DWARF is not requested.
- [ ] libgunwinder dependency optional and package/build changes documented.
- [ ] Public C++/Python APIs either unchanged or added without breaking old
      callers.
- [ ] Architecture-specific register code guarded.
- [ ] Lost perf-buffer events reported.
- [ ] Stack-map `-EFAULT`, `-ENOMEM`, and `-EEXIST` behavior preserved.
- [ ] DWARF stop reasons visible without overloading stack-map errors.
- [ ] Raw stack-copy size bounded and documented as overhead.
- [ ] Tool man page includes `OVERHEAD` and caveats.
- [ ] Smoke and workload tests cover both fallback and DWARF paths.
