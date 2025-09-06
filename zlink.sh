#!/usr/bin/env bash
# TSV latency sweep for Sparse 3D topologies
# - Focuses on two topologies: Sparse3D_Pillars and Sparse3D_Pillars_torus
# - Keeps XY link latency = 1
# - Sweeps Z-link (TSV) effective latency in {1,2,4}
# - Reuses base settings from prior experiments (run_topo.sh)

###############################################################################
# Basic config
###############################################################################
GEM5_EXECUTABLE="./build/NULL/gem5.opt"
GEM5_CONFIG="configs/example/garnet_synth_traffic.py"

RESULTS_DIR="lab4/sparse3d_tsv"
TEMP_DIR="${RESULTS_DIR}/tmp"
OUTPUT_CSV="${RESULTS_DIR}/results.csv"
PLOT_DIR="${RESULTS_DIR}/plots"
SIM_CYCLES=10000

# Node counts (match earlier sweeps)
NUM_CPUS=64
NUM_DIRS=64

# Concurrency: cap parallel background runs to 8
JOBS=32

# Synthetic patterns to sweep (same as earlier quick runs)
SYNTHETIC_PATTERNS=(uniform_random)

# Injection rates to sweep (0.02 -> 0.70 step 0.02)
INJECTION_RATES=$(seq 0.02 0.02 0.50)

# Keep XY link latency fixed at 1
XY_LINK_LATENCY=1

# TSV/Z-link effective latency values to sweep
Z_LATENCIES=(1 2 4)

# Two target topologies (name|extra_args)
TOPOLOGY_BASES=(
  #"Mesh3D_XYZ|--mesh-rows=4"
  "Sparse3D_Pillars|--mesh-rows=4"
  "Cluster3D_Hub|--mesh-rows=4"
  "Hier3D_Chiplet|--mesh-rows=4"
)

###############################################################################
# Safety checks
###############################################################################
set -u  # no undefined variables
rm -rf "${RESULTS_DIR}" "${TEMP_DIR}"
mkdir -p "${RESULTS_DIR}" "${TEMP_DIR}" "${PLOT_DIR}"

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
  while [[ "$(jobs -rp | wc -l | tr -d ' ')" -ge "${JOBS}" ]]; do
    sleep 0.2
  done
}

###############################################################################
# One simulation run
# Args: <topology> <topo_args> <traffic> <rate> <z_latency>
###############################################################################
run_one() {
  local topo="$1"
  local topo_args="$2"
  local traffic="$3"
  local rate="$4"
  local zlat="$5"   # desired effective Z-link latency

  # Folder-friendly rate tag: e.g., 0.010 -> 0p010
  local rate_tag
  rate_tag="$(printf "%.3f" "${rate}" | sed 's/\\.//g')"

  # Label topology with Z latency for plotting (six curves total)
  local topo_label="${topo}_Z${zlat}"

  # Output directory per run
  local OUTDIR="${TEMP_DIR}/m5out_${topo_label}_${traffic}_${rate_tag}"
  mkdir -p "${OUTDIR}"

  # Map requested Z latency to TSV controls with XY link-latency=1
  # Effective Z = link_latency * slowdown / speedup; with link_latency=1 -> Z = slowdown/speedup
  local tsv_slowdown=${zlat}
  local tsv_speedup=1

  "${GEM5_EXECUTABLE}" -d "${OUTDIR}" "${GEM5_CONFIG}" \
    --network=garnet --num-cpus="${NUM_CPUS}" --num-dirs="${NUM_DIRS}" \
    --topology="${topo}" \
    --inj-vnet=0 --synthetic="${traffic}" \
    --sim-cycles="${SIM_CYCLES}" --injectionrate="${rate}" --escape-vc --routing-algorithm=5 \
    --link-latency="${XY_LINK_LATENCY}" --tsv-slowdown="${tsv_slowdown}" --tsv-speedup="${tsv_speedup}" \
    ${topo_args} \
    > "${OUTDIR}/gem5.log" 2>&1

  # Parse stats and append to CSV (using the labeled topology)
  local STATS="${OUTDIR}/stats.txt"
  if [[ -f "${STATS}" ]]; then
    local line
    line="$(
      awk -v topology="${topo_label}" -v traffic="${traffic}" -v rate="${rate}" \
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

    if command -v flock >/dev/null 2>&1; then
      (
        flock -x 200
        echo "${line}" >> "${OUTPUT_CSV}"
      ) 200>"${LOCKFILE}"
    else
      echo "${line}" >> "${OUTPUT_CSV}"
    fi
  else
    echo "WARN: No stats.txt for ${topo_label}/${traffic} at rate ${rate} (OUTDIR=${OUTDIR})"
  fi
}

###############################################################################
# Sweep loops (parallelized)
###############################################################################
echo "Starting TSV latency sweeps for Sparse 3D topologies..."
echo "Topologies: ${#TOPOLOGY_BASES[@]} | Z-lats: ${#Z_LATENCIES[@]} | Patterns: ${#SYNTHETIC_PATTERNS[@]} | Rates: $(echo "${INJECTION_RATES}" | wc -w) | Jobs: ${JOBS}"

for entry in "${TOPOLOGY_BASES[@]}"; do
  IFS='|' read -r TOPO TOPO_ARGS <<< "${entry}"
  for zlat in "${Z_LATENCIES[@]}"; do
    for traffic in "${SYNTHETIC_PATTERNS[@]}"; do
      for rate in ${INJECTION_RATES}; do
        printf "Queue: topo=%s, Z=%s, traffic=%s, rate=%.3f\n" "${TOPO}" "${zlat}" "${traffic}" "${rate}"
        wait_for_slot
        run_one "${TOPO}" "${TOPO_ARGS}" "${traffic}" "${rate}" "${zlat}" &
      done
    done
  done
done

wait

echo "------------------------------------------------------------------------"
echo "All simulations complete. Results CSV: ${OUTPUT_CSV}"
echo "Per-run logs under: ${TEMP_DIR}/m5out_*/* (see gem5.log for details)"
echo "------------------------------------------------------------------------"
