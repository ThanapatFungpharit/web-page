# Interpreter benchmark

Run from the repository root:

```powershell
.venv/Scripts/python.exe benchmarks/benchmark_interpreter.py --runs 200 --repeats 5
```

Add `--profile` for a cumulative-time profile of the flat action workload.

The script measures warmed interpreter overhead, using the median of five batches
after ten warm-up calls. It loads manifests through the public API during setup
and uses a page stub with immediate async methods. YAML loading, browser
communication, DOM operations and network latency are excluded. Each action run
validates its new parameter values, acquires its lock, and dispatches its steps;
successful static dependency linking is reused.

## Comparing dependency-plan reuse

```powershell
.venv/Scripts/python.exe benchmarks/benchmark_interpreter.py --runs 200 --repeats 7 --compare-linking
```

This alternates production and control batches in the same process. The control
deliberately discards dependency links on every action entry, while retaining
load-time template compilation and action metadata. It isolates the benefit of
reusing dependency plans; it is not a comparison against the original interpreter.
The template and membership workloads act as controls and should have similar
timings on both sides.

Measured on this Windows workspace on 2026-10-17:

| Workload | Relink every call | Reuse linked plan | Speedup |
| --- | ---: | ---: | ---: |
| Flat action with 50 fill steps | 557.84 µs | 389.32 µs | 1.43× |
| Repeat group with 50 fill iterations | 428.24 µs | 435.63 µs | ~1.0× |
| Parent action making 20 child calls | 1,234.46 µs | 482.75 µs | 2.56× |

The repeat workload has only one nested step to validate, so link reuse contributes
little to its overall cost. System load affects timings; the alternating control
is more useful for evaluating plan reuse than comparing separate invocations.

## Initial cache optimization measurements

These historical measurements preceded load-time binding and retained action plans.
They use 200 runs per batch and five batches:

| Workload | Before | After | Speedup |
| --- | ---: | ---: | ---: |
| 100 template fills, mixing literal and escaped param/result templates | 270.08 µs | 91.09 µs | 3.0× |
| Two selector membership checks in a 1,000-key manifest | 5,494.58 µs | 1.14 µs | ~4,820× |
| Flat action with 50 fill steps | 752.15 µs | 260.45 µs | 2.9× |
| Repeat group with 50 fill iterations | 495.77 µs | 195.02 µs | 2.5× |
| Parent action making 20 child calls | 1,008.02 µs | 655.03 µs | 1.5× |

The baseline was the existing working tree immediately before the template
optimization, including its uncommitted changes. Timing varies with system load.
These ratios describe interpreter overhead and do not predict total browser action
speedups. The membership improvement comes from checking the requested key and its
references directly; its cost no longer scales with the number of other selectors
for a dictionary-backed manifest.

Loaded steps and selectors now retain their compiled templates for their own
lifetime, independently of the 1,024-entry fallback LRU used by standalone string
helpers. Clearing or overflowing that cache does not cause loaded programs to
reparse templates. Static action metadata and repeat-group output sets are computed
at load time. Cross-manifest links are checked on first use and invalidated by
successful action/selector loads or hook registration.

Parameter values, saved results, and formatted outputs are resolved on every
access. The compiled syntax stores immutable reference sets, while the public
reference helpers return fresh sets. No native-code JIT or generated Python source
is involved.
