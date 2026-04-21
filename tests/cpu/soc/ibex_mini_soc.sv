// Minimal SoC wrapping Ibex + our generated CLINT + our generated PLIC.
//
// Purpose: prove that the HDL emitted by `rdl2arch-riscv` (register
// blocks + logic modules) drives a real RISC-V CPU's interrupt inputs
// correctly. This is the Phase-6.1 scaffold — it elaborates cleanly and
// supports a timer-ISR cocotb test (Phase 6.2).
//
// Layout
// ------
//   * Ibex (ibex_top_tracing) with its obi-flavored instr / data
//     interfaces.
//   * ram_2p (shipped with Ibex), dual-port: port A = data, port B =
//     read-only instruction fetch.
//   * simulator_ctrl (shipped with Ibex) for sim output + halt.
//   * Our generated Clint (AXI4-Lite register block) + ClintLogic
//     (msip/mtip combinational outputs + mtime counter).
//   * Our generated PlicMultictx (AXI4-Lite register block, 22-bit
//     addr, two M-mode contexts — see tests/rdl/plic_multictx.rdl) +
//     PlicMultictxLogic (per-context priority arbiter + claim/complete).
//   * Two `obi_to_axi_lite` bridges (one per MMIO device).
//
// Memory map (32-bit physical address space)
// ------------------------------------------
//   0x0002_0000..0x0002_03FF  simulator_ctrl  (1 kB)
//   0x0010_0000..0x001F_FFFF  RAM             (1 MB, holds .text + .data)
//   0x0200_0000..0x0200_FFFF  CLINT           (64 kB, 16-bit addr)
//   0x0C00_0000..0x0C3F_FFFF  PLIC            (4 MB,  22-bit addr)
//
// Ibex boots at 0x0010_0080 (RAM base + 0x80, the convention used by
// Ibex's own simple_system). Our cocotb test loads the compiled ISR
// image into RAM starting at 0x0010_0000.
//
// Interrupt plumbing (RISC-V privileged-spec M-mode pins)
// -------------------------------------------------------
//   clint_msip_out  -> ibex.irq_software_i     (mip.msip)
//   clint_mtip_out  -> ibex.irq_timer_i        (mip.mtip)
//   plic_intr_out[0]-> ibex.irq_external_i     (mip.meip,  cause 11)
//   plic_intr_out[1]-> ibex.irq_fast_i[0]      (mip.bit16, cause 16)
//
// Ibex is an M-only core, so the second PLIC context has no S-mode
// "SEIP" pin to drive. We repurpose `irq_fast_i[0]` as a stand-in so
// the multi-context arbitration path is still observable end-to-end.
// ISRs dispatch on mcause to differentiate the two contexts.

`ifndef RV32M
  `define RV32M ibex_pkg::RV32MFast
`endif
`ifndef RV32B
  `define RV32B ibex_pkg::RV32BNone
`endif
`ifndef RV32ZC
  `define RV32ZC ibex_pkg::RV32ZcaZcbZcmp
`endif
`ifndef RegFile
  `define RegFile ibex_pkg::RegFileFF
`endif

module ibex_mini_soc
  import ClintPkg::*;
  import PlicMultictxPkg::*;
