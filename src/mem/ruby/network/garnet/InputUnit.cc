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


#include "mem/ruby/network/garnet/InputUnit.hh"

#include "debug/RubyNetwork.hh"
#include "mem/ruby/network/garnet/Credit.hh"
#include "mem/ruby/network/garnet/Router.hh"

namespace gem5
{

namespace ruby
{

namespace garnet
{

InputUnit::InputUnit(int id, PortDirection direction, Router *router)
  : Consumer(router), m_router(router), m_id(id), m_direction(direction),
    m_vc_per_vnet(m_router->get_vc_per_vnet())
{
    const int m_num_vcs = m_router->get_num_vcs();
    m_num_buffer_reads.resize(m_num_vcs/m_vc_per_vnet);
    m_num_buffer_writes.resize(m_num_vcs/m_vc_per_vnet);
    for (int i = 0; i < m_num_buffer_reads.size(); i++) {
        m_num_buffer_reads[i] = 0;
        m_num_buffer_writes[i] = 0;
    }

    // Instantiating the virtual channels
    virtualChannels.reserve(m_num_vcs);
    for (int i=0; i < m_num_vcs; i++) {
        virtualChannels.emplace_back();
    }

    // SPIN (optional): init per-VC stall and frozen state
    m_stall_count.assign(m_num_vcs, 0);
    m_vc_frozen.assign(m_num_vcs, false);
    m_fork_vector.assign(m_router->get_num_outports(), false);
}

/*
 * The InputUnit wakeup function reads the input flit from its input link.
 * Each flit arrives with an input VC.
 * For HEAD/HEAD_TAIL flits, performs route computation,
 * and updates route in the input VC.
 * The flit is buffered for (m_latency - 1) cycles in the input VC
 * and marked as valid for SwitchAllocation starting that cycle.
 *
 */

void
InputUnit::wakeup()
{
    flit *t_flit;
    if (m_in_link->isReady(curTick())) {

        t_flit = m_in_link->consumeLink();
        DPRINTF(RubyNetwork, "Router[%d] Consuming:%s Width: %d Flit:%s\n",
        m_router->get_id(), m_in_link->name(),
        m_router->getBitWidth(), *t_flit);
        assert(t_flit->m_width == m_router->getBitWidth());
        int vc = t_flit->get_vc();
        t_flit->increment_hops(); // for stats

        // SPIN control flits handling (subset)
        if (m_router->spin_scheme_enabled()) {
            flit_type ft = t_flit->get_type();
            if (ft == PROBE_ || ft == MOVE_ || ft == CHECK_PROBE_ || ft == KILL_MOVE_) {
                // Attach current router id as inport for forwarding semantics
                t_flit->setInport(m_router->get_id());
                if (ft == PROBE_) {
                    if (t_flit->getSourceId() == m_router->get_id()) {
                        if (verify_dependence_at_source(t_flit)) {
                            m_router->set_loop_delay(Cycles(1));
                            m_router->latch_path(t_flit);
                            int move_id = m_router->send_move_msg(m_id, t_flit->getSourceVc());
                            m_router->latch_source_id_buffer(m_router->get_id(), move_id);
                            m_router->create_move_info_entry(m_id, t_flit->getSourceVc(), m_router->peek_path_top());
                            m_router->set_counter(t_flit->getSourceInport(), t_flit->getSourceVc(), s_move, 0);
                        } else {
                            m_num_probes_dropped++;
                        }
                        delete t_flit;
                        return;
                    } else {
                        // Drop if path turns exceed capacity
                        if ((unsigned)t_flit->getNumTurns() > m_router->get_net_ptr()->getSpinMaxTurnCapacity()) {
                            m_num_probes_dropped++;
                            delete t_flit;
                            return;
                        }
                        if (create_fork_vector(t_flit)) {
                            m_router->fork_probes(t_flit, m_fork_vector);
                        } else {
                            m_num_probes_dropped++;
                        }
                        clear_fork_vector();
                        delete t_flit;
                        return;
                    }
                } else if (ft == MOVE_) {
                    if (t_flit->getSourceId() == m_router->get_id()) {
                        if (verify_dependence_at_source(t_flit)) {
                            m_router->set_move_bit();
                            m_router->set_counter(m_id, t_flit->getSourceVc(), s_forward_progress, 0);
                        } else {
                            m_router->send_kill_move(m_id);
                            m_router->invalidate_path_buffer();
                            m_router->invalidate_source_id_buffer();
                            m_router->increment_counter_ptr();
                            m_router->clear_move_registry();
                            m_num_move_dropped++;
                        }
                        delete t_flit;
                        return;
                    } else {
                        Counter_state cs = m_router->get_counter_state();
                        if (!(cs == s_deadlock_detection || cs == s_off || cs == s_frozen)) {
                            m_num_move_dropped++;
                            delete t_flit; return;
                        }
                        if (cs == s_frozen && !m_router->partial_check_source_id_buffer(t_flit->getSourceId())) {
                            m_num_move_dropped++;
                            delete t_flit; return;
                        }
                        if (m_router->check_outport_entry_in_move_registry(t_flit->peekPathTop())) {
                            m_num_move_dropped++;
                            delete t_flit; return;
                        }
                        int mvc = find_move_vc(t_flit);
                        if (mvc != -1) {
                            m_router->set_move_bit();
                            m_router->latch_source_id_buffer(t_flit->getSourceId(), t_flit->get_id());
                            m_router->create_move_info_entry(m_id, mvc, t_flit->peekPathTop());
                            m_router->set_counter(m_id, mvc, s_frozen, (unsigned)(1));
                            m_router->forward_move(t_flit);
                        } else {
                            m_num_move_dropped++;
                            delete t_flit;
                        }
                        return;
                    }
                } else if (ft == CHECK_PROBE_) {
                    if (t_flit->getSourceId() == m_router->get_id()) {
                        if (verify_dependence_at_source(t_flit)) {
                            m_router->set_move_bit();
                            m_router->set_counter(m_id, t_flit->getSourceVc(), s_forward_progress, 0);
                        } else {
                            m_router->send_kill_move(m_id);
                            m_router->invalidate_path_buffer();
                            m_router->invalidate_source_id_buffer();
                            m_router->increment_counter_ptr();
                            m_router->clear_move_registry();
                            m_num_check_probe_dropped++;
                        }
                        delete t_flit;
                        return;
                    } else {
                        assert(m_router->get_counter_state() == s_frozen);
                        assert(m_router->partial_check_source_id_buffer(t_flit->getSourceId()));
                        int mvc = find_move_vc(t_flit);
                        if (mvc != -1) {
                            m_router->set_move_bit();
                            m_router->update_move_info_entry(m_id, mvc, t_flit->peekPathTop());
                            m_router->set_counter(m_id, mvc, s_frozen, (unsigned)(1));
                            m_router->forward_check_probe(t_flit);
                        } else {
                            m_num_check_probe_dropped++;
                            delete t_flit;
                        }
                        return;
                    }
                } else if (ft == KILL_MOVE_) {
                    if (t_flit->getSourceId() == m_router->get_id()) {
                        delete t_flit; return;
                    } else {
                        if (m_router->partial_check_source_id_buffer(t_flit->getSourceId())) {
                            t_flit->setMustSend(true);
                            m_router->set_kill_move_processed_this_cycle();
                            if (m_router->get_num_move_registry_entries() == 1) {
                                m_router->reset_move_bit();
                                m_router->increment_counter_ptr();
                                m_router->invalidate_source_id_buffer();
                                m_router->clear_move_registry();
                            } else {
                                m_router->invalidate_move_registry_entry(m_id, t_flit->peekPathTop());
                            }
                        } else {
                            t_flit->setMustSend(false);
                        }
                        m_router->forward_kill_move(t_flit);
                        return;
                    }
                }
            }
        }

        if ((t_flit->get_type() == HEAD_) ||
            (t_flit->get_type() == HEAD_TAIL_)) {

            assert(virtualChannels[vc].get_state() == IDLE_);
            set_vc_active(vc, curTick());

            // Route computation for this vc
            int outport = m_router->route_compute(t_flit->get_route(),
                m_id, m_direction);

            // Update output port in VC
            // All flits in this packet will use this output port
            // The output port field in the flit is updated after it wins SA
            grant_outport(vc, outport);

            // SPIN: initialize deadlock detection counter on first HEAD
            if (m_router->spin_scheme_enabled() &&
                m_router->get_counter_state() == s_off &&
                m_direction != "Local" &&
                m_router->getOutportDirection(outport) != "Local") {
                m_router->set_counter(m_id, vc, s_deadlock_detection, 0);
            }

        } else {
            assert(virtualChannels[vc].get_state() == ACTIVE_);
        }


        // Buffer the flit
        virtualChannels[vc].insertFlit(t_flit);

        int vnet = vc/m_vc_per_vnet;
        // number of writes same as reads
        // any flit that is written will be read only once
        m_num_buffer_writes[vnet]++;
        m_num_buffer_reads[vnet]++;

        Cycles pipe_stages = m_router->get_pipe_stages();
        if (pipe_stages == 1) {
            // 1-cycle router
            // Flit goes for SA directly
            t_flit->advance_stage(SA_, curTick());
        } else {
            assert(pipe_stages > 1);
            // Router delay is modeled by making flit wait in buffer for
            // (pipe_stages cycles - 1) cycles before going for SA

            Cycles wait_time = pipe_stages - Cycles(1);
            t_flit->advance_stage(SA_, m_router->clockEdge(wait_time));

            // Wakeup the router in that cycle to perform SA
            m_router->schedule_wakeup(Cycles(wait_time));
        }

        if (m_in_link->isReady(curTick())) {
            m_router->schedule_wakeup(Cycles(1));
        }
    }
}

// --- SPIN (optional) helpers ---
void
InputUnit::increment_stall(int vc)
{
    if (!m_router->get_net_ptr()->isSpinSchemeEnabled()) return;
    // Only enable freezing when an escape path is available to guarantee
    // forward progress in our simplified SPIN handling.
    if (!m_router->get_net_ptr()->isEscapeVcEnabled()) return;
    if (vc < 0 || vc >= (int)m_stall_count.size()) return;
    if (m_vc_frozen[vc]) return; // already frozen
    m_stall_count[vc]++;
    const uint32_t thresh = m_router->get_net_ptr()->getSpinDdThreshold();
    if (thresh > 0 && m_stall_count[vc] >= thresh) {
        // Freeze this VC; SwitchAllocator will attempt an escape if enabled
        m_vc_frozen[vc] = true;
        DPRINTF(RubyNetwork, "Router %d InputUnit %s freezing VC %d after %u stalls\n",
                m_router->get_id(), m_router->getPortDirectionName(get_direction()), vc, m_stall_count[vc]);
    }
}

void
InputUnit::reset_stall(int vc)
{
    if (vc < 0 || vc >= (int)m_stall_count.size()) return;
    m_stall_count[vc] = 0;
}

void
InputUnit::freeze_vc(int vc)
{
    if (!m_router->get_net_ptr()->isSpinSchemeEnabled()) return;
    if (vc < 0 || vc >= (int)m_vc_frozen.size()) return;
    m_vc_frozen[vc] = true;
}

void
InputUnit::thaw_vc(int vc)
{
    if (vc < 0 || vc >= (int)m_vc_frozen.size()) return;
    if (m_vc_frozen[vc]) {
        DPRINTF(RubyNetwork, "Router %d InputUnit %s thaw VC %d\n",
                m_router->get_id(), m_router->getPortDirectionName(get_direction()), vc);
    }
    m_vc_frozen[vc] = false;
    reset_stall(vc);
}

bool
InputUnit::is_vc_frozen(int vc) const
{
    if (vc < 0 || vc >= (int)m_vc_frozen.size()) return false;
    return m_vc_frozen[vc];
}

// Send a credit back to upstream router for this VC.
// Called by SwitchAllocator when the flit in this VC wins the Switch.
void
InputUnit::increment_credit(int in_vc, bool free_signal, Tick curTime)
{
    DPRINTF(RubyNetwork, "Router[%d]: Sending a credit vc:%d free:%d to %s\n",
    m_router->get_id(), in_vc, free_signal, m_credit_link->name());
    Credit *t_credit = new Credit(in_vc, free_signal, curTime);
    creditQueue.insert(t_credit);
    m_credit_link->scheduleEventAbsolute(m_router->clockEdge(Cycles(1)));
}

bool
InputUnit::functionalRead(Packet *pkt, WriteMask &mask)
{
    bool read = false;
    for (auto& virtual_channel : virtualChannels) {
        if (virtual_channel.functionalRead(pkt, mask))
            read = true;
    }

    return read;
}

uint32_t
InputUnit::functionalWrite(Packet *pkt)
{
    uint32_t num_functional_writes = 0;
    for (auto& virtual_channel : virtualChannels) {
        num_functional_writes += virtual_channel.functionalWrite(pkt);
    }

    return num_functional_writes;
}

void
InputUnit::resetStats()
{
    for (int j = 0; j < m_num_buffer_reads.size(); j++) {
        m_num_buffer_reads[j] = 0;
        m_num_buffer_writes[j] = 0;
    }
}

// --- SPIN helpers (subset) ---
bool InputUnit::verify_dependence_at_source(flit *t_flit)
{
    // True if the VC’s currently latched outport matches the path’s expected next outport.
    int vc = t_flit->getSourceVc();
    if (vc < 0) return false;
    int expected = -1;
    // For PROBE, compare against path top; for MOVE/CHECK_PROBE, compare against router’s path buffer later
    expected = t_flit->peekPathTop();
    return (virtualChannels[vc].get_outport() == expected);
}

bool InputUnit::create_fork_vector(flit *t_flit)
{
    // For this inport’s vnet, mark all outports where a VC is ACTIVE and not Local.
    int vnet = t_flit->get_vnet();
    std::fill(m_fork_vector.begin(), m_fork_vector.end(), false);
    int base = vnet * m_vc_per_vnet;
    int end = base + m_vc_per_vnet;
    bool any = false;
    for (int i = base; i < end; ++i) {
        if (virtualChannels[i].get_state() != ACTIVE_) return false;
        int outp = virtualChannels[i].get_outport();
        if (m_router->getOutportDirection(outp) == "Local") return false;
        m_fork_vector[outp] = true;
        any = true;
    }
    return any;
}

void InputUnit::clear_fork_vector()
{
    std::fill(m_fork_vector.begin(), m_fork_vector.end(), false);
}

int InputUnit::find_move_vc(flit *t_flit)
{
    // Choose a VC (in this vnet) whose outport matches the path top and contains head+tail
    int vnet = t_flit->get_vnet();
    int base = vnet * m_vc_per_vnet;
    int end = base + m_vc_per_vnet;
    int outp = t_flit->peekPathTop();
    for (int i = base; i < end; ++i) {
        if (virtualChannels[i].get_state() != ACTIVE_) return -1;
        if (m_router->getOutportDirection(virtualChannels[i].get_outport()) == "Local") return -1;
        if (virtualChannels[i].get_outport() == outp && virtualChannels[i].containsHeadAndTail())
            return i;
    }
    return -1;
}

void InputUnit::reset_spin_stats()
{
    m_num_probes_dropped = 0;
    m_num_move_dropped = 0;
    m_num_check_probe_dropped = 0;
}

} // namespace garnet
} // namespace ruby
} // namespace gem5
