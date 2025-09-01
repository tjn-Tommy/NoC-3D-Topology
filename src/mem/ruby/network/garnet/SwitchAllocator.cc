/*
 * Copyright (c) 2020 Inria
 * Copyright (c) 2016 Georgia Institute of Technology
 * Copyright (c) 2008 Princeton University
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are
 * met: redistributions of source code must retain the above copyright
 * notice, this list of conditions and the following disclaimer;
 * redistributions in binary form must reproduce the above copyright
 * notice, this list of conditions and the following disclaimer in the
 * documentation and/or other materials provided with the distribution;
 * neither the name of the copyright holders nor the names of its
 * contributors may be used to endorse or promote products derived from
 * this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
 * A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
 * OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 * SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
 * LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
 * DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 * THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */


#include "mem/ruby/network/garnet/SwitchAllocator.hh"

#include "debug/RubyNetwork.hh"
#include "mem/ruby/network/garnet/GarnetNetwork.hh"
#include "mem/ruby/network/garnet/InputUnit.hh"
#include "mem/ruby/network/garnet/OutputUnit.hh"
#include "mem/ruby/network/garnet/Router.hh"

namespace gem5
{

namespace ruby
{

namespace garnet
{

SwitchAllocator::SwitchAllocator(Router *router)
    : Consumer(router)
{
    m_router = router;
    m_num_vcs = m_router->get_num_vcs();
    m_vc_per_vnet = m_router->get_vc_per_vnet();
    m_input_arbiter_activity = 0;
    m_output_arbiter_activity = 0;
}

void
SwitchAllocator::init()
{
    m_num_inports = m_router->get_num_inports();
    m_num_outports = m_router->get_num_outports();
    m_round_robin_inport.resize(m_num_outports);
    m_round_robin_invc.resize(m_num_inports);
    m_port_requests.resize(m_num_inports);
    m_vc_winners.resize(m_num_inports);
    m_is_escape_req.resize(m_num_inports);

    for (int i = 0; i < m_num_inports; i++) {
        m_round_robin_invc[i] = 0;
        m_port_requests[i] = -1;
        m_vc_winners[i] = -1;
        m_is_escape_req[i] = false;
    }

    for (int i = 0; i < m_num_outports; i++) {
        m_round_robin_inport[i] = 0;
    }
}

/*
 * The wakeup function of the SwitchAllocator performs a 2-stage
 * seperable switch allocation. At the end of the 2nd stage, a free
 * output VC is assigned to the winning flits of each output port.
 * There is no separate VCAllocator stage like the one in garnet1.0.
 * At the end of this function, the router is rescheduled to wakeup
 * next cycle for peforming SA for any flits ready next cycle.
 */

void
SwitchAllocator::wakeup()
{
    if (is_escape_vc_enabled()) {
        arbitrate_inports_escape(); // First stage of allocation
    } else {
        arbitrate_inports(); // First stage of allocation
    }
    arbitrate_outports(); // Second stage of allocation

    clear_request_vector();
    check_for_wakeup();
}

/*
 * SA-I (or SA-i) loops through all input VCs at every input port,
 * and selects one in a round robin manner.
 *    - For HEAD/HEAD_TAIL flits only selects an input VC whose output port
 *     has at least one free output VC.
 *    - For BODY/TAIL flits, only selects an input VC that has credits
 *      in its output VC.
 * Places a request for the output port from this input VC.
 */

void
SwitchAllocator::arbitrate_inports()
{
    // Select a VC from each input in a round robin manner
    // Independent arbiter at each input port
    for (int inport = 0; inport < m_num_inports; inport++) {
        int invc = m_round_robin_invc[inport];

        for (int invc_iter = 0; invc_iter < m_num_vcs; invc_iter++) {
            auto input_unit = m_router->getInputUnit(inport);

            if (input_unit->need_stage(invc, SA_, curTick())) {
                // This flit is in SA stage

                int outport = input_unit->get_outport(invc);
                int outvc = input_unit->get_outvc(invc);

                // check if the flit in this InputVC is allowed to be sent
                // send_allowed conditions described in that function.
                bool make_request =
                    send_allowed(inport, invc, outport, outvc, is_escape_vc_enabled());

                if (make_request) {
                    m_input_arbiter_activity++;
                    m_port_requests[inport] = outport;
                    m_vc_winners[inport] = invc;

                    break; // got one vc winner for this port
                }
            }

            invc++;
            if (invc >= m_num_vcs)
                invc = 0;
        }
    }
}

void
SwitchAllocator::arbitrate_inports_escape()
{
    // Independent arbiter at each input port
    for (int inport = 0; inport < m_num_inports; inport++) {

        auto *input_unit = m_router->getInputUnit(inport);
        bool picked = false;

        // ---------------------------------------------
        // 1) Strong priority to ALL escape VCs (per vnet)
        //    Scan invc = vnet*m_vc_per_vnet + 0 for all vnets
        // ---------------------------------------------
        if (is_escape_vc_enabled()) {
            for (int invc = 0; invc < m_num_vcs; invc += m_vc_per_vnet) {
                if (!input_unit->need_stage(invc, SA_, curTick()))
                    continue;

                const PortDirection inDir_esp = m_router->getInportDirection(inport);
                flit *f_esp = input_unit->peekTopFlit(invc);
                RouteInfo r_esp = f_esp->get_route();
                int esc_outport = m_router->escape_route_compute(r_esp, inport, inDir_esp);

                bool make_request = send_allowed(inport, invc, esc_outport, -1, true);

                if (make_request) {
                    m_input_arbiter_activity++;
                    m_port_requests[inport] = esc_outport;
                    m_vc_winners[inport]    = invc;   // escape VC for this vnet
                    m_is_escape_req[inport] = true;
                    picked = true;
                    DPRINTF(RubyNetwork, "SwitchAllocator at Router %d granting escape invc %d at inport %d\n",
                            m_router->get_id(), invc, inport);
                    break;
                } else {
                    DPRINTF(RubyNetwork, "SwitchAllocator at Router %d denied escape invc %d at inport %d\n",
                            m_router->get_id(), invc, inport);
                }
            }
        }

        if (picked) continue;

        // ---------------------------------------------------
        // 2) Otherwise, round-robin among non-escape VCs (offsets 1..)
        //    Start from the saved pointer but skip VC indices that are escape
        // ---------------------------------------------------
        int start = m_round_robin_invc[inport];
        if (start % m_vc_per_vnet == 0) start++; // skip escape index for that vnet

        int invc = start;
        for (int iter = 0; iter < m_num_vcs; iter++) {
            if (invc >= m_num_vcs) invc = 0; // wrap
            if (invc % m_vc_per_vnet == 0) { invc++; continue; } // skip escape

            if (input_unit->need_stage(invc, SA_, curTick())) {
                int outport = input_unit->get_outport(invc);
                int outvc   = input_unit->get_outvc(invc);

                bool make_request =
                    send_allowed(inport, invc, outport, outvc, is_escape_vc_enabled());

                // Fallback: if HEAD with no free outVC, try routing it via escape (VC0 at next hop)
                if (!make_request && is_escape_vc_enabled() && outvc == -1) {
                    const int vnet = get_vnet(invc);
                    const PortDirection inDir = m_router->getInportDirection(inport);
                    flit *f = input_unit->peekTopFlit(invc);
                    RouteInfo r = f->get_route();
                    int esc_outport = m_router->escape_route_compute(r, inport, inDir);

                    auto *outU = m_router->getOutputUnit(esc_outport);
                    const int esc_vc = vnet * m_vc_per_vnet + 0;

                    DPRINTF(RubyNetwork, "SwitchAllocator at Router %d attempting escape "
                            "outvc %d at outport %d for invc %d at inport %d\n",
                            m_router->get_id(), esc_vc,
                            m_router->getPortDirectionName(outU->get_direction()),
                            invc,
                            m_router->getPortDirectionName(input_unit->get_direction()));

                    if (outU->is_vc_idle(esc_vc, curTick())) {
                        outport = esc_outport;
                        m_is_escape_req[inport] = true;
                        m_port_requests[inport] = outport;
                        m_vc_winners[inport]    = invc;
                        DPRINTF(RubyNetwork, "SwitchAllocator at Router %d granted escape "
                                "request for invc %d at inport %d\n",
                                m_router->get_id(), invc,
                                m_router->getPortDirectionName(input_unit->get_direction()));
                        picked = true;
                        break;
                    } else {
                        DPRINTF(RubyNetwork, "SwitchAllocator at Router %d failed escape "
                                "request for invc %d at inport %d\n",
                                m_router->get_id(), invc,
                                m_router->getPortDirectionName(input_unit->get_direction()));
                        DPRINTF(RubyNetwork,
                                "ESC-STATE Router %d outport %s esc_vc=%d state=%s credits=%d\n",
                                m_router->get_id(),
                                m_router->getPortDirectionName(outU->get_direction()),
                                esc_vc,
                                outU->is_vc_idle(esc_vc, curTick()) ? "IDLE" : "ACTIVE",
                                outU->get_credit_count(esc_vc));
                    }
                }

                if (make_request) {
                    m_input_arbiter_activity++;
                    m_port_requests[inport] = outport;
                    m_vc_winners[inport]    = invc;   // non-escape winner
                    picked = true;
                    break;
                }
            }

            invc++;
        }
    }
}




/*
void
SwitchAllocator::arbitrate_inports()
{
    // Select a VC from each input in a round robin manner
    // Independent arbiter at each input port
    for (int inport = 0; inport < m_num_inports; inport++) {
        int invc = m_round_robin_invc[inport];

        for (int invc_iter = 0; invc_iter < m_num_vcs; invc_iter++) {
            auto input_unit = m_router->getInputUnit(inport);

            if (input_unit->need_stage(invc, SA_, curTick())) {
                // This flit is in SA stage

                int outport = input_unit->get_outport(invc);
                int outvc = input_unit->get_outvc(invc);

                // check if the flit in this InputVC is allowed to be sent
                // send_allowed conditions described in that function.
                bool make_request =
                    send_allowed(inport, invc, outport, outvc, is_escape_vc_enabled());

                if (!make_request && is_escape_vc_enabled() && invc != 0) {
                    // If this is a HEAD and it failed because no free outVC,
                    // try escape: compute escape outport and request it.
                    if (outvc == -1) { // HEAD/HEAD_TAIL
                        const int vnet = get_vnet(invc);
                        const PortDirection inDir = m_router->getInportDirection(inport);
                        flit *f = input_unit->peekTopFlit(invc); // non-destructive
                        RouteInfo r = f->get_route();
                        int esc_outport = m_router->escape_route_compute(r, inport, inDir);

                        // Check that escape VC 0 on that outport is idle
                        auto outU = m_router->getOutputUnit(esc_outport);
                        const int esc_vc = vnet * m_vc_per_vnet + 0; // reserve 0

                        DPRINTF(RubyNetwork, "SwitchAllocator at Router %d "
                                             "attempting escape outvc %d at outport %d "
                                             "for invc %d at inport %d\n",
                                m_router->get_id(), esc_vc,
                                m_router->getPortDirectionName(
                                    outU->get_direction()),
                                invc,
                                m_router->getPortDirectionName(
                                    input_unit->get_direction()));

                        if (outU-> is_vc_idle(esc_vc, curTick())) {
                            m_input_arbiter_activity++;
                            outport = esc_outport;
                            make_request = true;
                            m_vc_winners[inport] = invc;
                            m_port_requests[inport] = outport;
                            m_is_escape_req[inport] = true;
                            DPRINTF(RubyNetwork, "SwitchAllocator at Router %d "
                                             "made escape request for invc %d at inport %d\n",
                                m_router->get_id(), invc,
                                m_router->getPortDirectionName(
                                    input_unit->get_direction()));
                            break;
                        } else {
                            DPRINTF(RubyNetwork, "SwitchAllocator at Router %d "
                                             "failed escape request for invc %d at inport %d\n",
                                m_router->get_id(), invc,
                                m_router->getPortDirectionName(
                                    input_unit->get_direction()));
                        }
                    }
                }

                if (make_request) {
                    m_input_arbiter_activity++;
                    m_port_requests[inport] = outport;
                    m_vc_winners[inport] = invc;

                    break; // got one vc winner for this port
                }
            }

            invc++;
            if (invc >= m_num_vcs)
                invc = 0;
        }
    }
}
*/

/*
 * SA-II (or SA-o) loops through all output ports,
 * and selects one input VC (that placed a request during SA-I)
 * as the winner for this output port in a round robin manner.
 *      - For HEAD/HEAD_TAIL flits, performs simplified outvc allocation.
 *        (i.e., select a free VC from the output port).
 *      - For BODY/TAIL flits, decrement a credit in the output vc.
 * The winning flit is read out from the input VC and sent to the
 * CrossbarSwitch.
 * An increment_credit signal is sent from the InputUnit
 * to the upstream router. For HEAD_TAIL/TAIL flits, is_free_signal in the
 * credit is set to true.
 */

void
SwitchAllocator::arbitrate_outports()
{
    // Now there are a set of input vc requests for output vcs.
    // Again do round robin arbitration on these requests
    // Independent arbiter at each output port
    for (int outport = 0; outport < m_num_outports; outport++) {
        int start_inport = m_round_robin_inport[outport];

        // First pass: give priority to escape requests targeting this outport
        int chosen_inport = -1;
        for (int iter = 0, inport = start_inport; iter < m_num_inports; iter++, inport++) {
            if (inport >= m_num_inports) inport = 0;
            if (m_port_requests[inport] == outport && m_is_escape_req[inport]) {
                chosen_inport = inport;
                break;
            }
        }

        // Second pass: if no escape requester, pick any requester (round-robin)
        if (chosen_inport == -1) {
            for (int iter = 0, inport = start_inport; iter < m_num_inports; iter++, inport++) {
                if (inport >= m_num_inports) inport = 0;
                if (m_port_requests[inport] == outport) {
                    chosen_inport = inport;
                    break;
                }
            }
        }

        if (chosen_inport == -1)
            continue; // no requests for this outport

        // Process the chosen inport
        int inport = chosen_inport;
        auto output_unit = m_router->getOutputUnit(outport);
        auto input_unit = m_router->getInputUnit(inport);

        // grant this outport to this inport
        int invc = m_vc_winners[inport];

        int outvc = input_unit->get_outvc(invc);
        if (outvc == -1) {
            if (m_is_escape_req[inport] && is_escape_vc_enabled()) {
                DPRINTF(RubyNetwork, "SwitchAllocator at Router %d granting escape VC %d at inport %d\n",
                        m_router->get_id(), invc, inport);
                outvc = m_router->getOutputUnit(outport)->set_escape_vc(get_vnet(invc));
                if (outvc == -1) {
                    // Critical: escape VC allocation failed, this should not happen
                    // if send_allowed was correct, but handle gracefully
                    DPRINTF(RubyNetwork, "CRITICAL: Router %d escape VC allocation FAILED for invc %d\n",
                            m_router->get_id(), invc);
                    // Skip this allocation and try next cycle
                    m_port_requests[inport] = -1;
                    continue;
                }
                input_unit->grant_outvc(invc, outvc);
            } else {
                outvc = vc_allocate(outport, inport, invc); // normal path
            }
        }

        // remove flit from Input VC
        flit *t_flit = input_unit->getTopFlit(invc);

        DPRINTF(RubyNetwork, "SwitchAllocator at Router %d "
                             "granted outvc %d at outport %d "
                             "to invc %d at inport %d to flit %s at "
                             "cycle: %lld\n",
                m_router->get_id(), outvc,
                m_router->getPortDirectionName(
                    output_unit->get_direction()),
                invc,
                m_router->getPortDirectionName(
                    input_unit->get_direction()),
                    *t_flit,
                m_router->curCycle());


        // Update outport field in the flit since this is
        // used by CrossbarSwitch code to send it out of
        // correct outport.
        // Note: post route compute in InputUnit,
        // outport is updated in VC, but not in flit
        t_flit->set_outport(outport);

        // set outvc (i.e., invc for next hop) in flit
        // (This was updated in VC by vc_allocate, but not in flit)
        t_flit->set_vc(outvc);

        // decrement credit in outvc
        output_unit->decrement_credit(outvc);

        // flit ready for Switch Traversal
        t_flit->advance_stage(ST_, curTick());
        m_router->grant_switch(inport, t_flit);
        m_output_arbiter_activity++;

        if ((t_flit->get_type() == TAIL_) ||
            t_flit->get_type() == HEAD_TAIL_) {

            // This Input VC should now be empty
            assert(!(input_unit->isReady(invc, curTick())));

            // Free this VC
            input_unit->set_vc_idle(invc, curTick());

            // Send a credit back
            // along with the information that this VC is now idle
            input_unit->increment_credit(invc, true, curTick());
        } else {
            // Send a credit back
            // but do not indicate that the VC is idle
            input_unit->increment_credit(invc, false, curTick());
        }

        // remove this request
        m_port_requests[inport] = -1;

        // Update Round Robin pointer
        m_round_robin_inport[outport] = inport + 1;
        if (m_round_robin_inport[outport] >= m_num_inports)
            m_round_robin_inport[outport] = 0;

        // Update Round Robin pointer to the next VC
        // We do it here to keep it fair.
        // Only the VC which got switch traversal
        // is updated.
        m_round_robin_invc[inport] = invc + 1;
        if (m_round_robin_invc[inport] >= m_num_vcs)
            m_round_robin_invc[inport] = 0;
    }
}

/*
 * A flit can be sent only if
 * (1) there is at least one free output VC at the
 *     output port (for HEAD/HEAD_TAIL),
 *  or
 * (2) if there is at least one credit (i.e., buffer slot)
 *     within the VC for BODY/TAIL flits of multi-flit packets.
 * and
 * (3) pt-to-pt ordering is not violated in ordered vnets, i.e.,
 *     there should be no other flit in this input port
 *     within an ordered vnet
 *     that arrived before this flit and is requesting the same output port.
 */

bool
SwitchAllocator::send_allowed(int inport, int invc, int outport, int outvc, bool is_escape_vc_enabled)
{
    // Check if outvc needed
    // Check if credit needed (for multi-flit packet)
    // Check if ordering violated (in ordered vnet)

    int vnet = get_vnet(invc);
    bool has_outvc = (outvc != -1);
    bool has_credit = false;

    auto output_unit = m_router->getOutputUnit(outport);
    if (!has_outvc) {
        DPRINTF(RubyNetwork, "Router %d SwitchAllocator::send_allowed for invc %d needs outvc for flit\n",
                m_router->get_id(), invc);

        // needs outvc
        // this is only true for HEAD and HEAD_TAIL flits.
        if (is_escape_vc_enabled && invc % m_vc_per_vnet == 0) {
            // For escape VCs, require the escape VC be IDLE before use.
            // Do NOT allow chaining onto an ACTIVE escape VC; that breaks
            // exclusive ownership and can cause deadlock/HOL blocking.
            if (output_unit->has_free_escape_vc(vnet)) {
                has_outvc = true;
                has_credit = true; // VC will have at least one buffer
            }
        } else {
            if (output_unit->has_free_vc(vnet)) {

                has_outvc = true;

                // each VC has at least one buffer,
                // so no need for additional credit check
                has_credit = true;
            }
        }

    } else {
        has_credit = output_unit->has_credit(outvc);
    }

    // cannot send if no outvc or no credit.
    if (!has_outvc || !has_credit) {
        // For escape VCs, add more detailed logging
        if (is_escape_vc_enabled && invc % m_vc_per_vnet == 0) {
            int esc_vc = vnet * m_vc_per_vnet + 0;
            DPRINTF(RubyNetwork, "Router %d ESCAPE VC BLOCKED: invc=%d outport=%d has_outvc=%s has_credit=%s esc_vc_credits=%d\n",
                    m_router->get_id(), invc, outport, has_outvc ? "yes" : "no", has_credit ? "yes" : "no",
                    output_unit->get_credit_count(esc_vc));
        }
        return false;
    }


    // protocol ordering check
    if ((m_router->get_net_ptr())->isVNetOrdered(vnet)) {
        auto input_unit = m_router->getInputUnit(inport);

        // enqueue time of this flit
        Tick t_enqueue_time = input_unit->get_enqueue_time(invc);

        // check if any other flit is ready for SA and for same output port
        // and was enqueued before this flit
        int vc_base = vnet*m_vc_per_vnet;
        for (int vc_offset = 0; vc_offset < m_vc_per_vnet; vc_offset++) {
            int temp_vc = vc_base + vc_offset;
            if (input_unit->need_stage(temp_vc, SA_, curTick()) &&
               (input_unit->get_outport(temp_vc) == outport) &&
               (input_unit->get_enqueue_time(temp_vc) < t_enqueue_time)) {
                return false;
            }
        }
    }

    return true;
}

// Assign a free VC to the winner of the output port.
int
SwitchAllocator::vc_allocate(int outport, int inport, int invc)
{
    // Select a free VC from the output port

    int outvc = -1;
    if (is_escape_vc_enabled() && invc % m_vc_per_vnet == 0){
        DPRINTF(RubyNetwork, "Escape VC allocation should not be performed");
        assert(false);
    } else {
        outvc = m_router->getOutputUnit(outport)->select_free_vc(get_vnet(invc));
    }

    // has to get a valid VC since it checked before performing SA
    assert(outvc != -1);
    m_router->getInputUnit(inport)->grant_outvc(invc, outvc);
    return outvc;
}

// Wakeup the router next cycle to perform SA again
// if there are flits ready.
void
SwitchAllocator::check_for_wakeup()
{
    Tick nextCycle = m_router->clockEdge(Cycles(1));

    if (m_router->alreadyScheduled(nextCycle)) {
        return;
    }

    for (int i = 0; i < m_num_inports; i++) {
        for (int j = 0; j < m_num_vcs; j++) {
            if (m_router->getInputUnit(i)->need_stage(j, SA_, nextCycle)) {
                m_router->schedule_wakeup(Cycles(1));
                return;
            }
        }
    }
}

int
SwitchAllocator::get_vnet(int invc)
{
    int vnet = invc/m_vc_per_vnet;
    assert(vnet < m_router->get_num_vnets());
    return vnet;
}

bool
SwitchAllocator::is_escape_vc_enabled() const
{
    return m_router->is_escape_vc_enabled();
}

// Clear the request vector within the allocator at end of SA-II.
// Was populated by SA-I.
void
SwitchAllocator::clear_request_vector()
{
    std::fill(m_port_requests.begin(), m_port_requests.end(), -1);
    std::fill(m_is_escape_req.begin(), m_is_escape_req.end(), false);
}

void
SwitchAllocator::resetStats()
{
    m_input_arbiter_activity = 0;
    m_output_arbiter_activity = 0;
}

} // namespace garnet
} // namespace ruby
} // namespace gem5
