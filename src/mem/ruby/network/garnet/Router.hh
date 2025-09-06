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


#ifndef __MEM_RUBY_NETWORK_GARNET_0_ROUTER_HH__
#define __MEM_RUBY_NETWORK_GARNET_0_ROUTER_HH__

#include <iostream>
#include <memory>
#include <queue>
#include <vector>

#include "mem/ruby/common/Consumer.hh"
#include "mem/ruby/common/NetDest.hh"
#include "mem/ruby/network/BasicRouter.hh"
#include "mem/ruby/network/garnet/CommonTypes.hh"
#include "mem/ruby/network/garnet/CrossbarSwitch.hh"
#include "mem/ruby/network/garnet/GarnetNetwork.hh"
#include "mem/ruby/network/garnet/RoutingUnit.hh"
#include "mem/ruby/network/garnet/SwitchAllocator.hh"
#include "mem/ruby/network/garnet/flit.hh"
#include "params/GarnetRouter.hh"

namespace gem5
{

namespace ruby
{

class FaultModel;

namespace garnet
{

class NetworkLink;
class CreditLink;
class InputUnit;
class OutputUnit;
class flitBuffer;

class Router : public BasicRouter, public Consumer
{
  public:
    typedef GarnetRouterParams Params;
    Router(const Params &p);

    ~Router() = default;

    void wakeup();
    void print(std::ostream& out) const {};

    void init();
    void addInPort(PortDirection inport_dirn, NetworkLink *link,
                   CreditLink *credit_link);
    void addOutPort(PortDirection outport_dirn, NetworkLink *link,
                    std::vector<NetDest>& routing_table_entry,
                    int link_weight, CreditLink *credit_link,
                    uint32_t consumerVcs);

    Cycles get_pipe_stages(){ return m_latency; }
    uint32_t get_num_vcs()       { return m_num_vcs; }
    uint32_t get_num_vnets()     { return m_virtual_networks; }
    uint32_t get_vc_per_vnet()   { return m_vc_per_vnet; }
    int get_num_inports()   { return m_input_unit.size(); }
    int get_num_outports()  { return m_output_unit.size(); }
    int get_id()            { return m_id; }

    void init_net_ptr(GarnetNetwork* net_ptr)
    {
        m_network_ptr = net_ptr;
    }

    GarnetNetwork* get_net_ptr()                    { return m_network_ptr; }

    InputUnit*
    getInputUnit(unsigned port)
    {
        assert(port < m_input_unit.size());
        return m_input_unit[port].get();
    }

    OutputUnit*
    getOutputUnit(unsigned port)
    {
        assert(port < m_output_unit.size());
        return m_output_unit[port].get();
    }

    int getBitWidth() { return m_bit_width; }

    PortDirection getOutportDirection(int outport) const;
    PortDirection getInportDirection(int inport);

    int route_compute(RouteInfo route, int inport, PortDirection direction);
    void grant_switch(int inport, flit *t_flit);
    void schedule_wakeup(Cycles time);

    std::string getPortDirectionName(PortDirection direction);
    void printFaultVector(std::ostream& out);
    void printAggregateFaultProbability(std::ostream& out);

    void regStats();
    void collateStats();
    void resetStats();

    // For Fault Model:
    bool get_fault_vector(int temperature, float fault_vector[]) {
        return m_network_ptr->fault_model->fault_vector(m_id, temperature,
                                                        fault_vector);
    }
    bool get_aggregate_fault_probability(int temperature,
                                         float *aggregate_fault_prob) {
        return m_network_ptr->fault_model->fault_prob(m_id, temperature,
                                                      aggregate_fault_prob);
    }

    bool is_escape_vc_enabled() const;
    // For Escape Tree Routing
    int escape_route_compute(RouteInfo route, int inport, PortDirection dir) {
        return routingUnit.outportEscapeVC(route, inport, dir);
    }

    // Direction ↔ outport queries for setup
    int outportIndexByDirection(PortDirection dir) const {
        return routingUnit.outportIndex(dir);
    }
    RoutingUnit& getRoutingUnit() { return routingUnit; }
    int neighborIdByOutport(int outport) const;

    // UGAL decision stats (to verify engagement)
    inline void incUGALMin() { m_ugal_min_choices++; }
    inline void incUGALNonMin() { m_ugal_nonmin_choices++; }

    bool functionalRead(Packet *pkt, WriteMask &mask);
    uint32_t functionalWrite(Packet *);