#(
  // Path to a .vmem file loaded into RAM at elab time via $readmemh.
  // Set by cocotb harness / fusesoc parameter. Empty = no preload.
  parameter string SRAMInitFile = ""
) (
  input  logic IO_CLK,
  input  logic IO_RST_N,

  // External interrupt sources (tied into PLIC.source_in[8:1]).
  // Bit 0 is reserved (SiFive convention: source ID 0 = no-source).
  input  logic [7:0] ext_irq_sources_i,

  // Observability for tests (cocotb reads these).
  output logic       clint_mtip_o,
  output logic       clint_msip_o,
  output logic       plic_meip_o,         // context-0 winner (M-external)
  output logic       plic_ctx1_irq_o,     // context-1 winner (routed to irq_fast[0])
  output logic [31:0] ibex_pc_o
);

  // ── clocks / reset ─────────────────────────────────────────────
  logic clk_sys;
  logic rst_sys_n;
  assign clk_sys   = IO_CLK;
  assign rst_sys_n = IO_RST_N;

  // ── Ibex obi signals ───────────────────────────────────────────
  logic        instr_req;
  logic        instr_gnt;
  logic        instr_rvalid;
  logic [31:0] instr_addr;
  logic [31:0] instr_rdata;
  logic        instr_err;

  logic        data_req;
  logic        data_gnt;
  logic        data_rvalid;
  logic        data_we;
  logic [3:0]  data_be;
  logic [31:0] data_addr;
  logic [31:0] data_wdata;
  logic [31:0] data_rdata;
  logic        data_err;

  // Ibex tracing macros want these tied to something sensible.
  logic [6:0] data_rdata_intg;
  logic [6:0] instr_rdata_intg;
  assign data_rdata_intg  = '0;
  assign instr_rdata_intg = '0;

  // ── interrupt lines ────────────────────────────────────────────
  logic irq_software;
  logic irq_timer;
  logic irq_external;
  logic irq_ctx1_fast;

  assign clint_msip_o     = irq_software;
  assign clint_mtip_o     = irq_timer;
  assign plic_meip_o      = irq_external;
  assign plic_ctx1_irq_o  = irq_ctx1_fast;

  // Debug observability: pull the committed PC out of the tracing wrapper.
  // Wired inside g_tracing below.

  // Instruction fetch is straight-through: always granted, 1-cycle RAM
  // latency. No MMIO fetches.
  assign instr_gnt = instr_req;
  assign instr_err = 1'b0;

  // ── Data-bus address decode ────────────────────────────────────
  localparam logic [31:0] RAM_BASE      = 32'h0010_0000;
  localparam logic [31:0] RAM_MASK      = ~32'h000F_FFFF;  // 1 MB
  localparam logic [31:0] SIMCTRL_BASE  = 32'h0002_0000;
  localparam logic [31:0] SIMCTRL_MASK  = ~32'h0000_03FF;  // 1 kB
  localparam logic [31:0] CLINT_BASE    = 32'h0200_0000;
  localparam logic [31:0] CLINT_MASK    = ~32'h0000_FFFF;  // 64 kB
  localparam logic [31:0] PLIC_BASE     = 32'h0C00_0000;
  localparam logic [31:0] PLIC_MASK     = ~32'h003F_FFFF;  // 4 MB

  logic sel_ram;
  logic sel_simctrl;
  logic sel_clint;
  logic sel_plic;
  assign sel_ram     = ((data_addr & RAM_MASK)     == RAM_BASE);
  assign sel_simctrl = ((data_addr & SIMCTRL_MASK) == SIMCTRL_BASE);
  assign sel_clint   = ((data_addr & CLINT_MASK)   == CLINT_BASE);
  assign sel_plic    = ((data_addr & PLIC_MASK)    == PLIC_BASE);

  // ── RAM (dual-port) ────────────────────────────────────────────
  logic        ram_data_req;
  logic        ram_data_rvalid;
  logic [31:0] ram_data_rdata;

  assign ram_data_req = data_req & sel_ram;

  ram_2p #(
    .Depth        (1024*1024/4),
    .BExtraDelay  (0),
    .MemInitFile  (SRAMInitFile)
  ) u_ram (
    .clk_i       (clk_sys),
    .rst_ni      (rst_sys_n),

    .a_req_i     (ram_data_req),
    .a_we_i      (data_we),
    .a_be_i      (data_be),
    .a_addr_i    (data_addr),
    .a_wdata_i   (data_wdata),
    .a_rvalid_o  (ram_data_rvalid),
    .a_rdata_o   (ram_data_rdata),

    .b_req_i     (instr_req),
    .b_we_i      (1'b0),
    .b_be_i      (4'b0),
    .b_addr_i    (instr_addr),
    .b_wdata_i   (32'b0),
    .b_rvalid_o  (instr_rvalid),
    .b_rdata_o   (instr_rdata)
  );

  // ── simulator_ctrl ─────────────────────────────────────────────
  logic        simctrl_req;
  logic        simctrl_rvalid;
  logic [31:0] simctrl_rdata;
  assign simctrl_req = data_req & sel_simctrl;

  simulator_ctrl #(
    .LogName("ibex_mini_soc.log")
  ) u_simctrl (
    .clk_i     (clk_sys),
    .rst_ni    (rst_sys_n),

    .req_i     (simctrl_req),
    .we_i      (data_we),
    .be_i      (data_be),
    .addr_i    (data_addr),
    .wdata_i   (data_wdata),
    .rvalid_o  (simctrl_rvalid),
    .rdata_o   (simctrl_rdata)
  );

  // ── CLINT: obi -> axi-lite bridge + Clint register block + ClintLogic ──
  logic        clint_obi_req;
  logic        clint_obi_gnt;
  logic        clint_obi_rvalid;
  logic [31:0] clint_obi_rdata;
  logic        clint_obi_err;

  assign clint_obi_req = data_req & sel_clint;

  // AXI-Lite signals between bridge and Clint (16-bit addr).
  logic                clint_aw_valid, clint_aw_ready;
  logic [15:0]         clint_aw_addr;
  logic [2:0]          clint_aw_prot;
  logic                clint_w_valid,  clint_w_ready;
  logic [31:0]         clint_w_data;
  logic [3:0]          clint_w_strb;
  logic                clint_b_valid,  clint_b_ready;
  logic [1:0]          clint_b_resp;
  logic                clint_ar_valid, clint_ar_ready;
  logic [15:0]         clint_ar_addr;
  logic [2:0]          clint_ar_prot;
  logic                clint_r_valid,  clint_r_ready;
  logic [31:0]         clint_r_data;
  logic [1:0]          clint_r_resp;

  obi_to_axi_lite #(.ADDR_W(16)) u_clint_bridge (
    .clk_i       (clk_sys),
    .rst_ni      (rst_sys_n),
    .obi_req_i   (clint_obi_req),
    .obi_gnt_o   (clint_obi_gnt),
    .obi_addr_i  (data_addr),
    .obi_we_i    (data_we),
    .obi_be_i    (data_be),
    .obi_wdata_i (data_wdata),
    .obi_rvalid_o(clint_obi_rvalid),
    .obi_rdata_o (clint_obi_rdata),
    .obi_err_o   (clint_obi_err),

    .aw_valid_o  (clint_aw_valid),
    .aw_ready_i  (clint_aw_ready),
    .aw_addr_o   (clint_aw_addr),
    .aw_prot_o   (clint_aw_prot),
    .w_valid_o   (clint_w_valid),
    .w_ready_i   (clint_w_ready),
    .w_data_o    (clint_w_data),
    .w_strb_o    (clint_w_strb),
    .b_valid_i   (clint_b_valid),
    .b_ready_o   (clint_b_ready),
    .b_resp_i    (clint_b_resp),
    .ar_valid_o  (clint_ar_valid),
    .ar_ready_i  (clint_ar_ready),
    .ar_addr_o   (clint_ar_addr),
    .ar_prot_o   (clint_ar_prot),
    .r_valid_i   (clint_r_valid),
    .r_ready_o   (clint_r_ready),
    .r_data_i    (clint_r_data),
    .r_resp_i    (clint_r_resp)
  );

  ClintHwifIn  clint_hwif_in;
  ClintHwifOut clint_hwif_out;

  Clint u_clint_regblock (
    .clk            (clk_sys),
    .rst            (~rst_sys_n),
    .s_axi_aw_valid (clint_aw_valid),
    .s_axi_aw_ready (clint_aw_ready),
    .s_axi_aw_addr  (clint_aw_addr),
    .s_axi_aw_prot  (clint_aw_prot),
    .s_axi_w_valid  (clint_w_valid),
    .s_axi_w_ready  (clint_w_ready),
    .s_axi_w_data   (clint_w_data),
    .s_axi_w_strb   (clint_w_strb),
    .s_axi_b_valid  (clint_b_valid),
    .s_axi_b_ready  (clint_b_ready),
    .s_axi_b_resp   (clint_b_resp),
    .s_axi_ar_valid (clint_ar_valid),
    .s_axi_ar_ready (clint_ar_ready),
    .s_axi_ar_addr  (clint_ar_addr),
    .s_axi_ar_prot  (clint_ar_prot),
    .s_axi_r_valid  (clint_r_valid),
    .s_axi_r_ready  (clint_r_ready),
    .s_axi_r_data   (clint_r_data),
    .s_axi_r_resp   (clint_r_resp),
    .hwif_in        (clint_hwif_in),
    .hwif_out       (clint_hwif_out)
  );

  ClintLogic u_clint_logic (
    .clk        (clk_sys),
    .rst        (~rst_sys_n),
    .mtime_tick (1'b1),              // tick every cycle — sim-test knob
    .hwif_out   (clint_hwif_out),    // regblock -> logic
    .hwif_in    (clint_hwif_in),     // logic   -> regblock
    .msip_out   (irq_software),
    .mtip_out   (irq_timer)
  );

  // ── PLIC: obi -> axi-lite bridge + Plic regblock + PlicLogic ──────────
  logic        plic_obi_req;
  logic        plic_obi_gnt;
  logic        plic_obi_rvalid;
  logic [31:0] plic_obi_rdata;
  logic        plic_obi_err;

  assign plic_obi_req = data_req & sel_plic;

  logic                plic_aw_valid, plic_aw_ready;
  logic [21:0]         plic_aw_addr;
  logic [2:0]          plic_aw_prot;
  logic                plic_w_valid,  plic_w_ready;
  logic [31:0]         plic_w_data;
  logic [3:0]          plic_w_strb;
  logic                plic_b_valid,  plic_b_ready;
  logic [1:0]          plic_b_resp;
  logic                plic_ar_valid, plic_ar_ready;
  logic [21:0]         plic_ar_addr;
  logic [2:0]          plic_ar_prot;
  logic                plic_r_valid,  plic_r_ready;
  logic [31:0]         plic_r_data;
  logic [1:0]          plic_r_resp;

  obi_to_axi_lite #(.ADDR_W(22)) u_plic_bridge (
    .clk_i       (clk_sys),
    .rst_ni      (rst_sys_n),
    .obi_req_i   (plic_obi_req),
    .obi_gnt_o   (plic_obi_gnt),
    .obi_addr_i  (data_addr),
    .obi_we_i    (data_we),
    .obi_be_i    (data_be),
    .obi_wdata_i (data_wdata),
    .obi_rvalid_o(plic_obi_rvalid),
    .obi_rdata_o (plic_obi_rdata),
    .obi_err_o   (plic_obi_err),

    .aw_valid_o  (plic_aw_valid),
    .aw_ready_i  (plic_aw_ready),
    .aw_addr_o   (plic_aw_addr),
    .aw_prot_o   (plic_aw_prot),
    .w_valid_o   (plic_w_valid),
    .w_ready_i   (plic_w_ready),
    .w_data_o    (plic_w_data),
    .w_strb_o    (plic_w_strb),
    .b_valid_i   (plic_b_valid),
    .b_ready_o   (plic_b_ready),
    .b_resp_i    (plic_b_resp),
    .ar_valid_o  (plic_ar_valid),
    .ar_ready_i  (plic_ar_ready),
    .ar_addr_o   (plic_ar_addr),
    .ar_prot_o   (plic_ar_prot),
    .r_valid_i   (plic_r_valid),
    .r_ready_o   (plic_r_ready),
    .r_data_i    (plic_r_data),
    .r_resp_i    (plic_r_resp)
  );

  PlicMultictxHwifIn   plic_hwif_in;
  PlicMultictxHwifOut  plic_hwif_out;
  logic                plic_claim_0_read_pulse;
  logic                plic_claim_0_write_pulse;
  logic                plic_claim_1_read_pulse;
  logic                plic_claim_1_write_pulse;

  PlicMultictx u_plic_regblock (
    .clk                     (clk_sys),
    .rst                     (~rst_sys_n),
    .s_axi_aw_valid          (plic_aw_valid),
    .s_axi_aw_ready          (plic_aw_ready),
    .s_axi_aw_addr           (plic_aw_addr),
    .s_axi_aw_prot           (plic_aw_prot),
    .s_axi_w_valid           (plic_w_valid),
    .s_axi_w_ready           (plic_w_ready),
    .s_axi_w_data            (plic_w_data),
    .s_axi_w_strb            (plic_w_strb),
    .s_axi_b_valid           (plic_b_valid),
    .s_axi_b_ready           (plic_b_ready),
    .s_axi_b_resp            (plic_b_resp),
    .s_axi_ar_valid          (plic_ar_valid),
    .s_axi_ar_ready          (plic_ar_ready),
    .s_axi_ar_addr           (plic_ar_addr),
    .s_axi_ar_prot           (plic_ar_prot),
    .s_axi_r_valid           (plic_r_valid),
    .s_axi_r_ready           (plic_r_ready),
    .s_axi_r_data            (plic_r_data),
    .s_axi_r_resp            (plic_r_resp),
    .hwif_in                 (plic_hwif_in),
    .hwif_out                (plic_hwif_out),
    .claim_0_read_pulse      (plic_claim_0_read_pulse),
    .claim_0_write_pulse     (plic_claim_0_write_pulse),
    .claim_1_read_pulse      (plic_claim_1_read_pulse),
    .claim_1_write_pulse     (plic_claim_1_write_pulse)
  );

  // PLIC source_in[0] is the reserved "no-source" bit; sources 1..8 are
  // the external IRQ lines we expose at the SoC boundary.
  logic [8:0] plic_source_in;
  assign plic_source_in = {ext_irq_sources_i, 1'b0};

  // One bit per M-mode context. plic_intr_out[0] drives mip.MEIP; we
  // route plic_intr_out[1] into Ibex's irq_fast_i[0] (mip bit 16 /
  // cause 16) — Ibex has no S-mode pin, so this is our stand-in.
  logic [1:0] plic_intr_out;
  assign irq_external   = plic_intr_out[0];
  assign irq_ctx1_fast  = plic_intr_out[1];

  PlicMultictxLogic u_plic_logic (
    .clk                     (clk_sys),
    .rst                     (~rst_sys_n),
    .source_in               (plic_source_in),
    .hwif_out                (plic_hwif_out),
    .hwif_in                 (plic_hwif_in),
    .intr_out                (plic_intr_out),
    .claim_0_read_pulse      (plic_claim_0_read_pulse),
    .claim_0_write_pulse     (plic_claim_0_write_pulse),
    .claim_1_read_pulse      (plic_claim_1_read_pulse),
    .claim_1_write_pulse     (plic_claim_1_write_pulse)
  );

  // ── Data-bus response muxing ───────────────────────────────────
  // Ibex's bus.sv-style contract: gnt is returned combinationally to
  // the host when the selected device can accept; rvalid/rdata arrive
  // one or more cycles later from the same device. For our single-
  // host topology we just or-mux — at most one device ever asserts
  // rvalid per cycle because the decoder is one-hot.
  assign data_gnt =
      (sel_ram     & data_req) |     // RAM: always ready, gnt == req
      (sel_simctrl & data_req) |     // simctrl: always ready
      (sel_clint   & clint_obi_gnt) |
      (sel_plic    & plic_obi_gnt);

  // rvalid / rdata: since the decoder is one-hot at any instant AND
  // the RAM/simctrl/bridge responses all settle one cycle after their
  // own request, the selected device drives rvalid high for one cycle
  // and the mux picks it up.
  //
  // Note: we don't track which device's response is "in flight" — the
  // one-cycle (for RAM/simctrl) and 1-cycle AXI-slave latency (for
  // MMIO bridges) guarantee only one device has rvalid=1 at a time
  // for a given host request.
  logic        simctrl_err;
  assign simctrl_err = 1'b0;

  assign data_rvalid = ram_data_rvalid
                     | simctrl_rvalid
                     | clint_obi_rvalid
                     | plic_obi_rvalid;

  always_comb begin
    if (ram_data_rvalid) begin
      data_rdata = ram_data_rdata;
      data_err   = 1'b0;
    end else if (simctrl_rvalid) begin
      data_rdata = simctrl_rdata;
      data_err   = simctrl_err;
    end else if (clint_obi_rvalid) begin
      data_rdata = clint_obi_rdata;
      data_err   = clint_obi_err;
    end else if (plic_obi_rvalid) begin
      data_rdata = plic_obi_rdata;
      data_err   = plic_obi_err;
    end else begin
      data_rdata = 32'b0;
      data_err   = 1'b0;
    end
  end

  // ── Ibex core ──────────────────────────────────────────────────
  ibex_top_tracing #(
    .DmBaseAddr      (32'h0000_0000),
    .DmAddrMask      (32'h0000_0003),
    .DmHaltAddr      (32'h0000_0000),
    .DmExceptionAddr (32'h0000_0000)
  ) u_ibex (
    .clk_i                     (clk_sys),
    .rst_ni                    (rst_sys_n),

    .test_en_i                 (1'b0),
    .scan_rst_ni               (1'b1),
    .ram_cfg_icache_tag_i      (prim_ram_1p_pkg::RAM_1P_CFG_DEFAULT),
    .ram_cfg_rsp_icache_tag_o  (),
    .ram_cfg_icache_data_i     (prim_ram_1p_pkg::RAM_1P_CFG_DEFAULT),
    .ram_cfg_rsp_icache_data_o (),

    .hart_id_i                 (32'b0),
    .boot_addr_i               (32'h0010_0000),

    .instr_req_o               (instr_req),
    .instr_gnt_i               (instr_gnt),
    .instr_rvalid_i            (instr_rvalid),
    .instr_addr_o              (instr_addr),
    .instr_rdata_i             (instr_rdata),
    .instr_rdata_intg_i        (instr_rdata_intg),
    .instr_err_i               (instr_err),

    .data_req_o                (data_req),
    .data_gnt_i                (data_gnt),
    .data_rvalid_i             (data_rvalid),
    .data_we_o                 (data_we),
    .data_be_o                 (data_be),
    .data_addr_o               (data_addr),
    .data_wdata_o              (data_wdata),
    .data_wdata_intg_o         (),
    .data_rdata_i              (data_rdata),
    .data_rdata_intg_i         (data_rdata_intg),
    .data_err_i                (data_err),

    .irq_software_i            (irq_software),
    .irq_timer_i               (irq_timer),
    .irq_external_i            (irq_external),
    // PLIC context-1 winner piped into fast IRQ 0 (mip bit 16, cause
    // 16). The remaining 14 fast IRQs are tied off — we only model
    // one additional external-flavour source in the fixture.
    .irq_fast_i                ({14'b0, irq_ctx1_fast}),
    .irq_nm_i                  (1'b0),

    .scramble_key_valid_i      ('0),
    .scramble_key_i            ('0),
    .scramble_nonce_i          ('0),
    .scramble_req_o            (),

    .debug_req_i               (1'b0),
    .crash_dump_o              (),
    .double_fault_seen_o       (),

    .fetch_enable_i            (ibex_pkg::IbexMuBiOn),
    .alert_minor_o             (),
    .alert_major_internal_o    (),
    .alert_major_bus_o         (),
    .core_sleep_o              (),

    .lockstep_cmp_en_o         (),

    .data_req_shadow_o         (),
    .data_we_shadow_o          (),
    .data_be_shadow_o          (),
    .data_addr_shadow_o        (),
    .data_wdata_shadow_o       (),
    .data_wdata_intg_shadow_o  (),

    .instr_req_shadow_o        (),
    .instr_addr_shadow_o       ()
  );

  // Pull the committed PC out of the tracing wrapper's RVFI stream for
  // sim observation. Optional — cocotb can also read .u_ibex.u_ibex_top.*
  // hierarchical paths if needed.
  assign ibex_pc_o = u_ibex.rvfi_pc_rdata;

endmodule
