# pdjson-zig -- build and verification entry points.
#
# The one command that matters:
#
#     make verify
#
# It runs the whole evidence pipeline from a clean checkout and fails on the
# first thing that does not hold up. Every number in README.md comes out of an
# artifact this target regenerates.

.POSIX:
.PHONY: all build verify test test-original test-zig differential fuzz bench \
        report abi abi-generate diagnose conformance safety size state-machine fmt \
        clean distclean \
        docker-verify mutation mutation-weakened release-gate claims invariants \
        hexfloat api-coverage

CC       = cc
CFLAGS   = -std=c99 -pedantic -Wall -Wextra -Wno-missing-field-initializers -O2
UPSTREAM = upstream/pdjson
BUILD    = build
ZIG      = zig
PYTHON   = python3

# Long-run knobs. `make fuzz FUZZ_SECONDS=600` for a deeper session.
FUZZ_SECONDS = 60
FUZZ_SEED    = 1
BENCH_REPS   = 5

all: build

# ---------------------------------------------------------------------- build

build: $(BUILD)/.stamp

$(BUILD)/.stamp: build.zig $(wildcard src/*.zig) $(wildcard tools/*.zig) \
                 $(wildcard oracle/*.c) $(wildcard oracle/*.h)
	@mkdir -p $(BUILD)
	@echo "==> Zig library and tools (ReleaseSafe: bounds and overflow checks on)"
	$(ZIG) build
	@echo "==> Zig tools (ReleaseFast, for the benchmark comparison only)"
	$(ZIG) build --release=fast --prefix $(BUILD)/zig-fast
	@echo "==> C reference oracle (links the pinned upstream pdjson.c)"
	$(CC) $(CFLAGS) -I $(UPSTREAM) -I oracle \
	    -o $(BUILD)/transcript_c oracle/transcript_c.c $(UPSTREAM)/pdjson.c
	$(CC) $(CFLAGS) -I $(UPSTREAM) \
	    -o $(BUILD)/bench_c oracle/bench_c.c $(UPSTREAM)/pdjson.c
	@echo "==> C oracle with ASan+UBSan (used to attribute faults, not to pass tests)"
	$(CC) -std=c99 -g -O1 -fsanitize=address,undefined -fno-omit-frame-pointer \
	    -I $(UPSTREAM) -I oracle \
	    -o $(BUILD)/transcript_c_asan oracle/transcript_c.c $(UPSTREAM)/pdjson.c
	@echo "==> ABI probe from the pinned public header"
	$(CC) -std=c11 -pedantic -Wall -Wextra -I $(UPSTREAM) \
	    -o $(BUILD)/abi_probe_c tools/abi_probe_c.c
	@touch $@

# --------------------------------------------------------------------- checks

test: test-zig

test-zig: build
	@echo "==> Zig-native test suite"
	$(ZIG) build test

test-original: build
	@echo "==> Upstream test suite, unmodified, linked against the Zig library"
	$(PYTHON) scripts/original-tests.py

abi: build
	@echo "==> C ABI layout equivalence (host, executed)"
	@sh scripts/abi-check.sh
	@echo "==> C ABI layout equivalence (cross-target, compile-time)"
	@sh scripts/abi-cross-check.sh
	@echo "==> the compile-time ABI contract can actually fail"
	@sh scripts/abi-contract-negative.sh

abi-generate:
	@echo "==> Regenerate the compile-time ABI contract from the pinned header"
	@sh scripts/abi-generate.sh

diagnose: build
	@echo "==> Target-dependent build decisions"
	@$(ZIG) build diagnose

api-coverage:
	@echo "==> Exported API behaviour coverage"
	$(PYTHON) scripts/api-coverage.py

state-machine: build
	@echo "==> State-transition specification and coverage"
	$(PYTHON) scripts/state-machine.py --self-test
	$(PYTHON) scripts/state-machine.py

hexfloat: build
	@echo "==> Hex-float correctness against an exact-integer reference"
	$(PYTHON) scripts/hexfloat_oracle.py --compare 200000 --seed 20260802

invariants: build
	@echo "==> Transcript invariants (implementation-independent)"
	$(PYTHON) scripts/invariants.py --sweep

conformance: build
	@echo "==> Fixed conformance corpus (differential)"
	$(PYTHON) scripts/differential.py --label fixed-corpus --quiet

differential: conformance

fuzz: build
	@echo "==> Differential fuzz session ($(FUZZ_SECONDS)s, seed $(FUZZ_SEED))"
	$(PYTHON) fuzz/fuzz.py --seconds $(FUZZ_SECONDS) --seed $(FUZZ_SEED) \
	    --out fuzz/logs/session-seed$(FUZZ_SEED).json

mutation: build
	@echo "==> Does the comparison notice a change in every transcript field?"
	$(PYTHON) -u scripts/mutation-test.py --self-test
	@echo "==> Mutation testing (does the harness actually catch defects?)"
	$(PYTHON) -u scripts/mutation-test.py

mutation-weakened: build
	@echo "==> The same mutants against a deliberately weakened comparison."
	@echo "    Survivors here are the point: they are what the full comparison catches."
	$(PYTHON) -u scripts/mutation-test.py --detector event-only \
	    --out artifacts/mutation-report-weakened.json

safety:
	@echo "==> Escape-hatch scan and per-occurrence inventory"
	@sh scripts/safety-scan.sh

fmt:
	@echo "==> Formatting check"
	$(ZIG) fmt --check build.zig src tools tests/port

bench: build
	@echo "==> Benchmark ($(BENCH_REPS) repetitions)"
	$(PYTHON) scripts/bench.py --repetitions $(BENCH_REPS)
	@echo "==> Artifact size: what this library costs a consumer"
	$(PYTHON) scripts/size-report.py

size: build
	@echo "==> Artifact size: what this library costs a consumer"
	$(PYTHON) scripts/size-report.py

claims:
	@echo "==> Claim ledger validation"
	$(PYTHON) scripts/validate-claims.py
	@echo "==> Claim ledger audit (the prose, not just the checks)"
	$(PYTHON) scripts/audit-claims.py

report: build
	$(PYTHON) scripts/report.py

# ------------------------------------------------------------------- pipeline

# The order is deliberate: provenance first (so nothing downstream is measuring
# a tampered baseline), then build, then the cheap invariants, then the
# expensive evidence, then the claim ledger last so it validates fresh
# artifacts rather than stale ones.
verify:
	@echo "=============================================================="
	@echo " pdjson-zig verification"
	@echo "=============================================================="
	@echo
	@echo "[1/16] tool versions"
	@sh scripts/check-tools.sh
	@echo
	@echo "[2/16] pinned upstream hashes"
	@sh scripts/verify-upstream-hashes.sh
	@echo
	@echo "[3/16] build C reference oracle and Zig library"
	@$(MAKE) --no-print-directory build
	@echo
	@echo "[4/16] the Zig artifact contains no upstream parser code"
	@sh scripts/verify-no-c-linkage.sh
	@echo
	@echo "[5/16] C ABI layout equivalence (host + 6 cross targets)"
	@sh scripts/abi-check.sh
	@sh scripts/abi-cross-check.sh
	@echo
	@echo "[5b/16] the compile-time ABI contract can fail (10 injected drifts)"
	@sh scripts/abi-contract-negative.sh
	@echo
	@echo "[5c/16] target-dependent build decisions"
	@$(ZIG) build diagnose
	@echo
	@echo "[6/16] C oracle determinism"
	@sh scripts/oracle-determinism.sh
	@echo
	@echo "[7/16] upstream test suite against the Zig library"
	@$(PYTHON) scripts/original-tests.py
	@echo
	@echo "[8/16] Zig-native test suite"
	@$(ZIG) build test
	@echo
	@echo "[9/16] fixed conformance corpus (differential)"
	@$(PYTHON) scripts/differential.py --label fixed-corpus --quiet
	@echo
	@echo "[9a/16] exported API behaviour coverage"
	@$(PYTHON) scripts/api-coverage.py
	@echo
	@echo "[9c/16] state-transition coverage against a written specification"
	@$(PYTHON) scripts/state-machine.py --self-test
	@$(PYTHON) scripts/state-machine.py
	@echo
	@echo "[9b/16] hex-float correctness against an exact-integer reference (smoke)"
	@$(PYTHON) scripts/hexfloat_oracle.py --compare 20000 --seed 20260802 \
	    --out artifacts/hex-float/property-smoke.json
	@echo
	@echo "[10/16] transcript invariants, checked without reference to either implementation"
	@$(PYTHON) scripts/invariants.py --sweep
	@echo
	@echo "[10b/16] JSONTestSuite conformance (skipped if not fetched)"
	@sh scripts/conformance-suite.sh
	@echo
	@echo "[11/16] bounded differential fuzz smoke test"
	@$(PYTHON) fuzz/fuzz.py --seconds $(FUZZ_SECONDS) --seed $(FUZZ_SEED) \
	    --out fuzz/logs/session-verify.json --quiet
	@echo
	@echo "[12/16] formatting"
	@$(ZIG) fmt --check build.zig src tools tests/port
	@echo
	@echo "[12b/16] the differential's comparison notices every transcript field"
	@$(PYTHON) scripts/mutation-test.py --self-test
	@echo
	@echo "[13/16] escape-hatch scan and per-occurrence inventory"
	@sh scripts/safety-scan.sh
	@echo
	@echo "[14/16] benchmark smoke test and artifact size"
	@$(PYTHON) scripts/bench.py --smoke
	@$(PYTHON) scripts/size-report.py
	@echo
	@echo "[15/16] generate reports"
	@$(PYTHON) scripts/report.py
	@echo
	@echo "[16/16] validate CLAIMS.json against the artifacts just produced"
	@$(PYTHON) scripts/validate-claims.py
	@$(PYTHON) scripts/audit-claims.py
	@echo
	@echo "=============================================================="
	@echo " VERIFY OK"
	@echo "=============================================================="

release-gate: verify
	@sh scripts/release-gate.sh

# --------------------------------------------------------------------- docker

docker-verify:
	docker build -t pdjson-zig-verify .
	docker run --rm pdjson-zig-verify

# ---------------------------------------------------------------------- clean

clean:
	rm -rf $(BUILD) zig-out .zig-cache fuzz/work

distclean: clean
	rm -rf tests/conformance/JSONTestSuite
