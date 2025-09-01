#!/usr/bin/env bash
# Parallel, topology-aware Garnet sweep runner
# - Supports multiple topologies (each with its own extra args)
# - Runs multiple synthetic traffic patterns
# - Parallelized with a job limit
# - Hides gem5 stdout/stderr into per-run logs
# - Appends results to a single CSV safely (flock)

###############################################################################
# Basic config
###############################################################################
GEM5_EXECUTABLE="./build/NULL/gem5.opt"
GEM5_CONFIG="configs/example/garnet_synth_traffic.py"

RESULTS_DIR="lab4/sec1"
TEMP_DIR="lab4/sec1/tmp"
OUTPUT_CSV="${RESULTS_DIR}/results.csv"
SIM_CYCLES=10000

# Node counts (adjust as needed)
NUM_CPUS=64
NUM_DIRS=64

# Concurrency (default: number of cores if available, else 4)
JOBS="$(command -v nproc >/dev/null 2>&1 && nproc || echo 4)"

# Synthetic patterns to sweep
SYNTHETIC_PATTERNS=(uniform_random) #tornado shuffle transpose neighbor)

# Injection rates to sweep (0.02 -> 0.50 step 0.02)
INJECTION_RATES=$(seq 0.02 0.02 0.50)

# Topologies to sweep. Each entry is "TOPOLOGY|EXTRA_ARGS"
# Edit/add as needed (you can include your custom ones here).
TOPOLOGY_MATRIX=(
  "Mesh3D_XYZ|--mesh-rows=4"
  "Mesh_XY|--mesh-rows=8"
  "Sparse3D_Pillars|--mesh-rows=4"
  # Example: custom 3D (adjust args to match your config)
  # "Mesh3D|--mesh-rows=4 --mesh-cols=4 --mesh-depth=4"
  # Example: your custom topology name with params
  # "SW3D_Express|--mesh-rows=8"
)

###############################################################################
# Safety checks
###############################################################################
set -u  # no undefined variables
rm -rf "${RESULTS_DIR}" "${TEMP_DIR}"
mkdir -p "${RESULTS_DIR}"
mkdir -p "${TEMP_DIR}"

if [[ ! -x "${GEM5_EXECUTABLE}" ]]; then
  echo "ERROR: gem5 executable not found/executable at: ${GEM5_EXECUTABLE}"
  exit 1
fi
if [[ ! -f "${GEM5_CONFIG}" ]]; then
  echo "ERROR: gem5 config not found at: ${GEM5_CONFIG}"
  exit 1
fi

###############################################################################
# CSV header
###############################################################################
if [[ ! -f "${OUTPUT_CSV}" ]]; then
  echo "Topology,Traffic,InjectionRate,Throughput,PacketsInjected,PacketsReceived,AvgTotalLatency,AvgHops" > "${OUTPUT_CSV}"
fi

LOCKFILE="${OUTPUT_CSV}.lock"
touch "${LOCKFILE}"

###############################################################################
# Helper: throttle to $JOBS background tasks
###############################################################################
wait_for_slot() {
  # Wait until the number of running jobs is below the limit
  while [[ "$(jobs -rp | wc -l | tr -d ' ')" -ge "${JOBS}" ]]; do
    sleep 0.2
  done
}

###############################################################################
# One simulation run
# Args: <topology> <topo_args> <traffic> <rate>
###############################################################################
run_one() {
  local topo="$1"
  local topo_args="$2"
  local traffic="$3"
  local rate="$4"

  # Create a tidy rate tag for folder names: e.g., 0.010 -> 0p010
  local rate_tag
  rate_tag="$(printf "%.3f" "${rate}" | sed 's/\./p/g')"

  # Output directory per run
  local OUTDIR="${TEMP_DIR}/m5out_${topo}_${traffic}_${rate_tag}"
  mkdir -p "${OUTDIR}"

  # Run gem5 (hide output, but keep per-run log)
  # NOTE: using ${topo_args} unquoted on purpose to allow multiple args
  "${GEM5_EXECUTABLE}" -d "${OUTDIR}" "${GEM5_CONFIG}" \
    --network=garnet --num-cpus="${NUM_CPUS}" --num-dirs="${NUM_DIRS}" \
    --topology="${topo}" ${topo_args}  \
    --inj-vnet=0 --synthetic="${traffic}" \
    --sim-cycles="${SIM_CYCLES}" --injectionrate="${rate}" --escape-vc \
    > "${OUTDIR}/gem5.log" 2>&1
  #--link-latency=2 --router-latency=2
  # Parse stats if present
  local STATS="${OUTDIR}/stats.txt"
  if [[ -f "${STATS}" ]]; then
    # Use awk to extract metrics and compute throughput = rec / cycles / NUM_CPUS
    local line
    line="$(
      awk -v topology="${topo}" -v traffic="${traffic}" -v rate="${rate}" \
          -v cycles="${SIM_CYCLES}" -v nodes="${NUM_CPUS}" '
        BEGIN{ inj=0; rec=0; t_lat=0; hops=0; }
        /system\.ruby\.network\.packets_injected::total/ { inj=$2 }
        /system\.ruby\.network\.packets_received::total/ { rec=$2 }
        /system\.ruby\.network\.average_packet_latency/  { t_lat=$2 }
        /system\.ruby\.network\.average_hops/            { hops=$2 }
        END{
          throughput = (nodes>0 && cycles>0) ? rec / cycles / nodes : 0;
          printf "%s,%s,%.3f,%.6f,%.0f,%.0f,%.4f,%.4f\n",
                 topology, traffic, rate, throughput, inj, rec, t_lat, hops
        }' "${STATS}"
    )"

    # Append to CSV atomically
    if command -v flock >/dev/null 2>&1; then
      (
        flock -x 200
        echo "${line}" >> "${OUTPUT_CSV}"
      ) 200>"${LOCKFILE}"
    else
      # Fallback (less robust without flock, but works)
      echo "${line}" >> "${OUTPUT_CSV}"
    fi
  else
    echo "WARN: No stats.txt for ${topo}/${traffic} at rate ${rate} (OUTDIR=${OUTDIR})"
  fi
}

###############################################################################
# Sweep loops (parallelized)
###############################################################################
echo "Starting gem5 simulations..."
echo "Topologies: ${#TOPOLOGY_MATRIX[@]} | Patterns: ${#SYNTHETIC_PATTERNS[@]} | Rates: $(echo "${INJECTION_RATES}" | wc -w) | Jobs: ${JOBS}"

for entry in "${TOPOLOGY_MATRIX[@]}"; do
  IFS='|' read -r TOPO TOPO_ARGS <<< "${entry}"

  for traffic in "${SYNTHETIC_PATTERNS[@]}"; do
    for rate in ${INJECTION_RATES}; do
      printf "Queue: topo=%s, traffic=%s, rate=%.3f\n" "${TOPO}" "${traffic}" "${rate}"
      wait_for_slot
      run_one "${TOPO}" "${TOPO_ARGS}" "${traffic}" "${rate}" &
    done
  done
done

# Wait for all jobs to finish
wait

echo "------------------------------------------------------------------------"
echo "All simulations complete. Results CSV: ${OUTPUT_CSV}"
echo "Per-run logs under: ${TEMP_DIR}/m5out_*/* (see gem5.log for details)"
echo "------------------------------------------------------------------------"
