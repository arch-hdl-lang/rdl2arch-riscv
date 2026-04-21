// OBI (Ibex-style) <-> AXI4-Lite bridge.
//
// Single-transaction-in-flight, zero-state: reuses the AXI-Lite slave's
// own `aw_ready`/`ar_ready` to backpressure OBI's `gnt` line. Our
// generated register blocks (rdl2arch) implement those ready signals as
// `!bresp_valid_r` / `!rresp_valid_r` — high exactly when no response
// is outstanding — so if Ibex retries every cycle until gnt, we never
// drop a transaction.
//
// OBI timing (from the Ibex reference, matching `shared/rtl/bus.sv`'s
// assumptions):
//   * Cycle T:   host asserts `obi_req_i` + addr/we/be/wdata. When
//                `obi_gnt_o` is high on the same cycle, the slave
//                captures the txn and the host moves on.
//   * Cycle T+1: host ignores its own req signal. Response eventually
//                arrives as `obi_rvalid_o`/`obi_rdata_o` (for reads) /
//                just `obi_rvalid_o` (for writes). With our AXI slave
//                it's always exactly T+1.
//
// Accordingly:
//   * Writes: `obi_req && obi_we` drives both `aw_valid` and `w_valid`
//     high simultaneously. The slave's `aw_ready` tracks `w_ready` so
//     we gate gnt on both.
//   * Reads:  `obi_req && !obi_we` drives `ar_valid` high. gnt waits
//     on `ar_ready`.
//   * `b_ready`/`r_ready` are tied high — we don't stall responses.
//   * `obi_rvalid = b_valid | r_valid` captures either channel.

module obi_to_axi_lite #(
  parameter int unsigned ADDR_W = 22
) (
  input  logic                 clk_i,
  input  logic                 rst_ni,

  // OBI slave side (facing Ibex / soc_bus).
  input  logic                 obi_req_i,
  output logic                 obi_gnt_o,
  input  logic [31:0]          obi_addr_i,
  input  logic                 obi_we_i,
  input  logic [3:0]           obi_be_i,
  input  logic [31:0]          obi_wdata_i,
  output logic                 obi_rvalid_o,
  output logic [31:0]          obi_rdata_o,
  output logic                 obi_err_o,

  // AXI4-Lite master side (facing our generated register block).
  output logic                 aw_valid_o,
  input  logic                 aw_ready_i,
  output logic [ADDR_W-1:0]    aw_addr_o,
  output logic [2:0]           aw_prot_o,

  output logic                 w_valid_o,
  input  logic                 w_ready_i,
  output logic [31:0]          w_data_o,
  output logic [3:0]           w_strb_o,

  input  logic                 b_valid_i,
  output logic                 b_ready_o,
  input  logic [1:0]           b_resp_i,

  output logic                 ar_valid_o,
  input  logic                 ar_ready_i,
  output logic [ADDR_W-1:0]    ar_addr_o,
  output logic [2:0]           ar_prot_o,

  input  logic                 r_valid_i,
  output logic                 r_ready_o,
  input  logic [31:0]          r_data_i,
  input  logic [1:0]           r_resp_i
);

  // Clock and reset are unused in this zero-state bridge, but we carry
  // them through so future pipelined variants can drop in.
  logic unused_clk;
  logic unused_rst;
  assign unused_clk = clk_i;
  assign unused_rst = rst_ni;

  // Writes drive aw + w in lockstep; reads drive ar. No latching.
  assign aw_valid_o = obi_req_i & obi_we_i;
  assign w_valid_o  = obi_req_i & obi_we_i;
  assign aw_addr_o  = obi_addr_i[ADDR_W-1:0];
  assign aw_prot_o  = 3'b000;
  assign w_data_o   = obi_wdata_i;
  assign w_strb_o   = obi_be_i;

  assign ar_valid_o = obi_req_i & ~obi_we_i;
  assign ar_addr_o  = obi_addr_i[ADDR_W-1:0];
  assign ar_prot_o  = 3'b000;

  assign b_ready_o = 1'b1;
  assign r_ready_o = 1'b1;

  // gnt asserts only when the slave can actually accept on the
  // direction the host wants. Ibex holds obi_req until it sees gnt, so
  // a stalled cycle just becomes one extra clock of wait.
  assign obi_gnt_o = obi_req_i
                   & (obi_we_i ? (aw_ready_i & w_ready_i) : ar_ready_i);

  // Either channel's response becomes the OBI response. Writes don't
  // carry data, so rdata is only meaningful when r_valid is high.
  assign obi_rvalid_o = b_valid_i | r_valid_i;
  assign obi_rdata_o  = r_data_i;
  assign obi_err_o    = (b_valid_i & (|b_resp_i))
                      | (r_valid_i & (|r_resp_i));

endmodule
