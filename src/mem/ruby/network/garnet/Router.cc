/*
 * Copyright (c) 2020 Advanced Micro Devices, Inc.
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


#include "mem/ruby/network/garnet/Router.hh"

#include "debug/RubyNetwork.hh"
#include "mem/ruby/network/garnet/CreditLink.hh"
#include "mem/ruby/network/garnet/GarnetNetwork.hh"
#include "mem/ruby/network/garnet/InputUnit.hh"
#include "mem/ruby/network/garnet/NetworkLink.hh"
#include "mem/ruby/network/garnet/OutputUnit.hh"
#include "mem/ruby/network/garnet/flitBuffer.hh"

namespace gem5
{

namespace ruby
{

namespace garnet
{

Router::Router(const Params &p)
  : BasicRouter(p), Consumer(this), m_latency(p.latency),
    m_virtual_networks(p.virt_nets), m_vc_per_vnet(p.vcs_per_vnet),
    m_num_vcs(m_virtual_networks * m_vc_per_vnet), m_bit_width(p.width),
    m_network_ptr(nullptr), routingUnit(this), switchAllocator(this),
    crossbarSwitch(this)
{
    m_input_unit.clear();
    m_output_unit.clear();
}

void
Router::init()
{
    BasicRouter::init();

    switchAllocator.init();
    crossbarSwitch.init();

    // SPIN: allocate state if enabled (safe to allocate always)
    init_spin_scheme_ptr();
}

void
Router::wakeup()
{
    DPRINTF(RubyNetwork, "Router %d woke up\n", m_id);
    assert(clockEdge() == curTick());

    // check for incoming flits
    for (int inport = 0; inport < m_input_unit.size(); inport++) {
        m_input_unit[inport]->wakeup();
    }

    // check for incoming credits
    // Note: the credit update is happening before SA
    // buffer turnaround time =
    //     credit traversal (1-cycle) + SA (1-cycle) + Link Traversal (1-cycle)
    // if we want the credit update to take place after SA, this loop should
    // be moved after the SA request
    for (int outport = 0; outport < m_output_unit.size(); outport++) {
        m_output_unit[outport]->wakeup();
    }

    // Reset per-cycle flag for KILL_MOVE processing
    reset_kill_move_processed_this_cycle();

    // SPIN: counter timeout check to trigger probe/move/kill progression
    if (spin_scheme_enabled()) {
        check_counter_timeout();
    }

    // Switch Allocation
    switchAllocator.wakeup();

    // Switch Traversal
    crossbarSwitch.wakeup();
}

void
Router::addInPort(PortDirection inport_dirn,
                  NetworkLink *in_link, CreditLink *credit_link)
{
    fatal_if(in_link->bitWidth != m_bit_width, "Widths of link %s(%d)does"
            " not match that of Router%d(%d). Consider inserting SerDes "
            "Units.", in_link->name(), in_link->bitWidth, m_id, m_bit_width);

    int port_num = m_input_unit.size();
    InputUnit *input_unit = new InputUnit(port_num, inport_dirn, this);

    input_unit->set_in_link(in_link);
    input_unit->set_credit_link(credit_link);
    in_link->setLinkConsumer(this);
    in_link->setVcsPerVnet(get_vc_per_vnet());
    credit_link->setSourceQueue(input_unit->getCreditQueue(), this);
    credit_link->setVcsPerVnet(get_vc_per_vnet());

    m_input_unit.push_back(std::shared_ptr<InputUnit>(input_unit));

    routingUnit.addInDirection(inport_dirn, port_num);
}

void
Router::addOutPort(PortDirection outport_dirn,
                   NetworkLink *out_link,
                   std::vector<NetDest>& routing_table_entry, int link_weight,
                   CreditLink *credit_link, uint32_t consumerVcs)
{
    fatal_if(out_link->bitWidth != m_bit_width, "Widths of units do not match."
            " Consider inserting SerDes Units");

    int port_num = m_output_unit.size();
    OutputUnit *output_unit = new OutputUnit(port_num, outport_dirn, this,
                                             consumerVcs);

    output_unit->set_out_link(out_link);
    output_unit->set_credit_link(credit_link);
    credit_link->setLinkConsumer(this);
    credit_link->setVcsPerVnet(consumerVcs);
    out_link->setSourceQueue(output_unit->getOutQueue(), this);
    out_link->setVcsPerVnet(consumerVcs);

    m_output_unit.push_back(std::shared_ptr<OutputUnit>(output_unit));

    routingUnit.addRoute(routing_table_entry);
    routingUnit.addWeight(link_weight);
    routingUnit.addOutDirection(outport_dirn, port_num);
}

PortDirection
Router::getOutportDirection(int outport) const
{
    return m_output_unit[outport]->get_direction();
}

PortDirection
Router::getInportDirection(int inport)
{
    return m_input_unit[inport]->get_direction();
}

int
Router::route_compute(RouteInfo route, int inport, PortDirection inport_dirn)
{
    return routingUnit.outportCompute(route, inport, inport_dirn);
}

void
Router::grant_switch(int inport, flit *t_flit)
{
    crossbarSwitch.update_sw_winner(inport, t_flit);
}

void
Router::schedule_wakeup(Cycles time)
{
    // wake up after time cycles
    scheduleEvent(time);
}

std::string
Router::getPortDirectionName(PortDirection direction)
{
    // PortDirection is actually a string
    // If not, then this function should add a switch
    // statement to convert direction to a string
    // that can be printed out
    return direction;
}

bool
Router::is_escape_vc_enabled() const
{
    return m_network_ptr->isEscapeVcEnabled();
}

int
Router::neighborIdByOutport(int outport) const
{
    auto dir = getOutportDirection(outport);
    if (dir == "Local") return -1;
    return m_output_unit[outport]->getDestRouterId();
}

void
Router::regStats()
{
    BasicRouter::regStats();

    m_buffer_reads
        .name(name() + ".buffer_reads")
        .flags(statistics::nozero)
    ;

    m_buffer_writes
        .name(name() + ".buffer_writes")
        .flags(statistics::nozero)
    ;

    m_crossbar_activity
        .name(name() + ".crossbar_activity")
        .flags(statistics::nozero)
    ;

    m_sw_input_arbiter_activity
        .name(name() + ".sw_input_arbiter_activity")
        .flags(statistics::nozero)
    ;

    m_sw_output_arbiter_activity
        .name(name() + ".sw_output_arbiter_activity")
        .flags(statistics::nozero)
    ;

    // UGAL stats
    m_ugal_min_choices
        .name(name() + ".ugal_min_choices")
        .flags(statistics::nozero)
    ;
    m_ugal_nonmin_choices
        .name(name() + ".ugal_nonmin_choices")
        .flags(statistics::nozero)
    ;
}

void
Router::collateStats()
{
    for (int j = 0; j < m_virtual_networks; j++) {
        for (int i = 0; i < m_input_unit.size(); i++) {
            m_buffer_reads += m_input_unit[i]->get_buf_read_activity(j);
            m_buffer_writes += m_input_unit[i]->get_buf_write_activity(j);
        }
    }

    m_sw_input_arbiter_activity = switchAllocator.get_input_arbiter_activity();
    m_sw_output_arbiter_activity =
        switchAllocator.get_output_arbiter_activity();
    m_crossbar_activity = crossbarSwitch.get_crossbar_activity();
}

void
Router::resetStats()
{
    for (int i = 0; i < m_input_unit.size(); i++) {
            m_input_unit[i]->resetStats();
    }

    crossbarSwitch.resetStats();
    switchAllocator.resetStats();
}

void
Router::printFaultVector(std::ostream& out)
{
    int temperature_celcius = BASELINE_TEMPERATURE_CELCIUS;
    int num_fault_types = m_network_ptr->fault_model->number_of_fault_types;
    float fault_vector[num_fault_types];
    get_fault_vector(temperature_celcius, fault_vector);
    out << "Router-" << m_id << " fault vector: " << std::endl;
    for (int fault_type_index = 0; fault_type_index < num_fault_types;
         fault_type_index++) {
        out << " - probability of (";
        out <<
        m_network_ptr->fault_model->fault_type_to_string(fault_type_index);
        out << ") = ";
        out << fault_vector[fault_type_index] << std::endl;
    }
}

void
Router::printAggregateFaultProbability(std::ostream& out)
{
    int temperature_celcius = BASELINE_TEMPERATURE_CELCIUS;
    float aggregate_fault_prob;
    get_aggregate_fault_probability(temperature_celcius,
                                    &aggregate_fault_prob);
    out << "Router-" << m_id << " fault probability: ";
    out << aggregate_fault_prob << std::endl;
}

bool
Router::functionalRead(Packet *pkt, WriteMask &mask)
{
    bool read = false;
    if (crossbarSwitch.functionalRead(pkt, mask))
        read = true;

    for (uint32_t i = 0; i < m_input_unit.size(); i++) {
        if (m_input_unit[i]->functionalRead(pkt, mask))
            read = true;
    }

    for (uint32_t i = 0; i < m_output_unit.size(); i++) {
        if (m_output_unit[i]->functionalRead(pkt, mask))
            read = true;
    }

    return read;
}

uint32_t
Router::functionalWrite(Packet *pkt)
{
    uint32_t num_functional_writes = 0;
    num_functional_writes += crossbarSwitch.functionalWrite(pkt);

    for (uint32_t i = 0; i < m_input_unit.size(); i++) {
        num_functional_writes += m_input_unit[i]->functionalWrite(pkt);
    }

    for (uint32_t i = 0; i < m_output_unit.size(); i++) {
        num_functional_writes += m_output_unit[i]->functionalWrite(pkt);
    }

    return num_functional_writes;
}

// ---------------- SPIN implementation (subset) ----------------
void Router::init_spin_scheme_ptr()
{
    m_counter = std::make_unique<counter>();
    m_counter->count = 0;
    m_counter->thresh = Cycles(0);
    m_counter->state = s_off;
    m_counter->cptr = new pointer();

    m_path_buffer = std::make_unique<path_buffer>();
    while (!m_path_buffer->path.empty()) m_path_buffer->path.pop();
    m_path_buffer->valid = false;

    m_source_id_buffer = std::make_unique<source_id_buffer>();
    m_source_id_buffer->valid = false;

    probeQueue = std::make_unique<flitBuffer>();
    moveQueue = std::make_unique<flitBuffer>();
    kill_moveQueue = std::make_unique<flitBuffer>();
    check_probeQueue = std::make_unique<flitBuffer>();
}

void Router::set_counter(unsigned input_port, unsigned vc, Counter_state state, unsigned thresh)
{
    m_counter->cptr->input_port = input_port;
    unsigned vnet = vc / m_vc_per_vnet;
    m_counter->cptr->vnet = vnet;
    m_counter->cptr->vc = vc;
    m_counter->state = state;
    m_counter->count = 0;

    switch (state) {
        case s_move:
        case s_check_probe:
        case s_forward_progress:
            m_counter->thresh = curCycle() + get_loop_delay();
            break;
        case s_frozen:
            m_counter->thresh = curCycle() + Cycles(thresh);
            break;
        case s_deadlock_detection:
            m_counter->thresh = curCycle() + Cycles(m_network_ptr->getSpinDdThreshold());
            break;
        default:
            m_counter->thresh = Cycles(INFINITE_);
            break;
    }
    if (state != s_off) {
        assert((m_counter->thresh - curCycle()) > 0);
        schedule_wakeup(Cycles(m_counter->thresh - curCycle()));
    }
}

void Router::increment_counter_ptr()
{
    if (!m_counter) return;
    unsigned cur_inp = m_counter->cptr->input_port;
    unsigned cur_vc = m_counter->cptr->vc;

    // Try remaining VCs on current inport
    for (unsigned i = cur_vc + 1; i < m_num_vcs; ++i) {
        int t_outport = getInputUnit(cur_inp)->get_outport(i);
        if (getInputUnit(cur_inp)->get_vc_state(i) == ACTIVE_ && getOutportDirection(t_outport) != "Local") {
            set_counter(cur_inp, i, s_deadlock_detection, 0);
            return;
        }
    }
    // Next inports
    for (unsigned ip = cur_inp + 1; ip < m_input_unit.size(); ++ip) {
        if (getInportDirection(ip) == "Local") continue;
        for (unsigned j = 0; j < m_num_vcs; ++j) {
            int t_outport = getInputUnit(ip)->get_outport(j);
            if (getInputUnit(ip)->get_vc_state(j) == ACTIVE_ && getOutportDirection(t_outport) != "Local") {
                set_counter(ip, j, s_deadlock_detection, 0);
                return;
            }
        }
    }
    // Wrap around
    for (unsigned ip = 0; ip <= cur_inp; ++ip) {
        if (getInportDirection(ip) == "Local") continue;
        for (unsigned j = 0; j < m_num_vcs; ++j) {
            int t_outport = getInputUnit(ip)->get_outport(j);
            if (getInputUnit(ip)->get_vc_state(j) == ACTIVE_ && getOutportDirection(t_outport) != "Local") {
                set_counter(ip, j, s_deadlock_detection, 0);
                return;
            }
        }
    }
    // No candidate
    set_counter(cur_inp, cur_vc, s_off, 0);
}

void Router::check_counter_timeout()
{
    if (!m_counter || m_counter->state == s_off) return;
    if (curCycle() < m_counter->thresh) return;
    switch (m_counter->state) {
        case s_deadlock_detection:
            send_probe();
            increment_counter_ptr();
            break;
        case s_move:
            send_kill_move(m_counter->cptr->input_port);
            invalidate_path_buffer();
            invalidate_source_id_buffer();
            clear_move_registry();
            increment_counter_ptr();
            break;
        case s_frozen:
            if (get_move_bit()) set_start_move();
            break;
        case s_forward_progress:
            if (get_move_bit()) set_start_move();
            break;
        case s_check_probe:
            send_kill_move(m_counter->cptr->input_port);
            invalidate_path_buffer();
            invalidate_source_id_buffer();
            clear_move_registry();
            increment_counter_ptr();
            break;
        default:
            break;
    }
}

void Router::latch_path(flit *f)
{
    m_path_buffer->path = f->get_path();
    m_path_buffer->valid = true;
}

int Router::peek_path_top() const
{
    assert(m_path_buffer->valid);
    return m_path_buffer->path.empty() ? -1 : m_path_buffer->path.front();
}

void Router::invalidate_path_buffer()
{
    m_path_buffer->valid = false;
    while (!m_path_buffer->path.empty()) m_path_buffer->path.pop();
}

void Router::latch_source_id_buffer(int source_id, int move_id)
{
    m_source_id_buffer->source_id = source_id;
    m_source_id_buffer->move_id = move_id;
    m_source_id_buffer->valid = true;
}

void Router::invalidate_source_id_buffer()
{
    m_source_id_buffer->source_id = -1;
    m_source_id_buffer->move_id = -1;
    m_source_id_buffer->valid = false;
}

bool Router::check_source_id_buffer(int source_id, int move_id) const
{
    if (!m_source_id_buffer->valid) return false;
    return (m_source_id_buffer->source_id == source_id && m_source_id_buffer->move_id == move_id);
}

bool Router::partial_check_source_id_buffer(int source_id) const
{
    if (!m_source_id_buffer->valid) return false;
    return (m_source_id_buffer->source_id == source_id);
}

int Router::send_move_msg(int inport, int vc)
{
    int vnet = vc / m_vc_per_vnet;
    // Build move flit from path buffer (output port sequence)
    flit *move = new flit(get_id(), inport, vc, vnet, MOVE_,
                          clockEdge(Cycles(1)) - Cycles(1), m_path_buffer->path);
    // two-loop delay in ticks (approximate): use router latency cycles
    Tick ld = clockEdge(get_loop_delay()) - clockEdge(Cycles(0));
    move->addDelay(ld);
    move->addDelay(ld);
    // Subtract current router latency
    move->subDelay(clockEdge(m_latency) - clockEdge(Cycles(0)));
    moveQueue->insert(move);
    if (m_latency > 1)
        schedule_wakeup(Cycles(m_latency - Cycles(1)));
    return move->get_id();
}

void Router::send_probe()
{
    // Build a probe for the currently pointed VC with dependency path comprising its outport
    int inport = m_counter->cptr->input_port;
    int vc = m_counter->cptr->vc;
    int vnet = vc / m_vc_per_vnet;
    std::queue<int> p;
    p.push(getInputUnit(inport)->get_outport(vc));
    flit *probe = new flit(get_id(), inport, vc, vnet, PROBE_,
                           clockEdge(Cycles(1)) - Cycles(1), p);
    Tick ld = clockEdge(get_loop_delay()) - clockEdge(Cycles(0));
    probe->addDelay(ld);
    probe->addDelay(ld);
    probe->subDelay(clockEdge(m_latency) - clockEdge(Cycles(0)));
    probeQueue->insert(probe);
    if (m_latency > 1)
        schedule_wakeup(Cycles(m_latency - Cycles(1)));
}

void Router::send_check_probe(int inport, int vc)
{
    int vnet = vc / m_vc_per_vnet;
    // Build check-probe with current path buffer; outport set by constructor via path head
    flit *cp = new flit(get_id(), inport, vc, vnet, CHECK_PROBE_, clockEdge(Cycles(1)) - Cycles(1), m_path_buffer->path);
    Tick ld = clockEdge(get_loop_delay()) - clockEdge(Cycles(0));
    cp->addDelay(ld);
    cp->addDelay(ld);
    cp->subDelay(clockEdge(m_latency) - clockEdge(Cycles(0)));
    check_probeQueue->insert(cp);
    if (m_latency > 1)
        schedule_wakeup(Cycles(m_latency - Cycles(1)));
}

void Router::fork_probes(flit *t_flit, const std::vector<bool> &fork_vector)
{
    int vnet = t_flit->get_vnet();
    for (int op = 0; op < (int)fork_vector.size(); ++op) {
        if (!fork_vector[op]) continue;
        std::queue<int> path = t_flit->get_path();
        path.push(op);
        flit *p = new flit(t_flit->getSourceId(), t_flit->getInport(), t_flit->getSourceVc(), vnet,
                           PROBE_, clockEdge(Cycles(1)) - Cycles(1), path);
        p->addDelay(t_flit->getDelay());
    p->subDelay(clockEdge(m_latency) - clockEdge(Cycles(0)));
        probeQueue->insert(p);
    }
}

void Router::send_kill_move(int inport)
{
    flit *kill = new flit(get_id(), m_path_buffer->path, clockEdge(Cycles(1)) - Cycles(1), inport);
    kill->setMustSend(true);
    kill_moveQueue->insert(kill);
    if (m_latency > 1) schedule_wakeup(Cycles(m_latency - Cycles(1)));
}

void Router::forward_kill_move(flit *kill_move)
{
    int outport = kill_move->getPathTop();
    kill_move->set_outport(outport);
    kill_move->set_time(clockEdge(Cycles(1)) - Cycles(1));
    kill_moveQueue->insert(kill_move);
    if (m_latency > 1) schedule_wakeup(Cycles(m_latency - Cycles(1)));
}

void Router::forward_move(flit *mv)
{
    mv->subDelay(clockEdge(m_latency) - clockEdge(Cycles(0)));
    int outport = mv->getPathTop();
    mv->set_outport(outport);
    mv->set_time(clockEdge(Cycles(1)) - Cycles(1));
    moveQueue->insert(mv);
    if (m_latency > 1) schedule_wakeup(Cycles(m_latency - Cycles(1)));
}

void Router::forward_check_probe(flit *cp)
{
    cp->subDelay(clockEdge(m_latency) - clockEdge(Cycles(0)));
    int outport = cp->getPathTop();
    cp->set_outport(outport);
    cp->set_time(clockEdge(Cycles(1)) - Cycles(1));
    check_probeQueue->insert(cp);
    if (m_latency > 1) schedule_wakeup(Cycles(m_latency - Cycles(1)));
}

void Router::create_move_info_entry(int inport, int vc, int outport)
{
    auto *mi = new move_info();
    mi->inport = inport;
    mi->vc = vc;
    mi->outport = outport;
    mi->vc_at_downstream_router = -1;
    mi->tail_moved = false;
    mi->cur_move_count = 0;
    move_registry.push_back(mi);
    getInputUnit(inport)->freeze_vc(vc);
}

void Router::update_move_info_entry(int inport, int vc, int outport)
{
    for (auto *mi : move_registry) {
        if (mi->outport == outport) {
            getInputUnit(inport)->thaw_vc(mi->vc);
            mi->vc = vc;
            getInputUnit(inport)->freeze_vc(vc);
            return;
        }
    }
}

void Router::invalidate_move_registry_entry(int inport, int outport)
{
    for (auto it = move_registry.begin(); it != move_registry.end(); ++it) {
        if ((*it)->outport == outport) {
            getInputUnit(inport)->thaw_vc((*it)->vc);
            delete *it;
            move_registry.erase(it);
            return;
        }
    }
}

bool Router::check_outport_entry_in_move_registry(int outport) const
{
    for (auto *mi : move_registry) if (mi->outport == outport) return true;
    return false;
}

void Router::update_move_vc_at_downstream_router(int vc, int outport)
{
    for (auto *mi : move_registry) {
        if (mi->outport == outport) { mi->vc_at_downstream_router = vc; return; }
    }
}

void Router::invalidate_move_vcs()
{
    for (auto *mi : move_registry) {
        mi->vc_at_downstream_router = -1;
        mi->tail_moved = false;
        mi->cur_move_count = 0;
    }
}

void Router::clear_move_registry()
{
    for (auto *mi : move_registry) {
        getInputUnit(mi->inport)->thaw_vc(mi->vc);
        delete mi;
    }
    move_registry.clear();
}

void Router::move_complete()
{
    reset_start_move();
    reset_move_bit();
    if (get_counter_state() == s_forward_progress) {
        assert(move_registry.size() == 1);
        assert(move_registry[0]->inport == (int)m_counter->cptr->input_port);
        assert(move_registry[0]->vc == (int)m_counter->cptr->vc);
        // After a complete move along the cycle, initiate check-probe to pivot cross-overs
        send_check_probe(m_counter->cptr->input_port, m_counter->cptr->vc);
        set_counter(m_counter->cptr->input_port, m_counter->cptr->vc, s_check_probe, 0);
        clear_move_registry();
        create_move_info_entry(m_counter->cptr->input_port, m_counter->cptr->vc, peek_path_top());
    } else {
        invalidate_move_vcs();
    }
}

} // namespace garnet
} // namespace ruby
} // namespace gem5
