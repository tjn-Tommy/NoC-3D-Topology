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

RESULTS_DIR="lab4/sec2"
TEMP_DIR="lab4/sec2/tmp"
OUTPUT_CSV="${RESULTS_DIR}/results.csv"
SIM_CYCLES=50000

# TSV latency controls (Z-link timing):
# Effective Z latency = link_latency * TSV_SLOWDOWN / TSV_SPEEDUP
# Override via environment variables if desired.
TSV_SLOWDOWN=${TSV_SLOWDOWN:-4}
TSV_SPEEDUP=${TSV_SPEEDUP:-1}

# Node counts (adjust as needed)
NUM_CPUS=64
NUM_DIRS=64

# Concurrency (default: number of cores if available, else 4)
JOBS="$(command -v nproc >/dev/null 2>&1 && nproc || echo 4)"

# Synthetic patterns to sweep
SYNTHETIC_PATTERNS=(uniform_random transpose neighbor) #tornado shuffle 

# Injection rates to sweep (0.02 -> 0.50 step 0.02)
INJECTION_RATES=$(seq 0.02 0.02 0.50)

# Topologies to sweep. Each entry is "TOPOLOGY|EXTRA_ARGS"
# Edit/add as needed (you can include your custom ones here).
TOPOLOGY_MATRIX=(
  # 2D baseline (no TSV settings needed)
  #"Mesh_XY|--mesh-rows=8"

  # 3D topologies with per-topology TSV settings (edit here to tailor)
  "Mesh3D_XYZ|--mesh-rows=4 --tsv-slowdown=4 --tsv-speedup=4"
  # "Torus3D|--mesh-rows=4 --tsv-slowdown=4 --tsv-speedup=1"
  #"Sparse3D_Pillars|--mesh-rows=4 --tsv-slowdown=4 --tsv-speedup=4"
  #"Sparse3D_Pillars_torus|--mesh-rows=4 --tsv-slowdown=4 --tsv-speedup=4"
  "Cluster3D_Hub|--mesh-rows=4 --tsv-slowdown=4 --tsv-speedup=4"
  #"Hier3D_ClusterHub|--mesh-rows=4 --tsv-slowdown=4 --tsv-speedup=4"
  # "SW3D_Express|--mesh-rows=4 --tsv-slowdown=4 --tsv-speedup=4"
  #"Hier3D_Chiplet|--mesh-rows=4 --tsv-slowdown=4 --tsv-speedup=4"
  # "PillarTorusExpress3D|--mesh-rows=4 --tsv-slowdown=4 --tsv-speedup=4"
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
  rate_tag="$(printf "%.3f" "${rate}" | sed 's/\\.//g')"

  # Output directory per run
  local OUTDIR="${TEMP_DIR}/m5out_${topo}_${traffic}_${rate_tag}"
  mkdir -p "${OUTDIR}"

  # Run gem5 (hide output, but keep per-run log)
  # NOTE: using ${topo_args} unquoted on purpose to allow multiple args
  # Per-topology TSV overrides via environment:
  #   export TSV_SLOWDOWN_Mesh3D_XYZ=6 TSV_SPEEDUP_Mesh3D_XYZ=1
  #   export TSV_SLOWDOWN_Torus3D=4 TSV_SPEEDUP_Torus3D=2
  # Any --tsv-* provided in topo_args override these defaults.
  local topo_key
  topo_key="$(echo "${topo}" | tr -c 'A-Za-z0-9' '_')"
  local tsv_slow_topo tsv_fast_topo
  eval "tsv_slow_topo=\${TSV_SLOWDOWN_${topo_key}:-${TSV_SLOWDOWN}}"
  eval "tsv_fast_topo=\${TSV_SPEEDUP_${topo_key}:-${TSV_SPEEDUP}}"
  "${GEM5_EXECUTABLE}" -d "${OUTDIR}" "${GEM5_CONFIG}" \
    --network=garnet --num-cpus="${NUM_CPUS}" --num-dirs="${NUM_DIRS}" \
    --topology="${topo}" --vcs-per-vnet=4 \
    --inj-vnet=0 --synthetic="${traffic}" \
    --sim-cycles="${SIM_CYCLES}" --injectionrate="${rate}" --escape-vc --routing-algorithm=4 \
    --tsv-slowdown="${tsv_slow_topo}" --tsv-speedup="${tsv_fast_topo}" \
    ${topo_args} \
    > "${OUTDIR}/gem5.log" 2>&1
    #--escape-vc
  #--link-latency=2 --router-latency=2
  # Parse stats if present
  local STATS="${OUTDIR}/stats.txt"
  if [[ -f "${STATS}" ]]; then
    # Use awk to extract metrics and compute throughput = rec / cycles / NUM_CPUS
    local line
    line="$(
      awk -v topology="${topo}" -v traffic="${traffic}" -v rate="${rate}" \
          -v cycles="${SIM_CYCLES}" -v nodes="${NUM_CPUS}" \
          '\
        BEGIN{ inj=0; rec=0; t_lat=0; hops=0; }\
        /system\.ruby\.network\.packets_injected::total/ { inj=$2 }\
        /system\.ruby\.network\.packets_received::total/ { rec=$2 }\
        /system\.ruby\.network\.average_packet_latency/  { t_lat=$2 }\
        /system\.ruby\.network\.average_hops/            { hops=$2 }\
        END {\
          throughput = (nodes>0 && cycles>0) ? rec / cycles / nodes : 0;\
          printf "%s,%s,%.3f,%.6f,%.0f,%.0f,%.4f,%.4f\n",\
                 topology, traffic, rate, throughput, inj, rec, t_lat, hops\
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