    // --- SPIN (optional) API ---
    bool spin_scheme_enabled() const { return m_network_ptr->isSpinSchemeEnabled(); }
    // Counter state and pointer
    void init_spin_scheme_ptr();
    void set_counter(unsigned input_port, unsigned vc, Counter_state state, unsigned thresh);
    Counter_state get_counter_state() const { return m_counter ? m_counter->state : s_off; }
    void increment_counter_ptr();
    bool check_counter_ptr(unsigned inport, unsigned invc) {
        return m_counter && m_counter->cptr && (m_counter->cptr->input_port == (int)inport) && (m_counter->cptr->vc == (int)invc);
    }
    void check_counter_timeout();
    inline Cycles get_loop_delay() const { return loop_delay; }
    inline void set_loop_delay(Cycles c) { loop_delay = c; }
    int get_counter_inport() const { return m_counter ? m_counter->cptr->input_port : -1; }
    int get_counter_vc() const { return m_counter ? m_counter->cptr->vc : -1; }

    // SPIN queues (owned by Router)
    flitBuffer* getProbeQueuePtr() { return probeQueue.get(); }
    flitBuffer* getMoveQueuePtr() { return moveQueue.get(); }
    flitBuffer* getKillMoveQueuePtr() { return kill_moveQueue.get(); }
    flitBuffer* getCheckProbeQueuePtr() { return check_probeQueue.get(); }

    // Move registry
    void create_move_info_entry(int inport, int vc, int outport);
    void update_move_info_entry(int inport, int vc, int outport);
    void clear_move_registry();
    const std::vector<move_info *>& get_move_registry() const { return move_registry; }
    int get_num_move_registry_entries() const { return move_registry.size(); }
    void invalidate_move_registry_entry(int inport, int outport);
    bool check_outport_entry_in_move_registry(int outport) const;
    void update_move_vc_at_downstream_router(int vc, int outport);
    void invalidate_move_vcs();

    // Path buffer and source id buffer
    void latch_path(flit *f);
    int peek_path_top() const;
    void invalidate_path_buffer();
    void latch_source_id_buffer(int source_id, int move_id);
    void invalidate_source_id_buffer();
    bool check_source_id_buffer(int source_id, int move_id) const;
    bool partial_check_source_id_buffer(int source_id) const;
    void set_move_bit() { m_move = true; }
    void reset_move_bit() { m_move = false; }
    bool get_move_bit() const { return m_move; }
    void set_start_move() { start_move = true; }
    void reset_start_move() { start_move = false; }
    bool get_start_move() const { return start_move; }
    inline void set_kill_move_processed_this_cycle() { kill_move_processed_this_cycle = true; }
    inline void reset_kill_move_processed_this_cycle() { kill_move_processed_this_cycle = false; }
    inline bool get_kill_move_processed_this_cycle() const { return kill_move_processed_this_cycle; }

    // Control flit send/forward helpers
    int send_move_msg(int inport, int vc);
    void send_probe();
    void send_check_probe(int inport, int vc);
    void fork_probes(flit *t_flit, const std::vector<bool> &fork_vector);
    void send_kill_move(int inport);
    void forward_kill_move(flit *kill_move);
    void forward_move(flit *move);
    void forward_check_probe(flit *check_probe);
    void move_complete();

  private:
    Cycles m_latency;
    uint32_t m_virtual_networks, m_vc_per_vnet, m_num_vcs;
    uint32_t m_bit_width;
    GarnetNetwork *m_network_ptr;

    RoutingUnit routingUnit;
    SwitchAllocator switchAllocator;
    CrossbarSwitch crossbarSwitch;

    std::vector<std::shared_ptr<InputUnit>> m_input_unit;
    std::vector<std::shared_ptr<OutputUnit>> m_output_unit;

    // Statistical variables required for power computations
    statistics::Scalar m_buffer_reads;
    statistics::Scalar m_buffer_writes;

    statistics::Scalar m_sw_input_arbiter_activity;
    statistics::Scalar m_sw_output_arbiter_activity;

    statistics::Scalar m_crossbar_activity;

    // UGAL statistics
    statistics::Scalar m_ugal_min_choices;
    statistics::Scalar m_ugal_nonmin_choices;

    // --- SPIN state ---
    struct pointer { unsigned input_port; unsigned vc; unsigned vnet; };
    struct counter { pointer *cptr; unsigned count; Cycles thresh; Counter_state state; ~counter(){ delete cptr; } };
    struct path_buffer { std::queue<int> path; bool valid = false; };
    struct source_id_buffer { int source_id=-1; int move_id=-1; bool valid=false; };

    std::unique_ptr<counter> m_counter;
    std::unique_ptr<path_buffer> m_path_buffer;
    std::unique_ptr<source_id_buffer> m_source_id_buffer;
    bool m_move = false;
    Cycles loop_delay = Cycles(0);
    std::unique_ptr<flitBuffer> probeQueue;
    std::unique_ptr<flitBuffer> moveQueue;
    std::unique_ptr<flitBuffer> kill_moveQueue;
    std::unique_ptr<flitBuffer> check_probeQueue;
    bool kill_move_processed_this_cycle = false;
    bool start_move = false;
    std::vector<move_info*> move_registry;
};

} // namespace garnet
} // namespace ruby
} // namespace gem5

#endif // __MEM_RUBY_NETWORK_GARNET_0_ROUTER_HH__
